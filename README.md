# Davidsino Rewards 🎰

Loyalty and rewards tracking system for The Davidsino.

## Features
- Web-based interface (mobile-friendly)
- RFID/NFC card scanning for quick player lookup
- Admin panel for deposits, losses, and adjustments
- Real-time points calculation
- PNL tracking per player
- **Crypto deposits** — BTC/ETH/LTC/SOL/USDT/USDC with QR codes and live rates
- **Provably fair slots** — three machines players can wager reward points on,
  with cryptographic proof the house isn't rigging results
- **Provably fair table games** — blackjack, baccarat, fan-tan and Mississippi
  Stud, dealt from the same commit/reveal seed as the slots

## Architecture
- **Backend:** FastAPI (Python) + PostgreSQL (SQLite works for local dev)
- **Frontend:** Vanilla HTML/CSS/JS (mobile-responsive)
- **Card Reading:** USB HID readers (keyboard input) + Web NFC (Android Chrome)
- **Slot engine:** `slots.py` — HMAC-SHA256 commit/reveal RNG, no dependencies
- **Table engine:** `tables.py` — same RNG, driving a Fisher-Yates shuffle
- **Payments:** `payments.py` — address book + payment URIs, no custody

## Quick Start

### Option 1: Docker Compose (Recommended)
```bash
cd davidsino-rewards
docker compose up -d
```
App runs at `http://localhost:8000`. Database persists in a Docker volume.

To customize admin pins:
```bash
ADMIN_PIN=9999 WORKER_PIN=4444 docker compose up -d
```

### Option 2: No database server (fastest for local dev)

SQLite needs no Postgres and no Docker:
```bash
pip install -r requirements.txt
DATABASE_URL="sqlite:///./davidsino.db" python3 -m uvicorn main:app --reload --port 8000
```
Everything works except Postgres-specific JSONB indexing. Use Postgres for the
real deployment.

### Option 3: Manual Setup

### 1. Install PostgreSQL
```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable postgresql
sudo systemctl start postgresql
```

### 2. Create Database
```bash
sudo -u postgres psql -c "CREATE USER davidsino WITH PASSWORD 'davidsino_pass';"
sudo -u postgres psql -c "CREATE DATABASE davidsino OWNER davidsino;"
sudo -u postgres psql -c "ALTER USER davidsino CREATEDB;"
```

### 3. Install Python Dependencies
```bash
cd davidsino-rewards
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Configure Environment
```bash
cp .env.example .env
# Edit .env and set your DATABASE_URL and ADMIN_PIN
nano .env
```

### 5. Run the Server
```bash
source venv/bin/activate
python3 main.py
```

Server starts on `http://0.0.0.0:8000`

## Usage

### Player View
1. Tap **Scan Card**
2. Hold RFID card to reader, or tap NFC on Android
3. See points balance and PNL

### Admin View
1. Tap **Admin Mode**
2. Enter your PIN (default: `1234`)
3. **Actions tab:** Deposit/deduct/adjust points
4. **Players tab:** View all players and balances
5. **Register tab:** Add new players

## Crypto Deposits

Players request a deposit in the app, send funds from their own wallet, and a
dealer confirms receipt — which credits cash-in and **100 reward points per $1**.

### Setup

Paste your receive addresses into `.env` (see `.env.example`). Anything you leave
blank simply doesn't appear in the app:

| Variable | Asset | Notes |
|---|---|---|
| `BTC_ADDRESS` | Bitcoin | BIP-21 QR with exact amount |
| `ETH_ADDRESS` | Ethereum | EIP-681 QR |
| `LTC_ADDRESS` | Litecoin | BIP-21 QR |
| `SOL_ADDRESS` | Solana | `solana:` URI |
| `USDT_ADDRESS` | Tether | set `USDT_NETWORK` (default `TRC-20`) |
| `USDC_ADDRESS` | USD Coin | set `USDC_NETWORK` (default `ERC-20`) |
| `VENMO_HANDLE` | Venmo | Apple Pay / Google Pay fund these |
| `CASHAPP_HANDLE` | Cash App | Apple Pay / Google Pay fund these |
| `PAYPAL_ME` | PayPal | friends & family only |
| `ZELLE_HANDLE` | Zelle | bank-app transfer |

Other knobs: `MAX_DEPOSIT_USD` (default 10000), `PRICE_FEED_DISABLED=1` for an
offline box.

```bash
BTC_ADDRESS=bc1q... VENMO_HANDLE=davidsino docker compose up -d --build
```

### The flow

1. Player taps **Add Funds**, picks an amount and an asset.
2. App shows the address, a QR encoding the **exact** coin amount, and the
   network. USD is converted at the live CoinGecko rate (cached 2 min; if the
   feed is down it falls back to "send $50 worth" and never blocks the deposit).
3. Player sends from their own wallet and optionally pastes the txid.
4. Dealer opens **Dealer Mode → Deposits**, verifies the funds landed, hits
   **Confirm** — points are credited and a `deposit` event is logged with the
   method, txid and rate.

### About Apple Pay / Google Pay

There is deliberately no direct Apple Pay or Google Pay integration. Those run on
card rails, and every major processor (Stripe, Square, PayPal's commercial API)
prohibits gambling transactions for unlicensed operators — an integration would
get the account frozen. Players use Apple/Google Pay as the *funding source*
inside Venmo or Cash App instead, which is the same tap-to-pay experience and
lands the money in your account.

### What this is not

This app never holds funds, generates addresses, or moves money on its own. It's
a ledger of transfers you confirm by hand. Check the legal position on
real-money play where you live before pointing it at the public internet.

## Provably Fair Slots

Three machines, wagering **reward points only** — a spin can never touch a
player's cash P/L.

| Machine | Bet range | RTP | Hit rate | Feel |
|---|---|---|---|---|
| Davidsino Classic | 10 – 1,000 | 93.3% | 33% | steady, small wins |
| Diamond Dave | 50 – 5,000 | 92.8% | 18% | dry spells, 4000× top prize |
| Vig City | 25 – 2,500 | ~94.1% | 21% | 5 reels, 5 paylines, wild jokers |

RTP for the 3-reel machines is computed by exhaustive enumeration, not sampling.
Check any time:

```bash
python3 scripts/rtp_check.py          # RTP, house edge and hit rate per machine
python3 tests/test_slots.py           # 31 engine tests
python3 tests/test_payments.py        # 24 payment tests (fully offline)
```

### How players verify the house isn't cheating

Every spin is derived from three values:

```
HMAC-SHA256(key = server_seed, msg = "client_seed:nonce:cursor")
```

- **server_seed** — secret, but the app shows you `sha256(server_seed)` *before*
  you play. The house is locked in and can't swap it after seeing your bet.
- **client_seed** — you pick it. So the house can't pre-compute a losing run
  aimed at you.
- **nonce** — the spin counter, incrementing 0, 1, 2…

Tap **🔐 Fairness** on any machine to see the committed hash, your client seed
and spin count. Tap **Rotate & Reveal** and the old server seed becomes public —
then recompute any spin, either in the app or independently:

```bash
python3 scripts/verify_spin.py \
    --server-seed <revealed seed> \
    --client-seed <your seed> \
    --hash <hash you were shown before playing> \
    --machine classic --bet 100 --spins 10
```

The script confirms `sha256(revealed) == committed hash`, then prints the grids.
If they match what you saw on screen, the results were fixed before you ever hit
SPIN. It talks to no server — it only needs `slots.py` and Python 3.

## Provably Fair Table Games

Four games, wagering **reward points only** — same rule as the slots, a hand can
never touch a player's cash P/L.

| Game | Bet range | House edge | Notes |
|---|---|---|---|
| Blackjack | 25 – 5,000 | **0.64%** measured under basic strategy | 6 decks, dealer **hits soft 17**, BJ pays 3:2 |
| Baccarat | 25 – 5,000 | 1.06% banker / 1.24% player / 14.36% tie | 8 decks, standard punto banco |
| Fan-Tan | 25 – 5,000 | 1.25% – 3.75% depending on the bet | beads counted in fours, 5% on winnings |
| Mississippi Stud | 25 – 1,000 ante | ~4.9% of ante at optimal play | 3 streets, raise 1×–3× or fold |

Every figure above is checked, not claimed:

```bash
python3 tests/test_tables.py                    # 86 engine tests
DAVIDSINO_SLOW=1 python3 tests/test_tables.py   # + all 2,598,960 poker hands enumerated
python3 scripts/bj_basic_strategy.py 2000000    # measure the blackjack edge yourself
```

The blackjack figure is 0.644% over 2,000,000 hands, which carries a one-sigma
error of about ±0.08% — blackjack is high-variance, so a short run cannot pin it
down. The published reference for these rules with unlimited resplits is ~0.62%;
this table allows one split only, which accounts for the rest. `bj_basic_strategy.py`
plays the hit-soft-17 chart, including the deviations the rule forces (double
eleven against an ace, soft eighteen from a deuce, soft nineteen against a six).

The fan-tan and baccarat edges are verified by **exact enumeration** of the
outcome space, not sampling. The Mississippi Stud paytable is verified by
classifying every one of the 2,598,960 possible five-card hands and comparing the
category counts to published poker frequencies — all twelve match exactly.

### When the cards are decided

This is the part that matters for a table game:

> The entire shoe is shuffled **once**, at the start of the hand, from
> `(server_seed, client_seed, nonce)`. Every card that will be dealt is fixed
> before you make a single decision.

So the house cannot look at your hit and then choose a card to bust you — that
card was already sitting at that position in the shoe. A hand spanning several
requests (blackjack, Mississippi Stud) is verifiable end to end: reveal the seed,
re-run the shuffle, and every card that appeared must match, in order.

The server stores **no cards** for a live hand — only the seed context and the
decisions made. The shoe is re-derived from the seed on every request, so there
is exactly one source of truth for what was dealt.

Tables share the slots' seed pair, so one rotation audits the whole floor:

```bash
python3 scripts/verify_table.py \
    --server-seed <revealed seed> --client-seed <your seed> \
    --hash <hash you were shown before playing> \
    --game blackjack --nonce 7
```

## Production Deployment

### Option 1: Docker Compose (Recommended)
```bash
docker compose up -d
```
For production, set your own pins via environment variables:
```bash
ADMIN_PIN=your_secret_pin WORKER_PIN=your_worker_pin docker compose up -d
```

To expose publicly, pair with a reverse proxy (nginx, Caddy) or Cloudflare Tunnel pointing at `http://localhost:8000`.

### Option 2: Direct
Run with uvicorn behind nginx reverse proxy.

### Option 2: Systemd Service
Create `/etc/systemd/system/davidsino.service`:
```ini
[Unit]
Description=Davidsino Rewards API
After=network.target postgresql.service

[Service]
Type=notify
User=dgawel
WorkingDirectory=/home/dgawel/.openclaw/workspace/davidsino-rewards
ExecStart=/home/dgawel/.openclaw/workspace/davidsino-rewards/venv/bin/python3 main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable davidsino
sudo systemctl start davidsino
```

### Option 3: Nginx Reverse Proxy
```nginx
server {
    listen 80;
    server_name rewards.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## API Endpoints

### Public
- `GET /` - Frontend app
- `POST /api/scan` - Scan card, get player info
- `GET /api/health` - Health check

### Admin
- `POST /api/admin/auth` - Authenticate with PIN
- `POST /api/admin/register` - Register new player
- `POST /api/admin/deposit` - Add deposit/points
- `POST /api/admin/loss` - Record loss/spend
- `POST /api/admin/adjustment` - Manual adjustment
- `GET /api/admin/players` - List all players
- `GET /api/admin/transactions/{id}` - Player transaction history

### Payments
- `GET /api/payments/methods` - Configured deposit methods
- `POST /api/payments/deposit-request` - Create a pending deposit
- `GET /api/payments/request/{id}` - Poll status
- `POST /api/payments/request/{id}/txid` - Attach a transaction hash
- `GET /api/payments/request/{id}/qr` - QR PNG of the payment URI
- `GET /api/admin/pending-deposits` - Dealer queue *(needs `X-Admin-Pin`)*
- `POST /api/admin/pending-deposits/{id}/confirm` - Credit it *(needs `X-Admin-Pin`)*
- `POST /api/admin/pending-deposits/{id}/cancel` - Reject it *(needs `X-Admin-Pin`)*

### Slots
- `GET /api/slots/machines` - Machine list and paytables
- `GET /api/slots/seed?card_id=` - Current fairness commitment
- `POST /api/slots/seed/rotate` - Reveal server seed, start a new one
- `POST /api/slots/spin` - Spin (wagers reward points)
- `POST /api/slots/verify` - Recompute any spin from revealed values
- `GET /api/players/{id}/slot-history` - Past spins with seed context

### Tables
- `GET /api/tables/games` - Game list, limits, bet types and paytables
- `POST /api/tables/deal` - Start a hand *(baccarat and fan-tan settle here)*
- `POST /api/tables/action` - Hit/stand/double/split, or raise/fold
- `GET /api/tables/round/{id}?card_id=` - Re-read one hand
- `GET /api/tables/active?card_id=` - Recover an interrupted hand
- `GET /api/players/{id}/table-history` - Past hands with seed context

Tables reuse the slots' seed endpoints — `/api/slots/seed` and
`/api/slots/seed/rotate` cover both.

## Security Notes
- Change default ADMIN_PIN in `.env`
- For production, use HTTPS (Let's Encrypt)
- Don't expose port 8000 directly to internet — use nginx
- Consider adding rate limiting for production

### Known gap: legacy admin endpoints are unauthenticated

The original `/api/admin/*` routes (deposit, cashout, add_points,
redeem_points, register, players) check the PIN **in the browser only** — anyone
who can reach the port can call them directly with curl and hand themselves
points. That predates this branch and is unchanged here.

The new money-moving endpoints (`pending-deposits/*`) do require the PIN as an
`X-Admin-Pin` header, so they can't be driven from outside the UI. Worth
extending that dependency to the legacy routes before this is reachable from the
open internet.

## Hardware
- **USB RFID Reader:** Any USB HID-compatible reader (acts as keyboard)
- **NFC:** Android phones with Chrome (Web NFC API)
- **RFID Cards:** Standard MIFARE 13.56MHz cards work great
