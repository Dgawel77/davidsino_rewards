# Slots & Payment-Request Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Davidsino Rewards locally, add a self-serve deposit-request flow (crypto addresses + Venmo/Cash App/PayPal handles, dealer-confirmed), and add three playable slot machines that wager reward points.

**Architecture:** Everything stays in the existing two-tier shape: FastAPI monolith (`main.py` + new `slots.py` module) over Postgres, vanilla JS frontend served from `static/`. Slots are server-authoritative (RNG + paytables in Python; the browser only animates results). Payments are a *request ledger*, not a processor integration: players create pending deposits, the app shows where to send money (configured via env vars), and the dealer confirms receipt — which credits cash-in + points through the existing deposit logic.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL 17 (Docker), `qrcode[pil]` for QR PNGs, vanilla HTML/CSS/JS, pytest for engine tests.

## Global Constraints

- No real payment-processor integration (Stripe/Square/Apple Pay merchant APIs) — processors prohibit gambling transactions; this app only *records* transfers made person-to-person. Apple Pay / Google Pay are supported indirectly: players use them as the funding source inside Venmo/Cash App.
- Slots wager **reward points only**. Slot spins must never touch `total_cash_in`, `total_cash_out`, or PNL.
- Slot RNG must be `random.SystemRandom` (server-side, unseeded).
- Each machine's RTP must land in 88–96% (verified by simulation script).
- All new events go through the existing `record_event()` / `player_events` table. New event types: `slot_spin`, `deposit` (confirmed pending deposits reuse the existing type, with `metadata_json.method` set).
- Frontend stays vanilla JS in the existing dark-casino style (`#1a1a2e` background, `#ffd700` gold, `#e94560` red). No frameworks, no CDNs.
- Payment destinations come from env vars only (never hardcoded): `BTC_ADDRESS`, `ETH_ADDRESS`, `VENMO_HANDLE`, `CASHAPP_HANDLE`, `PAYPAL_ME`. Unset ⇒ that method is hidden.
- Python 3.12, existing pinned deps; add only `qrcode[pil]==8.0` (runtime) and `pytest` (dev-only, not in requirements.txt).

---

### Task 1: Baseline local setup

Get the unmodified app running so there's a known-good starting point.

**Files:**
- None modified. Uses existing `docker-compose.yml`.

**Interfaces:**
- Produces: running stack at `http://localhost:8000`, healthy `/api/health`, a test player (card `TEST123`, name `David`) with points to gamble.

- [ ] **Step 1: Start the stack**

```bash
docker compose up -d --build
```

- [ ] **Step 2: Verify health and seed a test player**

```bash
curl -sf localhost:8000/api/health
curl -sf -X POST localhost:8000/api/admin/register -H 'Content-Type: application/json' \
  -d '{"card_id":"TEST123","name":"David"}'
curl -sf -X POST localhost:8000/api/admin/deposit -H 'Content-Type: application/json' \
  -d '{"card_id":"TEST123","amount":100,"description":"seed"}'
```

Expected: health `{"status":"ok"...}`; deposit response shows `reward_points: 10000`.

---

### Task 2: Slot engine (`slots.py`) with tests and RTP verification

**Files:**
- Create: `slots.py` (already drafted in the worktree — machine definitions, spin logic)
- Create: `tests/test_slots.py`
- Create: `scripts/rtp_check.py`

**Interfaces:**
- Produces:
  - `slots.spin(machine_key: str, bet: float) -> dict` — returns `{"machine", "bet", "grid": list[list[str]], "win": float, "detail": str|None, "lines": [...](5x3 only)}`; raises `ValueError` on unknown machine or bet outside `[min_bet, max_bet]`.
  - `slots.machine_list() -> list[dict]` — `{"key","name","tagline","kind","min_bet","max_bet","paytable","paytable_note"}` per machine.
  - Machines: `classic` (3-reel), `diamond_dave` (3-reel high volatility), `vig_city` (5×3, 5 paylines, wild `🃏`).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_slots.py
import pytest
import slots


def test_machine_list_has_three_machines():
    keys = {m["key"] for m in slots.machine_list()}
    assert keys == {"classic", "diamond_dave", "vig_city"}


def test_spin_rejects_unknown_machine():
    with pytest.raises(ValueError):
        slots.spin("nope", 100)


def test_spin_rejects_bet_out_of_range():
    with pytest.raises(ValueError):
        slots.spin("classic", 5)      # below min 10
    with pytest.raises(ValueError):
        slots.spin("classic", 10_000) # above max 1000


def test_reel3_spin_shape():
    r = slots.spin("classic", 10)
    assert len(r["grid"]) == 1 and len(r["grid"][0]) == 3
    assert r["win"] >= 0 and r["bet"] == 10


def test_lines5x3_spin_shape():
    r = slots.spin("vig_city", 25)
    assert len(r["grid"]) == 3 and all(len(row) == 5 for row in r["grid"])
    assert r["win"] >= 0


def test_line_win_wilds_substitute():
    target, run = slots._line_win(["🃏", "💎", "💎", "🍒", "🍒"], slots.VIG_CITY["line_pays"])
    assert target == "💎" and run == 3


def test_line_win_all_wilds_scores_as_sevens():
    target, run = slots._line_win(["🃏"] * 5, slots.VIG_CITY["line_pays"])
    assert target == "7️⃣" and run == 5


def test_reel3_three_of_kind_pays_multiplier(monkeypatch):
    monkeypatch.setattr(slots.rng, "choice", lambda reel: "7️⃣")
    r = slots.spin("classic", 10)
    assert r["win"] == 10 * 120
```

- [ ] **Step 2: Run tests, verify current state**

Run: `python3 -m pytest tests/test_slots.py -v` (install pytest in a venv if absent).
Expected: engine is drafted, so most pass — any failure is a real bug to fix in `slots.py` before continuing.

- [ ] **Step 3: Write the RTP simulation script**

```python
# scripts/rtp_check.py
"""Simulate spins per machine and report RTP. Target: 88-96%."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import slots

N = 500_000

for m in slots.machine_list():
    bet = m["min_bet"]
    wagered = returned = 0.0
    for _ in range(N):
        r = slots.spin(m["key"], bet)
        wagered += bet
        returned += r["win"]
    rtp = returned / wagered * 100
    flag = "OK" if 88 <= rtp <= 96 else "OUT OF RANGE"
    print(f"{m['key']:14s} RTP {rtp:6.2f}%  {flag}")
```

- [ ] **Step 4: Run RTP check and tune paytables**

Run: `python3 scripts/rtp_check.py`
Expected: all three machines print `OK`. If a machine is out of range, adjust in `slots.py` and re-run:
- `classic`: tune `pay2` values (pairs dominate RTP; cherry-pair 2× ≈ 24 pts of RTP).
- `diamond_dave`: tune `pay2` for 💎/7️⃣/🎰 and the `single` 🍀 refund (0.5 ↔ 0.4).
- `vig_city`: tune `line_pays` for 🍒/🍋/🍇 3-of-a-kinds (the frequent hits).

- [ ] **Step 5: Commit**

```bash
git add slots.py tests/test_slots.py scripts/rtp_check.py
git commit -m "feat: add slot machine engine with three machines, tests, RTP check"
```

---

### Task 3: Slots API endpoints

**Files:**
- Modify: `main.py` (imports, Pydantic model, two routes; frontend-label map untouched)

**Interfaces:**
- Consumes: `slots.spin`, `slots.machine_list` from Task 2; existing `record_event`, `get_db`, `Player`.
- Produces:
  - `GET /api/slots/machines` → `{"machines": [...]}` (machine_list passthrough)
  - `POST /api/slots/spin` body `{"card_id": str, "machine": str, "bet": float}` → `{"grid", "win", "bet", "detail", "lines"?, "reward_points": float}`; 404 unknown card, 400 bad bet/machine or insufficient points.

- [ ] **Step 1: Add the endpoints**

```python
# main.py — near other imports
import slots as slots_engine

# with the other Pydantic models
class SlotSpinRequest(BaseModel):
    card_id: str
    machine: str
    bet: float

# new routes section before "Routes - Admin List"
# ============================================================
# Routes - Slots (bets and wins are reward points, never cash/PNL)
# ============================================================
@app.get("/api/slots/machines")
def get_slot_machines():
    return {"machines": slots_engine.machine_list()}

@app.post("/api/slots/spin")
def slot_spin(request: SlotSpinRequest, db: Session = Depends(get_db)):
    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    bet = round(float(request.bet), 2)
    if player.reward_points < bet:
        raise HTTPException(status_code=400,
            detail=f"Insufficient points. Need {bet:.0f}, have {player.reward_points:.0f}")
    try:
        result = slots_engine.spin(request.machine, bet)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    net = result["win"] - bet
    player.reward_points += net
    machine_name = slots_engine.MACHINES[request.machine]["name"]
    outcome = result["detail"] or "No win"
    record_event(
        db, player.id, "slot_spin",
        points_delta=net,
        metadata={"machine": request.machine, "bet": bet,
                  "win": result["win"], "grid": result["grid"]},
        description=f"{machine_name}: bet {bet:.0f}, {outcome}",
    )
    db.commit()
    db.refresh(player)
    result["reward_points"] = player.reward_points
    return result
```

- [ ] **Step 2: Verify against the running stack**

```bash
docker compose up -d --build
curl -sf localhost:8000/api/slots/machines | python3 -m json.tool | head
curl -sf -X POST localhost:8000/api/slots/spin -H 'Content-Type: application/json' \
  -d '{"card_id":"TEST123","machine":"classic","bet":10}'
curl -s -X POST localhost:8000/api/slots/spin -H 'Content-Type: application/json' \
  -d '{"card_id":"TEST123","machine":"classic","bet":99999}'   # expect 400
curl -s -X POST localhost:8000/api/slots/spin -H 'Content-Type: application/json' \
  -d '{"card_id":"NOBODY","machine":"classic","bet":10}'       # expect 404
```

Expected: spin returns grid + updated `reward_points`; history endpoint shows a `slot_spin` event. (Note: Dockerfile must COPY `slots.py` — done in Task 6; until then run via local venv or add the COPY line early.)

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: add slots API endpoints wagering reward points"
```

---

### Task 4: Slots frontend

**Files:**
- Modify: `static/index.html` (slots views + CSS)
- Create: `static/slots.js`
- Modify: `static/app.js` (register views, menu hook, event label)

**Interfaces:**
- Consumes: `GET /api/slots/machines`, `POST /api/slots/spin`; `showView()`, `currentCardId` from `app.js`.
- Produces: menu card "🎰 Slots"; a "🎰 Play Slots" button on the scan-result view; views `slots-lobby-view` and `slots-play-view`; global functions `showSlotsLobby()`, `openMachine(key)`, `doSpin()`, `slotsBack()`.

- [ ] **Step 1: Add views + CSS to `index.html`**

Add to `showView`'s list in `app.js`: `'slots-lobby-view', 'slots-play-view'`.

```html
<!-- after the Leaderboard card in the main menu -->
<div class="card">
    <button class="btn btn-gold" onclick="showSlotsLobby()">🎰 Slots</button>
    <p style="text-align:center; color:#888; font-size:0.85rem; margin-top:8px;">
        Play your reward points
    </p>
</div>

<!-- new views before the closing .container div -->
<div id="slots-lobby-view" class="hidden">
    <button class="back-btn" onclick="backToMenu()">← Back</button>
    <div class="card">
        <h3 style="margin-bottom:12px;">🎰 Pick a Machine</h3>
        <div class="input-group">
            <label>Card ID</label>
            <input type="text" id="slots-card-id" placeholder="Scan or enter card ID">
        </div>
        <div id="slots-player-banner" class="hidden" style="text-align:center; margin-bottom:12px;">
            <span style="color:#ffd700; font-weight:bold;" id="slots-player-name"></span>
            · <span id="slots-player-points" style="color:#ffd700;">0</span> pts
        </div>
        <div id="machine-list">Loading...</div>
    </div>
</div>

<div id="slots-play-view" class="hidden">
    <button class="back-btn" onclick="slotsBack()">← Machines</button>
    <div class="card" style="text-align:center;">
        <div class="machine-name" id="machine-title"></div>
        <div class="machine-tagline" id="machine-tagline"></div>
        <div class="slot-balance">Points: <span id="slot-balance">0</span></div>
        <div id="reel-area" class="reel-area"></div>
        <div id="spin-result" class="spin-result"></div>
        <div class="bet-row">
            <button class="bet-adjust" onclick="adjustBet(-1)">−</button>
            <div class="bet-display"><span id="bet-amount">10</span> pts</div>
            <button class="bet-adjust" onclick="adjustBet(1)">+</button>
        </div>
        <button class="btn btn-gold" id="spin-btn" onclick="doSpin()">SPIN</button>
        <button class="btn btn-secondary btn-small" onclick="togglePaytable()">Paytable</button>
        <div id="paytable" class="paytable hidden"></div>
    </div>
</div>
```

CSS (inside the existing `<style>` block):

```css
.machine-item { padding:14px; background:#1a1a2e; border-radius:8px; margin-bottom:8px;
    cursor:pointer; border:2px solid transparent; }
.machine-item:active { border-color:#ffd700; }
.machine-name { font-size:1.3rem; color:#ffd700; font-weight:bold; }
.machine-tagline { font-size:0.8rem; color:#888; margin-bottom:10px; }
.slot-balance { margin:8px 0; color:#aaa; }
.slot-balance span { color:#ffd700; font-weight:bold; }
.reel-area { display:flex; justify-content:center; gap:6px; margin:14px 0; }
.reel-col { display:flex; flex-direction:column; gap:6px; }
.reel-cell { width:58px; height:58px; display:flex; align-items:center; justify-content:center;
    font-size:2rem; background:#1a1a2e; border:2px solid #0f3460; border-radius:8px; }
.reel-cell.spinning { animation: reelblur 0.12s linear infinite; }
.reel-cell.win-cell { border-color:#ffd700; box-shadow:0 0 12px #ffd70088; }
@keyframes reelblur { 0%{filter:blur(0)} 50%{filter:blur(2px)} 100%{filter:blur(0)} }
.spin-result { min-height:28px; font-weight:bold; margin-bottom:6px; }
.spin-result.win { color:#ffd700; animation: winpulse 0.5s ease 3; }
.spin-result.lose { color:#888; }
@keyframes winpulse { 50%{transform:scale(1.15)} }
.bet-row { display:flex; justify-content:center; align-items:center; gap:12px; margin-bottom:12px; }
.bet-adjust { width:44px; height:44px; border-radius:50%; border:none; background:#0f3460;
    color:#fff; font-size:1.4rem; cursor:pointer; }
.bet-display { min-width:90px; font-weight:bold; color:#ffd700; }
.paytable { margin-top:10px; text-align:left; font-size:0.85rem; }
.paytable-row { display:flex; justify-content:space-between; padding:4px 8px;
    border-bottom:1px solid #0f3460; }
.paytable-note { color:#888; font-size:0.75rem; margin-top:6px; }
```

- [ ] **Step 2: Write `static/slots.js`**

Full logic: load machines into the lobby; card-id resolution (pre-fill from `currentCardId` after a scan); bet stepping through presets (min, 25, 50, 100, 250, 500, 1000, ... capped to machine max); spin = disable button → animate all cells cycling random symbols ~900ms → settle columns left→right with the server grid → highlight win, update balance. Server response is authoritative; animation is cosmetic. Include `SYMBOLS` pool per machine gathered from its paytable rows for the shuffle animation, falling back to a generic emoji set. Errors (400/404) render in `#spin-result` in red.

```html
<!-- index.html, after app.js -->
<script src="/static/slots.js"></script>
```

- [ ] **Step 3: Register views + event label in `app.js`**

```js
// showView list gains: 'slots-lobby-view', 'slots-play-view'
// formatEventType labels gains: 'slot_spin': '🎰 Slots'
// scan-result view gains a button wired to: showSlotsLobby({ prefill: currentCardId })
```

- [ ] **Step 4: Verify in browser**

Run: `docker compose up -d --build`, open `http://localhost:8000`.
Expected: Slots card on menu → lobby lists 3 machines → enter `TEST123` → spin works, balance moves, history shows `🎰 Slots` events, insufficient-points shows a red error. Check mobile width (≤500px container).

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/slots.js static/app.js
git commit -m "feat: add slots frontend with three machines and reel animation"
```

---

### Task 5: Payments backend (pending deposits + payment methods + QR)

**Files:**
- Modify: `main.py` (model, config, routes)
- Modify: `requirements.txt` (add `qrcode[pil]==8.0`)
- Modify: `.env.example` (document new vars)

**Interfaces:**
- Consumes: existing `Player`, `record_event`, deposit crediting rules (points = amount × 100).
- Produces:
  - `GET /api/payments/methods` → `{"methods": [{"key","label","kind","address"|"handle","link","qr": bool}]}` — only env-configured methods.
  - `POST /api/payments/deposit-request` `{"card_id", "amount", "method"}` → `{"request_id", "status": "pending", "instructions": {...method payload...}}`
  - `GET /api/payments/qr/{method}` → PNG QR of the payment URI.
  - `GET /api/admin/pending-deposits` → `{"pending": [...]}` (status=pending, newest first, with player names)
  - `POST /api/admin/pending-deposits/{req_id}/confirm` → credits deposit (cash_in, +points×100, `deposit` event with `metadata.method`), marks confirmed.
  - `POST /api/admin/pending-deposits/{req_id}/cancel` → marks cancelled.
  - Table `pending_deposits(id, player_id FK, amount Numeric, method, status, created_at, resolved_at)`.

- [ ] **Step 1: Add model + method config**

```python
# main.py — ORM models section
class PendingDeposit(Base):
    __tablename__ = "pending_deposits"
    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(String(30), nullable=False)
    status = Column(String(20), default="pending", index=True)  # pending|confirmed|cancelled
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime, nullable=True)

# config, next to ADMIN_PIN
def _payment_methods():
    """Build available payment methods from env. Unset env var = method hidden."""
    methods = {}
    btc = os.getenv("BTC_ADDRESS", "").strip()
    eth = os.getenv("ETH_ADDRESS", "").strip()
    venmo = os.getenv("VENMO_HANDLE", "").strip().lstrip("@")
    cashapp = os.getenv("CASHAPP_HANDLE", "").strip().lstrip("$")
    paypal = os.getenv("PAYPAL_ME", "").strip()
    if btc:
        methods["btc"] = {"key": "btc", "label": "Bitcoin", "kind": "crypto",
                          "address": btc, "uri": f"bitcoin:{btc}", "qr": True}
    if eth:
        methods["eth"] = {"key": "eth", "label": "Ethereum", "kind": "crypto",
                          "address": eth, "uri": f"ethereum:{eth}", "qr": True}
    if venmo:
        methods["venmo"] = {"key": "venmo", "label": "Venmo (works with Apple/Google Pay)",
                            "kind": "handle", "handle": f"@{venmo}",
                            "link": f"https://venmo.com/u/{venmo}", "qr": True}
    if cashapp:
        methods["cashapp"] = {"key": "cashapp", "label": "Cash App (works with Apple/Google Pay)",
                              "kind": "handle", "handle": f"${cashapp}",
                              "link": f"https://cash.app/${cashapp}", "qr": True}
    if paypal:
        methods["paypal"] = {"key": "paypal", "label": "PayPal",
                             "kind": "handle", "handle": paypal,
                             "link": f"https://paypal.me/{paypal}", "qr": True}
    return methods

PAYMENT_METHODS = _payment_methods()
```

- [ ] **Step 2: Add routes**

```python
# main.py — new section before "Routes - Worker"
# ============================================================
# Routes - Payments (request/confirm ledger; money moves person-to-person)
# ============================================================
import io
import qrcode
from fastapi.responses import StreamingResponse

class DepositRequestCreate(BaseModel):
    card_id: str
    amount: float
    method: str

@app.get("/api/payments/methods")
def payment_methods():
    return {"methods": list(PAYMENT_METHODS.values())}

@app.post("/api/payments/deposit-request")
def create_deposit_request(request: DepositRequestCreate, db: Session = Depends(get_db)):
    if request.method not in PAYMENT_METHODS:
        raise HTTPException(status_code=400, detail="Unknown payment method")
    if not (0 < request.amount <= 10000):
        raise HTTPException(status_code=400, detail="Amount must be between $0 and $10,000")
    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    req = PendingDeposit(player_id=player.id, amount=round(request.amount, 2),
                         method=request.method)
    db.add(req)
    db.commit()
    db.refresh(req)
    return {"request_id": req.id, "status": "pending",
            "player": player.name, "amount": float(req.amount),
            "instructions": PAYMENT_METHODS[request.method]}

@app.get("/api/payments/qr/{method}")
def payment_qr(method: str):
    info = PAYMENT_METHODS.get(method)
    if not info or not info.get("qr"):
        raise HTTPException(status_code=404, detail="No QR for this method")
    data = info.get("uri") or info.get("link")
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")

@app.get("/api/admin/pending-deposits")
def list_pending_deposits(db: Session = Depends(get_db)):
    rows = (db.query(PendingDeposit, Player)
              .join(Player, Player.id == PendingDeposit.player_id)
              .filter(PendingDeposit.status == "pending")
              .order_by(PendingDeposit.created_at.desc()).all())
    return {"pending": [{
        "id": r.PendingDeposit.id, "player": r.Player.name,
        "card_id": r.Player.card_id, "amount": float(r.PendingDeposit.amount),
        "method": r.PendingDeposit.method,
        "created_at": r.PendingDeposit.created_at.isoformat(),
    } for r in rows]}

def _resolve_pending(db, req_id, new_status):
    req = db.query(PendingDeposit).filter(PendingDeposit.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {req.status}")
    req.status = new_status
    req.resolved_at = datetime.now(timezone.utc)
    return req

@app.post("/api/admin/pending-deposits/{req_id}/confirm")
def confirm_pending_deposit(req_id: int, db: Session = Depends(get_db)):
    req = _resolve_pending(db, req_id, "confirmed")
    player = db.query(Player).filter(Player.id == req.player_id).first()
    amount = float(req.amount)
    player.total_cash_in += amount
    reward_earned = amount * 100
    player.reward_points += reward_earned
    method_label = PAYMENT_METHODS.get(req.method, {}).get("label", req.method)
    record_event(db, player.id, "deposit", cash_amount=amount,
                 points_delta=reward_earned, pnl_impact=amount,
                 metadata={"method": req.method, "pending_deposit_id": req.id},
                 description=f"Deposit ${amount:.2f} via {method_label} (+{reward_earned:.0f} pts)")
    db.commit()
    return {"message": f"Confirmed ${amount:.2f} via {method_label}",
            "player": player.name, "reward_points": player.reward_points}

@app.post("/api/admin/pending-deposits/{req_id}/cancel")
def cancel_pending_deposit(req_id: int, db: Session = Depends(get_db)):
    req = _resolve_pending(db, req_id, "cancelled")
    db.commit()
    return {"message": "Request cancelled"}
```

Note: `record_event`'s metadata parameter is named `metadata` in its signature — keep calls consistent with the existing helper.

- [ ] **Step 3: Update `requirements.txt` and `.env.example`**

```
qrcode[pil]==8.0
```

```
# .env.example additions
# Payment destinations (leave blank to hide a method)
BTC_ADDRESS=
ETH_ADDRESS=
VENMO_HANDLE=
CASHAPP_HANDLE=
PAYPAL_ME=
```

- [ ] **Step 4: Verify with curl** (set `VENMO_HANDLE=davidsino BTC_ADDRESS=bc1qtestaddr` in compose env for the test)

```bash
curl -sf localhost:8000/api/payments/methods
curl -sf -X POST localhost:8000/api/payments/deposit-request -H 'Content-Type: application/json' \
  -d '{"card_id":"TEST123","amount":25,"method":"venmo"}'
curl -sf localhost:8000/api/payments/qr/venmo -o /tmp/qr.png && file /tmp/qr.png  # PNG image
curl -sf localhost:8000/api/admin/pending-deposits
curl -sf -X POST localhost:8000/api/admin/pending-deposits/1/confirm   # credits $25 + 2500 pts
curl -s  -X POST localhost:8000/api/admin/pending-deposits/1/confirm   # expect 400 already confirmed
```

- [ ] **Step 5: Commit**

```bash
git add main.py requirements.txt .env.example
git commit -m "feat: add pending-deposit payment flow with crypto/handle methods and QR codes"
```

---

### Task 6: Payments frontend + Docker/env plumbing

**Files:**
- Modify: `static/index.html` (Add Funds view, admin Pending tab)
- Create: `static/payments.js`
- Modify: `static/app.js` (view registration, admin tab wiring)
- Modify: `Dockerfile` (`COPY slots.py .`)
- Modify: `docker-compose.yml` (pass payment env vars through)

**Interfaces:**
- Consumes: Task 5 endpoints; `showView`, `currentCardId`.
- Produces: menu card "💰 Add Funds" → `deposit-view`; on submit shows instructions (handle/address, QR `<img src="/api/payments/qr/...">`, open-app link, copy button) and the pending notice; admin tab `Pending` with per-request ✅ Confirm / ❌ Cancel.

- [ ] **Step 1: Add `deposit-view` + admin Pending tab to `index.html`; write `static/payments.js`**

`deposit-view`: card-id input (pre-filled from `currentCardId`), amount input, method buttons rendered from `/api/payments/methods` (hidden if none configured, with a "dealer hasn't set up payment methods" note), submit → render instructions block + "Show the dealer once you've sent it — points land after they confirm."
Admin: fourth tab button `Pending` → `admin-pending` div; `loadPendingDeposits()` renders rows with amount, player, method, age; Confirm/Cancel buttons call the endpoints and refresh the list and show the result via existing `showResult`.

- [ ] **Step 2: Dockerfile + compose**

```dockerfile
COPY main.py .
COPY slots.py .
```

```yaml
# docker-compose.yml app.environment additions
      BTC_ADDRESS: ${BTC_ADDRESS:-}
      ETH_ADDRESS: ${ETH_ADDRESS:-}
      VENMO_HANDLE: ${VENMO_HANDLE:-}
      CASHAPP_HANDLE: ${CASHAPP_HANDLE:-}
      PAYPAL_ME: ${PAYPAL_ME:-}
```

- [ ] **Step 3: Verify in browser**

`VENMO_HANDLE=davidsino docker compose up -d --build`; walk the full flow: scan TEST123 → Add Funds → $25 Venmo → QR renders → Dealer Mode → Pending tab → Confirm → player points +2500, history shows deposit via Venmo.

- [ ] **Step 4: Commit**

```bash
git add static/ Dockerfile docker-compose.yml
git commit -m "feat: add deposit-request frontend and admin pending-deposit tab"
```

---

### Task 7: Docs + ship

**Files:**
- Modify: `README.md` (Slots section, Payments section with env var table + the processor/licensing caveat)

**Interfaces:** none new.

- [ ] **Step 1: Update README**

Document: the three machines and their point ranges, RTP targets; payment env vars; the flow (request → send money person-to-person → dealer confirms); explicit note that Apple Pay/Google Pay work as funding sources inside Venmo/Cash App, and that direct processor integration is deliberately out of scope.

- [ ] **Step 2: Full end-to-end re-verify**

```bash
docker compose down && docker compose up -d --build
# health, spin, deposit-request, confirm, leaderboard — all green
python3 -m pytest tests/ -v
python3 scripts/rtp_check.py
```

- [ ] **Step 3: Commit, push, open draft PR**

```bash
git add README.md docs/
git commit -m "docs: document slots and payment-request flow"
git push -u origin feature/slots-and-payments
# open draft PR (no gh CLI on this machine — use the GitHub compare URL if push succeeds)
```

---

## Self-Review

- **Spec coverage:** (1) local setup → Task 1; (2) crypto + Apple Pay/GPay → Tasks 5–6 (request-ledger design, GPay/Apple Pay via Venmo/Cash App funding — the honest scope given processor gambling bans); (3) custom slots → Tasks 2–4. ✔
- **Placeholder scan:** Frontend JS for Tasks 4/6 is described by behavior contract rather than full listings (single-session execution; the executor has full codebase context). All backend code is complete. ✔
- **Type consistency:** `slots.spin` return shape matches Task 3's usage (`result["win"]`, `result["grid"]`, `MACHINES[key]["name"]`); `record_event(metadata=...)` matches the existing helper signature in `main.py`. `PendingDeposit.amount` is `Numeric` — cast to `float` before JSON. ✔
