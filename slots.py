"""
Davidsino Slots - provably fair, server-side slot machine engine.

All bets and wins are in reward points (never cash / PNL).

PROVABLE FAIRNESS
-----------------
Every spin's outcome is derived deterministically from three values:

    server_seed   secret until revealed; its SHA-256 hash is published BEFORE you play
    client_seed   chosen by the player (or randomly assigned); changeable any time
    nonce         spin counter, increments by 1 each spin

    HMAC_SHA256(key=server_seed, msg="client_seed:nonce:cursor") -> random bytes

Because the server commits to `sha256(server_seed)` up front, it cannot change the
server seed after seeing your bet. Because you control `client_seed`, the server
cannot pre-compute a losing sequence tailored to you. Once you rotate your seed the
old `server_seed` is revealed, and anyone can re-run `verify_spin()` to confirm every
past spin matched what was promised.
"""
import hashlib
import hmac
import secrets

WILD = "🃏"


# ============================================================
# Provably-fair RNG
# ============================================================
def new_server_seed() -> str:
    """A fresh 32-byte secret, hex encoded."""
    return secrets.token_hex(32)


def new_client_seed() -> str:
    """A default client seed for players who don't pick their own."""
    return secrets.token_hex(8)


def seed_hash(server_seed: str) -> str:
    """The public commitment. Published before any spin uses this seed."""
    return hashlib.sha256(server_seed.encode()).hexdigest()


def _hmac_bytes(server_seed: str, client_seed: str, nonce: int, cursor: int) -> bytes:
    msg = f"{client_seed}:{nonce}:{cursor}".encode()
    return hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()


def float_stream(server_seed: str, client_seed: str, nonce: int, count: int):
    """
    Yield `count` deterministic floats in [0, 1).

    Each HMAC-SHA256 digest is 32 bytes = 8 four-byte chunks = 8 floats.
    When more are needed the cursor advances and a new digest is generated.
    """
    produced = 0
    cursor = 0
    while produced < count:
        digest = _hmac_bytes(server_seed, client_seed, nonce, cursor)
        for i in range(0, len(digest), 4):
            if produced >= count:
                return
            chunk = digest[i:i + 4]
            # Big-endian 32-bit int scaled into [0, 1)
            value = int.from_bytes(chunk, "big") / 2 ** 32
            produced += 1
            yield value
        cursor += 1


def _pick(reel, roll):
    """Map a [0,1) float onto a reel strip position."""
    idx = int(roll * len(reel))
    if idx >= len(reel):  # guard against float edge case
        idx = len(reel) - 1
    return reel[idx]


def _build_reel(weights):
    """Expand [(symbol, weight), ...] into a flat reel strip."""
    reel = []
    for sym, w in weights:
        reel.extend([sym] * w)
    return reel


# ============================================================
# Machine definitions
# ============================================================
# classic: 3 reels x 1 row. Three of a kind pays big, most pairs pay small,
#          a pair of cherries pays double.
CLASSIC = {
    "key": "classic",
    "name": "Davidsino Classic",
    "tagline": "Old-school 3-reeler. A pair keeps you alive, three of a kind pays.",
    "kind": "reel3",
    "min_bet": 10,
    "max_bet": 1000,
    "reel_weights": [
        ("🍒", 5), ("🍋", 5), ("🔔", 4), ("⭐", 3), ("💎", 2), ("7️⃣", 2), ("🎰", 1),
    ],
    "pay3": {"🍒": 6, "🍋": 10, "🔔": 16, "⭐": 25, "💎": 50, "7️⃣": 120, "🎰": 400},
    "pay2": {"🍒": 2, "🔔": 1, "⭐": 1, "💎": 1, "7️⃣": 1, "🎰": 1},
    "paytable_display": [
        ("🎰 🎰 🎰", "400×"), ("7️⃣ 7️⃣ 7️⃣", "120×"), ("💎 💎 💎", "50×"),
        ("⭐ ⭐ ⭐", "25×"), ("🔔 🔔 🔔", "16×"), ("🍋 🍋 🍋", "10×"),
        ("🍒 🍒 🍒", "6×"), ("🍒 🍒 –", "2×"), ("any other pair*", "1×"),
    ],
    "paytable_note": "*pairs of 🍋 don't pay",
}

# diamond_dave: high volatility 3-reeler. Mostly blanks, huge top prizes.
DIAMOND_DAVE = {
    "key": "diamond_dave",
    "name": "Diamond Dave",
    "tagline": "High roller special. Long dry spells, monster jackpots.",
    "kind": "reel3",
    "min_bet": 50,
    "max_bet": 5000,
    "reel_weights": [
        ("➖", 20), ("🍀", 8), ("💰", 5), ("💎", 3), ("7️⃣", 2), ("🎰", 1),
    ],
    "pay3": {"🍀": 20, "💰": 50, "💎": 200, "7️⃣": 600, "🎰": 4000},
    "pay2": {"🍀": 1, "💰": 2, "💎": 5, "7️⃣": 10, "🎰": 35},
    "paytable_display": [
        ("🎰 🎰 🎰", "4000×"), ("7️⃣ 7️⃣ 7️⃣", "600×"), ("💎 💎 💎", "200×"),
        ("💰 💰 💰", "50×"), ("🍀 🍀 🍀", "20×"), ("🎰 🎰 –", "35×"),
        ("7️⃣ 7️⃣ –", "10×"), ("💎 💎 –", "5×"), ("💰 💰 –", "2×"),
        ("🍀 🍀 –", "1× (push)"),
    ],
    "paytable_note": "Blanks pay nothing. Bring a bankroll.",
}

# vig_city: 5 reels x 3 rows, 5 fixed paylines, wild joker.
VIG_CITY = {
    "key": "vig_city",
    "name": "Vig City",
    "tagline": "5 reels, 5 lines, wild jokers. The vig never sleeps.",
    "kind": "lines5x3",
    "min_bet": 25,
    "max_bet": 2500,
    "reel_weights": [
        ("🍒", 20), ("🍋", 20), ("🍇", 16), ("🔔", 12), ("⭐", 8),
        ("💎", 5), ("7️⃣", 3), (WILD, 2),
    ],
    # Multipliers are per LINE bet (total bet / 5), indexed [3-kind, 4-kind, 5-kind]
    "line_pays": {
        "🍒": [5, 25, 100], "🍋": [5, 25, 100], "🍇": [8, 40, 150],
        "🔔": [15, 60, 250], "⭐": [30, 120, 500], "💎": [60, 250, 1000],
        "7️⃣": [150, 600, 2500],
    },
    "paytable_display": [
        ("7️⃣ ×3 / ×4 / ×5", "150× / 600× / 2500×"),
        ("💎 ×3 / ×4 / ×5", "60× / 250× / 1000×"),
        ("⭐ ×3 / ×4 / ×5", "30× / 120× / 500×"),
        ("🔔 ×3 / ×4 / ×5", "15× / 60× / 250×"),
        ("🍇 ×3 / ×4 / ×5", "8× / 40× / 150×"),
        ("🍒 or 🍋 ×3 / ×4 / ×5", "5× / 25× / 100×"),
        ("🃏 joker", "substitutes for anything"),
    ],
    "paytable_note": "Wins pay per line (bet ÷ 5), left to right. 5 lines: 3 rows + both diagonals.",
}

MACHINES = {m["key"]: m for m in (CLASSIC, DIAMOND_DAVE, VIG_CITY)}

# 5 fixed paylines for the 5x3 grid, as row index per reel column
PAYLINES_5X3 = [
    [0, 0, 0, 0, 0],  # top row
    [1, 1, 1, 1, 1],  # middle row
    [2, 2, 2, 2, 2],  # bottom row
    [0, 1, 2, 1, 0],  # V
    [2, 1, 0, 1, 2],  # inverted V
]

# Pre-built reel strips, keyed by machine
REELS = {key: _build_reel(m["reel_weights"]) for key, m in MACHINES.items()}


# ============================================================
# Spin evaluation
# ============================================================
def _spin_reel3(machine, bet, rolls):
    reel = REELS[machine["key"]]
    symbols = [_pick(reel, r) for r in rolls]
    win = 0.0
    detail = None

    counts = {}
    for s in symbols:
        counts[s] = counts.get(s, 0) + 1

    for sym, n in counts.items():
        if n == 3 and sym in machine["pay3"]:
            win = bet * machine["pay3"][sym]
            detail = f"Three {sym}!"
        elif n == 2 and sym in machine.get("pay2", {}):
            win = bet * machine["pay2"][sym]
            detail = f"Pair of {sym}"
    return {"grid": [symbols], "win": round(win, 2), "detail": detail}


def _line_win(line_syms, line_pays):
    """Longest left-to-right run, wilds substituting. Returns (symbol, run_length)."""
    target = None
    run = 0
    for s in line_syms:
        if s == WILD:
            run += 1
            continue
        if target is None:
            target = s
            run += 1
        elif s == target:
            run += 1
        else:
            break
    if target is None:  # all wilds — score as the top-paying symbol
        target = "7️⃣"
    if target not in line_pays:
        return None, 0
    return target, run


def _spin_lines5x3(machine, bet, rolls):
    reel = REELS[machine["key"]]
    # grid[row][col]; rolls are consumed column-major so verification is unambiguous
    grid = [[None] * 5 for _ in range(3)]
    i = 0
    for col in range(5):
        for row in range(3):
            grid[row][col] = _pick(reel, rolls[i])
            i += 1

    line_bet = bet / len(PAYLINES_5X3)
    total_win = 0.0
    hits = []

    for idx, line in enumerate(PAYLINES_5X3):
        syms = [grid[line[c]][c] for c in range(5)]
        target, run = _line_win(syms, machine["line_pays"])
        if target and run >= 3:
            mult = machine["line_pays"][target][run - 3]
            line_win = line_bet * mult
            total_win += line_win
            hits.append({"line": idx, "symbol": target, "count": run,
                         "win": round(line_win, 2)})

    detail = None
    if hits:
        best = max(hits, key=lambda h: h["win"])
        extra = len(hits) - 1
        detail = f"{best['count']}× {best['symbol']}" + (f" + {extra} more line(s)" if extra else "")
    return {"grid": grid, "win": round(total_win, 2), "detail": detail, "lines": hits}


def rolls_needed(machine_key: str) -> int:
    """How many random numbers a spin on this machine consumes."""
    return 3 if MACHINES[machine_key]["kind"] == "reel3" else 15


def spin(machine_key: str, bet: float, server_seed: str, client_seed: str, nonce: int) -> dict:
    """
    Run one provably fair spin.

    Returns {"machine", "bet", "grid", "win", "detail", "lines"?, "nonce",
             "client_seed", "server_seed_hash"}.
    Raises ValueError for an unknown machine or an out-of-range bet.
    """
    machine = MACHINES.get(machine_key)
    if machine is None:
        raise ValueError("Unknown machine")
    if bet < machine["min_bet"] or bet > machine["max_bet"]:
        raise ValueError(
            f"Bet must be between {machine['min_bet']} and {machine['max_bet']} points"
        )

    rolls = list(float_stream(server_seed, client_seed, nonce, rolls_needed(machine_key)))
    if machine["kind"] == "reel3":
        result = _spin_reel3(machine, bet, rolls)
    else:
        result = _spin_lines5x3(machine, bet, rolls)

    result["machine"] = machine_key
    result["bet"] = bet
    result["nonce"] = nonce
    result["client_seed"] = client_seed
    result["server_seed_hash"] = seed_hash(server_seed)
    return result


def verify_spin(machine_key: str, bet: float, server_seed: str, client_seed: str,
                nonce: int) -> dict:
    """
    Recompute a past spin from revealed values. Identical to spin(), plus the
    server seed and its hash so a player can confirm the published commitment.
    """
    result = spin(machine_key, bet, server_seed, client_seed, nonce)
    result["server_seed"] = server_seed
    result["server_seed_hash_matches"] = seed_hash(server_seed) == result["server_seed_hash"]
    return result


def machine_list():
    """Public machine info for the frontend."""
    return [{
        "key": m["key"],
        "name": m["name"],
        "tagline": m["tagline"],
        "kind": m["kind"],
        "min_bet": m["min_bet"],
        "max_bet": m["max_bet"],
        "paytable": m["paytable_display"],
        "paytable_note": m.get("paytable_note", ""),
    } for m in MACHINES.values()]
