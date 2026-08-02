"""
Davidsino Payments - crypto-first deposit requests.

This is a REQUEST LEDGER, not a payment processor. The player says "I want to
deposit $50 in BTC", the app hands back the house address (plus a QR encoding the
exact amount), the player sends it from their own wallet, and the dealer confirms
receipt. Confirmation is what credits cash-in and reward points.

Nothing here custodies funds or touches a card network. Card processors (Stripe,
Square, Apple Pay / Google Pay merchant APIs) prohibit gambling transactions for
unlicensed operators, which is why the flow is person-to-person transfers the
dealer verifies.

Assets are configured entirely by environment variable. An unset address means
that asset simply doesn't appear.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_DOWN

# QR rendering is optional — the app still runs (and shows addresses as text)
# if the dependency is missing.
try:
    import qrcode
    QR_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on deployment env
    QR_AVAILABLE = False


# ============================================================
# Asset catalogue
# ============================================================
# uri_scheme  : BIP-21 style scheme used to build a wallet deep link
# amount_param: query parameter a wallet expects for the amount
# decimals    : how many decimal places to round a converted amount to
# coingecko   : id used for USD price lookup
CRYPTO_ASSETS = {
    "btc": {
        "label": "Bitcoin", "symbol": "BTC", "env": "BTC_ADDRESS",
        "uri_scheme": "bitcoin", "amount_param": "amount", "decimals": 8,
        "coingecko": "bitcoin", "network": "Bitcoin",
    },
    "eth": {
        "label": "Ethereum", "symbol": "ETH", "env": "ETH_ADDRESS",
        "uri_scheme": "ethereum", "amount_param": "value", "decimals": 6,
        "coingecko": "ethereum", "network": "Ethereum (ERC-20)",
    },
    "ltc": {
        "label": "Litecoin", "symbol": "LTC", "env": "LTC_ADDRESS",
        "uri_scheme": "litecoin", "amount_param": "amount", "decimals": 8,
        "coingecko": "litecoin", "network": "Litecoin",
    },
    "sol": {
        "label": "Solana", "symbol": "SOL", "env": "SOL_ADDRESS",
        "uri_scheme": "solana", "amount_param": "amount", "decimals": 6,
        "coingecko": "solana", "network": "Solana",
    },
    "usdt": {
        "label": "Tether", "symbol": "USDT", "env": "USDT_ADDRESS",
        "uri_scheme": None, "amount_param": None, "decimals": 2,
        "coingecko": "tether", "network": os.getenv("USDT_NETWORK", "TRC-20"),
    },
    "usdc": {
        "label": "USD Coin", "symbol": "USDC", "env": "USDC_ADDRESS",
        "uri_scheme": None, "amount_param": None, "decimals": 2,
        "coingecko": "usd-coin", "network": os.getenv("USDC_NETWORK", "ERC-20"),
    },
}

# Cash-app style handles. Apple Pay / Google Pay reach the house through these:
# the player funds the transfer with their Apple/Google wallet inside the app.
HANDLE_ASSETS = {
    "venmo": {
        "label": "Venmo", "env": "VENMO_HANDLE", "prefix": "@",
        "link": "https://venmo.com/u/{handle}",
        "note": "Fund it with Apple Pay or Google Pay inside the Venmo app.",
    },
    "cashapp": {
        "label": "Cash App", "env": "CASHAPP_HANDLE", "prefix": "$",
        "link": "https://cash.app/${handle}",
        "note": "Fund it with Apple Pay or Google Pay inside Cash App.",
    },
    "paypal": {
        "label": "PayPal", "env": "PAYPAL_ME", "prefix": "",
        "link": "https://paypal.me/{handle}",
        "note": "Friends & family only — goods-and-services payments get held.",
    },
    "zelle": {
        "label": "Zelle", "env": "ZELLE_HANDLE", "prefix": "",
        "link": "", "note": "Send from your bank app to this address.",
    },
}


def _clean(value, strip_chars=""):
    value = (value or "").strip()
    return value.lstrip(strip_chars) if strip_chars else value


def available_methods():
    """
    Every method the operator has configured, crypto first.
    Re-read from the environment each call so a restart isn't needed after
    changing an address in a .env file that gets reloaded.
    """
    methods = []
    for key, asset in CRYPTO_ASSETS.items():
        address = _clean(os.getenv(asset["env"]))
        if not address:
            continue
        methods.append({
            "key": key,
            "kind": "crypto",
            "label": asset["label"],
            "symbol": asset["symbol"],
            "network": asset["network"],
            "address": address,
            "decimals": asset["decimals"],
            "supports_uri": asset["uri_scheme"] is not None,
        })
    for key, handle_asset in HANDLE_ASSETS.items():
        handle = _clean(os.getenv(handle_asset["env"]), "@$")
        if not handle:
            continue
        methods.append({
            "key": key,
            "kind": "handle",
            "label": handle_asset["label"],
            "handle": handle_asset["prefix"] + handle,
            "link": handle_asset["link"].format(handle=handle) if handle_asset["link"] else "",
            "note": handle_asset["note"],
        })
    return methods


def get_method(key):
    """Look up one configured method, or None if it isn't set up."""
    for method in available_methods():
        if method["key"] == key:
            return method
    return None


# ============================================================
# USD -> crypto conversion
# ============================================================
_price_cache = {}          # coingecko_id -> (timestamp, usd_price)
PRICE_TTL = 120            # seconds
PRICE_TIMEOUT = 4          # seconds; a slow feed must never hang a deposit


def usd_price(asset_key):
    """
    Spot USD price for an asset, or None when unavailable.

    A missing price is not an error: the deposit still works, the player is just
    told to send "$50 worth" instead of an exact coin amount. Set
    PRICE_FEED_DISABLED=1 for a fully offline deployment.
    """
    asset = CRYPTO_ASSETS.get(asset_key)
    if not asset:
        return None
    if os.getenv("PRICE_FEED_DISABLED", "").strip() in ("1", "true", "yes"):
        return None

    coin_id = asset["coingecko"]
    cached = _price_cache.get(coin_id)
    now = time.time()
    if cached and now - cached[0] < PRICE_TTL:
        return cached[1]

    url = ("https://api.coingecko.com/api/v3/simple/price"
           f"?ids={urllib.parse.quote(coin_id)}&vs_currencies=usd")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "davidsino-rewards"})
        with urllib.request.urlopen(req, timeout=PRICE_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        price = float(data[coin_id]["usd"])
        if price <= 0:
            return None
        _price_cache[coin_id] = (now, price)
        return price
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError, OSError):
        # Stale price beats no price if we ever had one.
        return cached[1] if cached else None


def convert_usd(asset_key, usd_amount):
    """USD -> asset amount as a Decimal string, or None if no price is available."""
    price = usd_price(asset_key)
    if not price:
        return None
    decimals = CRYPTO_ASSETS[asset_key]["decimals"]
    quantum = Decimal(1).scaleb(-decimals)
    amount = (Decimal(str(usd_amount)) / Decimal(str(price))).quantize(quantum, ROUND_DOWN)
    if amount <= 0:
        return None
    return format(amount, "f")


# ============================================================
# Payment URIs (what the QR encodes)
# ============================================================
def payment_uri(method, crypto_amount=None, label="Davidsino"):
    """
    Build a wallet deep link.

    BTC/LTC use BIP-21 (bitcoin:<addr>?amount=<btc>). ETH uses the EIP-681 shape
    most wallets accept. Token assets (USDT/USDC) have no portable URI scheme
    across networks, so the bare address is returned and the UI shows it as text.
    """
    if method["kind"] == "handle":
        return method.get("link") or ""

    asset = CRYPTO_ASSETS[method["key"]]
    if not asset["uri_scheme"]:
        return method["address"]

    uri = f"{asset['uri_scheme']}:{method['address']}"
    params = {}
    if crypto_amount:
        params[asset["amount_param"]] = crypto_amount
    if label:
        params["label"] = label
    if params:
        uri += "?" + urllib.parse.urlencode(params)
    return uri


def qr_png(data):
    """Render `data` as a PNG QR code. Returns bytes, or None if unavailable."""
    if not QR_AVAILABLE or not data:
        return None
    import io
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_instructions(method, usd_amount):
    """
    Everything the player needs to actually send the money.
    Returned verbatim to the frontend and stored on the request.
    """
    if method["kind"] == "handle":
        return {
            "kind": "handle",
            "label": method["label"],
            "handle": method["handle"],
            "link": method["link"],
            "note": method["note"],
            "usd_amount": round(usd_amount, 2),
            "uri": method["link"],
            "crypto_amount": None,
            "symbol": None,
            "network": None,
            "address": None,
            "price_usd": None,
        }

    crypto_amount = convert_usd(method["key"], usd_amount)
    price = usd_price(method["key"])
    uri = payment_uri(method, crypto_amount)
    return {
        "kind": "crypto",
        "label": method["label"],
        "symbol": method["symbol"],
        "network": method["network"],
        "address": method["address"],
        "usd_amount": round(usd_amount, 2),
        "crypto_amount": crypto_amount,
        "price_usd": round(price, 2) if price else None,
        "uri": uri,
        "note": (
            f"Send exactly {crypto_amount} {method['symbol']} on the "
            f"{method['network']} network."
            if crypto_amount else
            f"Send ${usd_amount:.2f} worth of {method['symbol']} on the "
            f"{method['network']} network. (Live price unavailable — confirm the "
            f"rate with the dealer.)"
        ),
    }
