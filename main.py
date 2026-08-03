"""
Davidsino Rewards - FastAPI Backend
Tracks player points, deposits, PNL, and event history for the casino loyalty program.
"""
import copy
import os
import secrets
import json
from datetime import datetime, timezone, timedelta, date
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Depends, Query, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, Numeric, Boolean, JSON, func, desc, asc
from sqlalchemy.orm import sessionmaker, declarative_base, Session as Session_
from sqlalchemy.dialects.postgresql import JSONB
from pydantic import BaseModel
from typing import Optional, List

import slots as slots_engine
import payments as payments_lib
import tables as tables_engine
import arcade as arcade_engine

load_dotenv()

# ============================================================
# Database Setup
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://davidsino:davidsino_pass@localhost:5432/davidsino")
ADMIN_PIN = os.getenv("ADMIN_PIN", "1234")
WORKER_PIN = os.getenv("WORKER_PIN", "5678")

IS_POSTGRES = DATABASE_URL.startswith(("postgresql", "postgres"))

# Postgres gets real JSONB; SQLite falls back to generic JSON so the app can be
# run locally with `DATABASE_URL=sqlite:///./davidsino.db` and no database server.
JSONType = JSONB if IS_POSTGRES else JSON

engine = create_engine(
    DATABASE_URL,
    # SQLite hands each connection its own thread by default, which breaks
    # FastAPI's threadpool. Harmless for a single-machine dev run.
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
if DATABASE_URL.startswith("sqlite"):
    # Write-ahead logging survives an unclean shutdown far better than the
    # default rollback journal, which matters when the whole thing lives on an
    # SD card in a Raspberry Pi that might lose power mid-hand.
    from sqlalchemy import event as _sa_event

    @_sa_event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ============================================================
# ORM Models
# ============================================================
class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, index=True)
    card_id = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # PNL tracking (cash in/out)
    total_cash_in = Column(Float, default=0.0)    # money player deposited
    total_cash_out = Column(Float, default=0.0)   # money player cashed out
    # Rewards points (separate ledger)
    reward_points = Column(Float, default=0.0)    # loyalty points balance

class Transaction(Base):
    """Legacy table - kept for backward compatibility. New writes go to player_events."""
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    amount = Column(Float, nullable=False)
    transaction_type = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class PlayerEvent(Base):
    """Unified event log for all player actions."""
    __tablename__ = "player_events"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)
    cash_amount = Column(Numeric(12, 2), default=0)
    points_delta = Column(Numeric(12, 2), default=0)
    pnl_impact = Column(Numeric(12, 2), default=0)
    metadata_json = Column(JSONType, default=dict)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class DailyRoast(Base):
    """Cache for daily AI roasts."""
    __tablename__ = "daily_roasts"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    roast_date = Column(DateTime, nullable=False)
    roast_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class SlotSeed(Base):
    """
    Provably-fair seed pair for one player.

    `server_seed` stays secret while `active` is true; only its SHA-256 hash is
    published. Rotating reveals the old seed so every spin made under it can be
    independently recomputed.
    """
    __tablename__ = "slot_seeds"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    server_seed = Column(String(128), nullable=False)
    server_seed_hash = Column(String(64), nullable=False, index=True)
    client_seed = Column(String(64), nullable=False)
    nonce = Column(Integer, default=0, nullable=False)
    active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    revealed_at = Column(DateTime, nullable=True)

class PendingDeposit(Base):
    """
    A player's declared intent to deposit. Credits nothing until a dealer
    confirms the money actually arrived.
    """
    __tablename__ = "pending_deposits"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    method = Column(String(30), nullable=False)
    status = Column(String(20), default="pending", nullable=False, index=True)  # pending|confirmed|cancelled
    txid = Column(String(200), nullable=True)
    instructions_json = Column(JSONType, default=dict)  # snapshot: address/amount/rate shown to the player
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime, nullable=True)

class Session(Base):
    """
    A logged-in card.

    The card ID is the credential, so it must not be the thing sent on every
    request — one shoulder-surf of a URL would be enough. Scanning exchanges it
    once for a random token with an expiry, and everything after that presents
    the token.
    """
    __tablename__ = "sessions"

    token = Column(String(64), primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=False, index=True)
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class TableRound(Base):
    """
    One hand of a table game that spans more than a single request.

    Blackjack and Mississippi Stud need somewhere to keep the hand between a
    deal and a decision. The cards are NOT stored — only the seed context and
    the choices made — so the shoe is re-derived from (server_seed, client_seed,
    nonce) on every request. That keeps a single source of truth for what was
    dealt: the seed. The state blob holds positions and bets, never a deck the
    server could quietly rewrite.
    """
    __tablename__ = "table_rounds"

    id = Column(Integer, primary_key=True, index=True)
    player_id = Column(Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=False, index=True)
    game = Column(String(30), nullable=False, index=True)
    nonce = Column(Integer, nullable=False)
    client_seed = Column(String(64), nullable=False)
    server_seed_hash = Column(String(64), nullable=False, index=True)
    state_json = Column(JSONType, default=dict)
    wagered = Column(Float, default=0.0, nullable=False)   # points already debited
    payout = Column(Float, default=0.0, nullable=False)
    status = Column(String(20), default="active", nullable=False, index=True)  # active|settled
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    settled_at = Column(DateTime, nullable=True)

# Create all tables
Base.metadata.create_all(bind=engine)

# ============================================================
# Preset Rewards (for worker redemptions)
# ============================================================
PRESET_REWARDS = {
    "drink_basic": {"name": "Basic Drink", "points": 500, "description": "Soda, water, or basic mixer ($5 value)"},
    "drink_premium": {"name": "Premium Drink", "points": 1500, "description": "Cocktail or premium beverage ($15 value)"},
    "snack": {"name": "Snack", "points": 800, "description": "Chips, candy, or small snack ($8 value)"},
    "meal": {"name": "Meal Voucher", "points": 2500, "description": "Food court or kitchen meal ($25 value)"},
    "entry_credit": {"name": "Tournament Entry", "points": 5000, "description": "Entry credit for tournament ($50 value)"},
    "vip_upgrade": {"name": "VIP Upgrade", "points": 10000, "description": "VIP area access for the night ($100 value)"},
}

# ============================================================
# Pydantic Models
# ============================================================
class PlayerResponse(BaseModel):
    id: int
    card_id: str
    name: str
    total_cash_in: float
    total_cash_out: float
    reward_points: float
    pnl: float

    class Config:
        from_attributes = True

class ScanRequest(BaseModel):
    card_id: str

class AdminAuth(BaseModel):
    pin: str
    role: str = "admin"

class DepositRequest(BaseModel):
    card_id: str
    amount: float
    description: str = ""

class LossRequest(BaseModel):
    card_id: str
    amount: float
    description: str = ""

class AdjustmentRequest(BaseModel):
    card_id: str
    amount: float
    description: str = ""

class RegisterRequest(BaseModel):
    card_id: str
    name: str

class WorkerRedeemRequest(BaseModel):
    card_id: str
    reward_key: str

class RoastRequest(BaseModel):
    player_id: int

class SlotSpinRequest(BaseModel):
    card_id: str
    machine: str
    bet: float

class SlotSeedRotateRequest(BaseModel):
    card_id: str
    client_seed: Optional[str] = None

class SlotVerifyRequest(BaseModel):
    machine: str
    bet: float
    server_seed: str
    client_seed: str
    nonce: int

class DepositRequestCreate(BaseModel):
    card_id: str
    amount: float
    method: str

class TxidRequest(BaseModel):
    txid: str

class TableDealRequest(BaseModel):
    card_id: str
    game: str
    bet: float
    # Instant games (baccarat, fan-tan, plinko) settle in this one call, so they
    # carry their bet selection with the deal.
    bet_type: Optional[str] = None
    picks: Optional[List[int]] = None
    risk: Optional[str] = None        # plinko
    balls: Optional[int] = None       # plinko: drop several at once
    mines: Optional[int] = None       # mines
    target: Optional[float] = None    # crash auto cash-out

class TableActionRequest(BaseModel):
    card_id: str
    round_id: int
    action: str
    multiple: Optional[int] = None
    tile: Optional[int] = None        # mines

# ============================================================
# FastAPI App
# ============================================================
app = FastAPI(title="Davidsino Rewards", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RevalidatingStatic(StaticFiles):
    """
    Static files that a browser must always revalidate.

    Starlette sends only ETag and Last-Modified. With no Cache-Control, browsers
    fall back to *heuristic* freshness and may serve a cached copy without asking
    the server at all — so a redeploy silently leaves people running old JS. The
    failure is nasty: index.html arrives fresh with a button wired to a function
    that only exists in the new app.js, the browser serves the old app.js from
    cache, and the button does nothing at all with no visible error.

    `no-cache` still allows caching; it just forces a revalidation first, so an
    unchanged file costs an empty 304 and a changed one is picked up at once.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.mount("/static", RevalidatingStatic(directory="static"), name="static")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "12"))

# A hard ceiling on the roster. This is a private table for a few friends, not a
# sign-up product — 0 means no limit, for a setup that wants one.
MAX_PLAYERS = int(os.getenv("MAX_PLAYERS", "3"))

# Scanning is the only unauthenticated way in, so it is the only thing worth
# brute-forcing. Wrong guesses are counted per source address.
_scan_failures = {}
SCAN_MAX_FAILURES = int(os.getenv("SCAN_MAX_FAILURES", "10"))
SCAN_LOCKOUT_MINUTES = int(os.getenv("SCAN_LOCKOUT_MINUTES", "5"))


def _client_ip(request) -> str:
    return (request.client.host if request and request.client else "unknown")


def _scan_locked(ip: str) -> bool:
    entry = _scan_failures.get(ip)
    if not entry:
        return False
    count, until = entry
    if datetime.now(timezone.utc) >= until:
        _scan_failures.pop(ip, None)
        return False
    return count >= SCAN_MAX_FAILURES


def _note_scan_failure(ip: str) -> None:
    count, until = _scan_failures.get(ip, (0, datetime.now(timezone.utc)))
    if datetime.now(timezone.utc) >= until:
        count = 0
    _scan_failures[ip] = (count + 1,
                          datetime.now(timezone.utc) + timedelta(minutes=SCAN_LOCKOUT_MINUTES))


def _clear_scan_failures(ip: str) -> None:
    _scan_failures.pop(ip, None)


def _issue_session(db: Session_, player: Player) -> "Session":
    token = secrets.token_urlsafe(32)
    sess = Session(
        token=token,
        player_id=player.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS),
    )
    db.add(sess)
    return sess


def current_player(authorization: str = Header(None),
                   db: Session_ = Depends(get_db)) -> Player:
    """
    Resolve the caller from their session token.

    Every endpoint that moves points or shows a player's private ledger depends
    on this, so a card ID alone is never enough to act as somebody.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Scan your card to play")
    token = authorization.split(" ", 1)[1].strip()

    sess = db.query(Session).filter(Session.token == token).first()
    if not sess:
        raise HTTPException(status_code=401, detail="Scan your card to play")

    expires = sess.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires:
        db.delete(sess)
        db.commit()
        raise HTTPException(status_code=401, detail="Session expired — scan again")

    player = db.query(Player).filter(Player.id == sess.player_id).first()
    if not player:
        raise HTTPException(status_code=401, detail="That card is no longer on file")

    sess.last_seen = datetime.now(timezone.utc)
    return player


def require_self(player: Player, card_id: str) -> None:
    """A request may only ever act on the card it was issued for."""
    if card_id and card_id != player.card_id:
        raise HTTPException(status_code=403, detail="That is not your card")


def require_admin(x_admin_pin: str = Header(None)):
    """
    Gate for endpoints that move money.

    NOTE: the pre-existing /api/admin/* routes are unauthenticated — the PIN is
    only checked in the browser. That is a known gap tracked separately; new
    money-moving endpoints require the PIN as a header so it can't be bypassed
    by calling the API directly.
    """
    if x_admin_pin != ADMIN_PIN:
        raise HTTPException(status_code=401, detail="Admin PIN required")
    return True

def get_pnl(player: Player) -> float:
    """PNL = cash_out - cash_in (positive = player ahead, negative = player down)"""
    return player.total_cash_out - player.total_cash_in

def record_event(db: Session_, player_id: int, event_type: str, cash_amount: float = 0,
                 points_delta: float = 0, pnl_impact: float = 0, metadata: dict = None,
                 description: str = ""):
    """Record a player event in the event log."""
    event = PlayerEvent(
        player_id=player_id,
        event_type=event_type,
        cash_amount=cash_amount,
        points_delta=points_delta,
        pnl_impact=pnl_impact,
        metadata_json=metadata or {},
        description=description,
    )
    db.add(event)

# ============================================================
# Routes - Public
# ============================================================
def _asset_version() -> str:
    """
    A stamp that changes whenever any front-end file does.

    Belt and braces alongside the no-cache headers: even a proxy or a browser
    that ignores them cannot reuse a bundle whose URL has changed.
    """
    newest = 0.0
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    for name in os.listdir(static_dir):
        if name.endswith((".js", ".css")):
            newest = max(newest, os.path.getmtime(os.path.join(static_dir, name)))
    return str(int(newest))


@app.get("/")
def serve_frontend():
    # The shell must never be stale — it is what points at every other asset.
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
    with open(path, encoding="utf-8") as fh:
        html = fh.read().replace("__ASSET_VERSION__", _asset_version())
    return Response(content=html, media_type="text/html",
                    headers={"Cache-Control": "no-store, must-revalidate"})

@app.get("/api/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.post("/api/scan")
def scan_card(request: ScanRequest, http_request: Request, db: Session_ = Depends(get_db)):
    """
    The only door into the app: present a registered card, get a session token.

    An unregistered card is a failed attempt, counted per address — this is the
    one unauthenticated endpoint, so it is the only one worth guessing at.
    """
    ip = _client_ip(http_request)
    if _scan_locked(ip):
        raise HTTPException(
            status_code=429,
            detail=f"Too many bad cards. Wait {SCAN_LOCKOUT_MINUTES} minutes or see the dealer.")

    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        _note_scan_failure(ip)
        return {"registered": False, "card_id": request.card_id}

    _clear_scan_failures(ip)
    # Sweep this player's expired sessions while we are here.
    db.query(Session).filter(Session.player_id == player.id,
                             Session.expires_at < datetime.now(timezone.utc)).delete()
    sess = _issue_session(db, player)
    db.commit()

    pnl = get_pnl(player)
    return {
        "registered": True,
        "token": sess.token,
        "expires_at": sess.expires_at.isoformat(),
        "player": {
            "id": player.id,
            "card_id": player.card_id,
            "name": player.name,
            "reward_points": player.reward_points,
            "total_cash_in": player.total_cash_in,
            "total_cash_out": player.total_cash_out,
            "pnl": pnl,
        }
    }

@app.post("/api/auth/logout")
def logout(authorization: str = Header(None), db: Session_ = Depends(get_db)):
    """Drop the session server-side, so a stolen token dies with the sign-out."""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        db.query(Session).filter(Session.token == token).delete()
        db.commit()
    return {"ok": True}


@app.get("/api/players/search")
def search_players(query: str = Query(..., min_length=1), db: Session_ = Depends(get_db),
                   _: None = Depends(require_admin)):
    """
    Dealer lookup by name or card ID.

    PIN-gated because it returns card IDs, and a card ID is now the credential
    that logs somebody in — open, this endpoint handed out every player's login
    to anyone who could reach the box.
    """
    search_term = f"%{query.lower()}%"
    players = db.query(Player).filter(
        (Player.name.ilike(search_term)) | (Player.card_id.ilike(search_term))
    ).all()
    
    result = []
    for p in players:
        pnl = get_pnl(p)
        result.append({
            "id": p.id,
            "card_id": p.card_id,
            "name": p.name,
            "reward_points": p.reward_points,
            "total_cash_in": p.total_cash_in,
            "total_cash_out": p.total_cash_out,
            "pnl": pnl,
        })
    return {"players": result, "count": len(result)}

# ============================================================
# Routes - History & Analytics
# ============================================================
@app.get("/api/players/{player_id}/history")
def get_player_history(player_id: int, db: Session_ = Depends(get_db),
                       limit: int = Query(100, ge=1, le=500),
                       offset: int = Query(0, ge=0),
                   me: Player = Depends(current_player)):
    """Get paginated event history for a player"""
    if me.id != player_id:
        raise HTTPException(status_code=403, detail="That is not your ledger")
    events = db.query(PlayerEvent).filter(
        PlayerEvent.player_id == player_id
    ).order_by(PlayerEvent.created_at.desc()).offset(offset).limit(limit).all()

    total = db.query(func.count(PlayerEvent.id)).filter(
        PlayerEvent.player_id == player_id
    ).scalar()

    return {
        "events": [{
            "id": e.id,
            "event_type": e.event_type,
            "cash_amount": float(e.cash_amount),
            "points_delta": float(e.points_delta),
            "pnl_impact": float(e.pnl_impact),
            "description": e.description,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        } for e in events],
        "total": total,
        "limit": limit,
        "offset": offset,
    }

@app.get("/api/players/{player_id}/daily-pnl")
def get_daily_pnl(player_id: int, db: Session_ = Depends(get_db),
                   me: Player = Depends(current_player)):
    """Get daily PNL summary with running total for a player"""
    if me.id != player_id:
        raise HTTPException(status_code=403, detail="That is not your ledger")
    # Group events by date and sum pnl_impact
    daily = db.query(
        func.date(PlayerEvent.created_at).label("day"),
        func.sum(PlayerEvent.pnl_impact).label("daily_pnl")
    ).filter(
        PlayerEvent.player_id == player_id
    ).group_by(
        func.date(PlayerEvent.created_at)
    ).order_by(
        asc("day")
    ).all()

    result = []
    running_total = 0.0
    for row in daily:
        running_total += float(row.daily_pnl)
        result.append({
            "date": row.day.isoformat(),
            "daily_pnl": float(row.daily_pnl),
            "running_total": running_total,
        })

    return {"daily_pnl": result}

@app.get("/api/players/{player_id}/summary")
def get_player_summary(player_id: int, db: Session_ = Depends(get_db),
                   me: Player = Depends(current_player)):
    """Get full account summary including roast"""
    if me.id != player_id:
        raise HTTPException(status_code=403, detail="That is not your ledger")
    player = db.query(Player).filter(Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    pnl = get_pnl(player)

    # Get recent events (last 10)
    recent_events = db.query(PlayerEvent).filter(
        PlayerEvent.player_id == player_id
    ).order_by(PlayerEvent.created_at.desc()).limit(10).all()

    # Get or generate today's roast
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    roast = db.query(DailyRoast).filter(
        DailyRoast.player_id == player_id,
        DailyRoast.roast_date >= today
    ).first()

    roast_text = roast.roast_text if roast else None

    # Stats
    total_events = db.query(func.count(PlayerEvent.id)).filter(
        PlayerEvent.player_id == player_id
    ).scalar()

    return {
        "player": {
            "id": player.id,
            "name": player.name,
            "card_id": player.card_id,
            "reward_points": player.reward_points,
            "total_cash_in": player.total_cash_in,
            "total_cash_out": player.total_cash_out,
            "pnl": pnl,
        },
        "stats": {
            "total_events": total_events,
        },
        "recent_events": [{
            "id": e.id,
            "event_type": e.event_type,
            "cash_amount": float(e.cash_amount),
            "points_delta": float(e.points_delta),
            "pnl_impact": float(e.pnl_impact),
            "description": e.description,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        } for e in recent_events],
        "roast": roast_text,
    }


def _generate_roast_text(player: Player, pnl: float, recent_events: list) -> str:
    """Generate a witty roast using rule-based logic. Dealer is always excellent."""
    import random
    name = player.name
    points = player.reward_points
    cash_in = player.total_cash_in
    cash_out = player.total_cash_out

    redeem_count = sum(1 for e in recent_events if e.event_type == 'reward_redeem')

    roasts_winning = [
        f"{name} is up ${pnl:.2f}. Enjoy it while it lasts — the house has all night.",
        f"{name} walked out +${pnl:.2f}. The dealer says 'see you tomorrow.'",
        f"Up ${pnl:.2f}? {name} must've found a lucky seat. Doesn't exist, but they believe it.",
        f"{name} is ahead by ${pnl:.2f}. The vig will catch up eventually.",
        f"+${pnl:.2f} for {name}. Don't get cocky — the math always wins.",
    ]
    roasts_losing = [
        f"{name} is down ${abs(pnl):.2f}. The Davidsino thanks you for your generous donation.",
        f"-${abs(pnl):.2f} later and {name} still thinks 'one more hand' is a strategy.",
        f"{name} brought ${cash_in:.0f}, left with ${cash_out:.0f}. That's not bad luck — that's the vig, baby.",
        f"Down ${abs(pnl):.2f}. {name}, the dealer is excellent. Your strategy? Less so.",
        f"{name} is in the red by ${abs(pnl):.2f}. At least the free drinks were worth it.",
    ]
    roasts_neutral = [
        f"{name} broke even. Boring. The house didn't even break a sweat.",
        f"{name} walked in, walked out, same wallet. Come back and actually play.",
        f"{name}'s PNL is $0.00. Either very disciplined or very unlucky at the tables.",
        f"No net change for {name}. The Davidsino accepts IOUs for excitement.",
    ]
    roasts_high_roller = [
        f"{name} dropped ${cash_in:.0f} in chips. The dealer says thanks, and please come again.",
        f"${cash_in:.0f} in and {name} still hasn't hit the VIP lounge. Try harder.",
        f"{name} threw ${cash_in:.0f} at the tables. House says: 'we accept all major cards.'",
    ]
    roasts_points_hoarder = [
        f"{name} is sitting on {points:.0f} points. Hoarder? Or just waiting for the right moment?",
        f"{points:.0f} reward points and counting. {name}, it's not a retirement fund. Spend it.",
    ]
    roasts_frequent_redeemer = [
        f"{name} redeemed {redeem_count} times. Either loves free drinks or has no impulse control.",
        f"{redeem_count} redemptions for {name}. The worker says 'another one?'",
    ]

    if pnl > 0:
        return random.choice(roasts_winning)
    elif pnl < 0:
        if cash_in > 500:
            return random.choice(roasts_high_roller + roasts_losing)
        return random.choice(roasts_losing)
    else:
        if points > 5000:
            return random.choice(roasts_points_hoarder)
        if redeem_count > 3:
            return random.choice(roasts_frequent_redeemer)
        return random.choice(roasts_neutral)


@app.post("/api/players/{player_id}/roast")
def generate_roast(player_id: int, db: Session_ = Depends(get_db),
                   me: Player = Depends(current_player)):
    """Generate and cache a new AI roast for a player"""
    if me.id != player_id:
        raise HTTPException(status_code=403, detail="That is not your ledger")
    player = db.query(Player).filter(Player.id == player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    pnl = get_pnl(player)

    # Get recent activity
    recent = db.query(PlayerEvent).filter(
        PlayerEvent.player_id == player_id
    ).order_by(PlayerEvent.created_at.desc()).limit(20).all()

    roast_text = _generate_roast_text(player, pnl, recent)

    # Cache the roast for today
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    existing = db.query(DailyRoast).filter(
        DailyRoast.player_id == player_id,
        DailyRoast.roast_date >= today
    ).first()

    if existing:
        existing.roast_text = roast_text
        existing.created_at = datetime.now(timezone.utc)
    else:
        new_roast = DailyRoast(
            player_id=player_id,
            roast_date=today,
            roast_text=roast_text,
        )
        db.add(new_roast)

    db.commit()
    return {"roast": roast_text}

    return {"roast": roast_text}

@app.get("/api/leaderboard")
def get_leaderboard(db: Session_ = Depends(get_db),
                    sort_by: str = Query("pnl", pattern="(pnl|points|cash_in)$")):
    """Get leaderboard ranked by PNL, reward points, or cash in"""
    players = db.query(Player).all()
    result = []
    house_total_pnl = 0.0  # Negative of sum of player PNLs (house perspective)

    for p in players:
        pnl = get_pnl(p)
        house_total_pnl -= pnl
        result.append({
            "id": p.id,
            "name": p.name,
            # Deliberately no card_id: the board is public to everyone in the
            # room, and a card ID is what logs you in.
            "reward_points": p.reward_points,
            "total_cash_in": p.total_cash_in,
            "total_cash_out": p.total_cash_out,
            "pnl": pnl,
        })

    # Sort
    if sort_by == "pnl":
        result.sort(key=lambda x: x["pnl"], reverse=True)
    elif sort_by == "points":
        result.sort(key=lambda x: x["reward_points"], reverse=True)
    elif sort_by == "cash_in":
        result.sort(key=lambda x: x["total_cash_in"], reverse=True)

    return {
        "players": result,
        "house_pnl": house_total_pnl,  # Positive = house is up, Negative = house is down
    }

# ============================================================
# Routes - Auth
# ============================================================
@app.post("/api/admin/auth")
def admin_auth(request: AdminAuth):
    """Verify admin or worker PIN"""
    if request.role == "admin" and request.pin == ADMIN_PIN:
        return {"authenticated": True, "role": "admin"}
    if request.role == "worker" and request.pin == WORKER_PIN:
        return {"authenticated": True, "role": "worker"}
    raise HTTPException(status_code=401, detail="Invalid PIN")

# ============================================================
# Routes - Admin
# ============================================================
@app.post("/api/admin/register")
def register_player(request: RegisterRequest, db: Session_ = Depends(get_db), _: None = Depends(require_admin)):
    """Register a new player"""
    existing = db.query(Player).filter(Player.card_id == request.card_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Card already registered")

    if MAX_PLAYERS:
        count = db.query(Player).count()
        if count >= MAX_PLAYERS:
            raise HTTPException(
                status_code=409,
                detail=f"The house is full — {MAX_PLAYERS} seats, all taken. "
                       f"Remove a player or raise MAX_PLAYERS.")

    player = Player(card_id=request.card_id, name=request.name)
    db.add(player)
    db.commit()
    db.refresh(player)

    record_event(db, player.id, "registration", description=f"Registered as {request.name}")
    db.commit()

    return {"message": "Player registered", "player_id": player.id}

@app.post("/api/admin/deposit")
def record_deposit(request: DepositRequest, db: Session_ = Depends(get_db), _: None = Depends(require_admin)):
    """Record a cash deposit"""
    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    player.total_cash_in += request.amount
    reward_earned = request.amount * 100
    player.reward_points += reward_earned

    # Legacy transaction
    transaction = Transaction(
        player_id=player.id,
        amount=request.amount,
        transaction_type="deposit",
        description=request.description or f"Deposit ${request.amount} (+{reward_earned:.0f} pts)",
    )
    db.add(transaction)

    # New event log
    record_event(
        db, player.id, "deposit",
        cash_amount=request.amount,
        points_delta=reward_earned,
        pnl_impact=request.amount,  # Deposit = player cash_in = PNL goes up (player more negative from house perspective)
        description=request.description or f"Deposit ${request.amount} (+{reward_earned:.0f} pts)",
    )

    db.commit()
    db.refresh(player)

    pnl = get_pnl(player)
    return {
        "message": "Deposit recorded",
        "pnl": pnl,
        "reward_points": player.reward_points,
        "reward_earned": reward_earned,
        "player": player.name,
    }

@app.post("/api/admin/cashout")
def record_cashout(request: LossRequest, db: Session_ = Depends(get_db), _: None = Depends(require_admin)):
    """Record a cash-out"""
    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    amount = abs(request.amount)
    player.total_cash_out += amount

    transaction = Transaction(
        player_id=player.id,
        amount=-amount,
        transaction_type="cashout",
        description=request.description or f"Cash out ${amount}",
    )
    db.add(transaction)

    record_event(
        db, player.id, "cashout",
        cash_amount=amount,
        pnl_impact=-amount,  # Cashout = player cash_out = PNL goes down (player more positive)
        description=request.description or f"Cash out ${amount}",
    )

    db.commit()
    db.refresh(player)

    pnl = get_pnl(player)
    return {"message": "Cash out recorded", "pnl": pnl, "player": player.name}

@app.post("/api/admin/add_points")
def add_reward_points(request: AdjustmentRequest, db: Session_ = Depends(get_db), _: None = Depends(require_admin)):
    """Manually add reward points"""
    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    amount = abs(request.amount)
    player.reward_points += amount

    transaction = Transaction(
        player_id=player.id,
        amount=0,
        transaction_type="reward_add",
        description=request.description or f"Bonus points +{amount}",
    )
    db.add(transaction)

    record_event(
        db, player.id, "reward_add",
        points_delta=amount,
        description=request.description or f"Bonus points +{amount}",
    )

    db.commit()
    db.refresh(player)

    return {"message": "Points added", "reward_points": player.reward_points, "player": player.name}

@app.post("/api/admin/redeem_points")
def redeem_points(request: AdjustmentRequest, db: Session_ = Depends(get_db), _: None = Depends(require_admin)):
    """Redeem reward points"""
    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    amount = abs(request.amount)
    if player.reward_points < amount:
        raise HTTPException(status_code=400, detail="Insufficient points")

    player.reward_points -= amount

    transaction = Transaction(
        player_id=player.id,
        amount=0,
        transaction_type="reward_redeem",
        description=request.description or f"Redeemed {amount} points",
    )
    db.add(transaction)

    record_event(
        db, player.id, "reward_redeem",
        points_delta=-amount,
        description=request.description or f"Redeemed {amount} points",
    )

    db.commit()
    db.refresh(player)

    return {"message": "Points redeemed", "reward_points": player.reward_points, "player": player.name}

# ============================================================
# Routes - Payments (crypto-first deposit requests)
#
# Money moves person-to-person; this app only records the request and credits
# the player once a dealer confirms the funds actually landed.
# ============================================================
MAX_DEPOSIT_USD = float(os.getenv("MAX_DEPOSIT_USD", "10000"))

@app.get("/api/payments/methods")
def payment_methods():
    """Configured deposit methods. Empty list = operator hasn't set any addresses."""
    return {"methods": payments_lib.available_methods(),
            "max_deposit": MAX_DEPOSIT_USD}

@app.post("/api/payments/deposit-request")
def create_deposit_request(request: DepositRequestCreate, db: Session_ = Depends(get_db),
                   me: Player = Depends(current_player)):
    """Create a pending deposit and return everything needed to send the money."""
    require_self(me, request.card_id)
    method = payments_lib.get_method(request.method)
    if not method:
        raise HTTPException(status_code=400, detail="Payment method not available")
    if not (0 < request.amount <= MAX_DEPOSIT_USD):
        raise HTTPException(status_code=400,
                            detail=f"Amount must be between $0 and ${MAX_DEPOSIT_USD:,.0f}")

    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    amount = round(request.amount, 2)
    instructions = payments_lib.build_instructions(method, amount)
    # Unguessable key for the QR image, which cannot send an auth header.
    instructions["qr_key"] = secrets.token_urlsafe(16)

    req = PendingDeposit(
        player_id=player.id,
        amount=amount,
        method=request.method,
        instructions_json=instructions,
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    return {
        "request_id": req.id,
        "status": req.status,
        "player": player.name,
        "amount": amount,
        "points_on_confirm": amount * 100,
        "instructions": instructions,
        "qr_url": f"/api/payments/request/{req.id}/qr?k={instructions['qr_key']}",
    }

@app.get("/api/payments/request/{req_id}")
def get_deposit_request(req_id: int, db: Session_ = Depends(get_db),
                        me: Player = Depends(current_player)):
    """Poll a request. Instructions come from the stored snapshot so the address
    and quoted rate never drift after the player has been shown them."""
    req = db.query(PendingDeposit).filter(PendingDeposit.id == req_id).first()
    if not req or req.player_id != me.id:
        # Request ids are sequential, so an ungated lookup would let anyone walk
        # the list and read every deposit on the floor.
        raise HTTPException(status_code=404, detail="Request not found")
    return {
        "request_id": req.id,
        "status": req.status,
        "amount": float(req.amount),
        "method": req.method,
        "txid": req.txid,
        "instructions": req.instructions_json or {},
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None,
    }

@app.post("/api/payments/request/{req_id}/txid")
def attach_txid(req_id: int, body: TxidRequest, db: Session_ = Depends(get_db),
                me: Player = Depends(current_player)):
    """Player records the transaction hash so the dealer can verify on-chain."""
    req = db.query(PendingDeposit).filter(PendingDeposit.id == req_id).first()
    if not req or req.player_id != me.id:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {req.status}")

    txid = body.txid.strip()
    if not (6 <= len(txid) <= 200):
        raise HTTPException(status_code=400, detail="That doesn't look like a transaction ID")

    req.txid = txid
    db.commit()
    return {"message": "Transaction ID recorded — show the dealer to get credited",
            "request_id": req.id, "txid": txid}

@app.get("/api/payments/request/{req_id}/qr")
def deposit_request_qr(req_id: int, k: str = Query(None), db: Session_ = Depends(get_db)):
    """
    QR of the payment URI for this request.

    An <img src> cannot carry an Authorization header, so this one is gated by a
    per-request key handed out with the instructions rather than by the session.
    Sequential ids alone would let anyone render every deposit on the floor.
    """
    req = db.query(PendingDeposit).filter(PendingDeposit.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    expected = (req.instructions_json or {}).get("qr_key")
    if not expected or k != expected:
        raise HTTPException(status_code=404, detail="Request not found")

    data = (req.instructions_json or {}).get("uri")
    png = payments_lib.qr_png(data)
    if not png:
        raise HTTPException(status_code=503, detail="QR rendering unavailable")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})

@app.get("/api/admin/pending-deposits")
def list_pending_deposits(db: Session_ = Depends(get_db), _: bool = Depends(require_admin),
                          status: str = Query("pending", pattern="^(pending|confirmed|cancelled|all)$")):
    """Deposit requests awaiting a dealer's confirmation."""
    q = (db.query(PendingDeposit, Player)
           .join(Player, Player.id == PendingDeposit.player_id))
    if status != "all":
        q = q.filter(PendingDeposit.status == status)
    rows = q.order_by(PendingDeposit.created_at.desc()).limit(200).all()

    return {"requests": [{
        "id": r.PendingDeposit.id,
        "player": r.Player.name,
        "card_id": r.Player.card_id,
        "amount": float(r.PendingDeposit.amount),
        "method": r.PendingDeposit.method,
        "status": r.PendingDeposit.status,
        "txid": r.PendingDeposit.txid,
        "instructions": r.PendingDeposit.instructions_json or {},
        "created_at": r.PendingDeposit.created_at.isoformat() if r.PendingDeposit.created_at else None,
    } for r in rows]}

def _claim_pending(db: Session_, req_id: int, new_status: str) -> PendingDeposit:
    """Lock the row and flip it out of `pending`, so a double-click can't credit twice."""
    req = (db.query(PendingDeposit)
             .filter(PendingDeposit.id == req_id)
             .with_for_update()
             .first())
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != "pending":
        raise HTTPException(status_code=400, detail=f"Request already {req.status}")
    req.status = new_status
    req.resolved_at = datetime.now(timezone.utc)
    return req

@app.post("/api/admin/pending-deposits/{req_id}/confirm")
def confirm_pending_deposit(req_id: int, db: Session_ = Depends(get_db),
                            _: bool = Depends(require_admin)):
    """Dealer confirms the funds arrived — credits cash-in and reward points."""
    req = _claim_pending(db, req_id, "confirmed")
    player = db.query(Player).filter(Player.id == req.player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    amount = float(req.amount)
    reward_earned = amount * 100
    player.total_cash_in += amount
    player.reward_points += reward_earned

    instructions = req.instructions_json or {}
    label = instructions.get("label", req.method)
    record_event(
        db, player.id, "deposit",
        cash_amount=amount,
        points_delta=reward_earned,
        pnl_impact=amount,
        metadata={
            "method": req.method,
            "pending_deposit_id": req.id,
            "txid": req.txid,
            "crypto_amount": instructions.get("crypto_amount"),
            "symbol": instructions.get("symbol"),
            "price_usd": instructions.get("price_usd"),
        },
        description=f"Deposit ${amount:.2f} via {label} (+{reward_earned:.0f} pts)",
    )
    db.commit()
    db.refresh(player)

    return {"message": f"Confirmed ${amount:.2f} via {label}",
            "player": player.name,
            "reward_points": player.reward_points,
            "reward_earned": reward_earned}

@app.post("/api/admin/pending-deposits/{req_id}/cancel")
def cancel_pending_deposit(req_id: int, db: Session_ = Depends(get_db),
                           _: bool = Depends(require_admin)):
    """Dealer rejects a request — nothing is credited."""
    req = _claim_pending(db, req_id, "cancelled")
    db.commit()
    return {"message": "Request cancelled", "request_id": req.id}

# ============================================================
# Routes - Slots (provably fair; bets and wins are reward points, never cash)
# ============================================================
def _active_seed(db: Session_, player_id: int, lock: bool = False) -> SlotSeed:
    """Fetch the player's active seed pair, creating one on first play."""
    q = db.query(SlotSeed).filter(SlotSeed.player_id == player_id, SlotSeed.active.is_(True))
    if lock:
        q = q.with_for_update()
    seed = q.first()
    if seed:
        return seed

    server_seed = slots_engine.new_server_seed()
    seed = SlotSeed(
        player_id=player_id,
        server_seed=server_seed,
        server_seed_hash=slots_engine.seed_hash(server_seed),
        client_seed=slots_engine.new_client_seed(),
        nonce=0,
        active=True,
    )
    db.add(seed)
    db.commit()
    db.refresh(seed)
    return seed

def _seed_public(seed: SlotSeed) -> dict:
    """Everything about a seed pair that is safe to show before the reveal."""
    return {
        "server_seed_hash": seed.server_seed_hash,
        "client_seed": seed.client_seed,
        "nonce": seed.nonce,
        "created_at": seed.created_at.isoformat() if seed.created_at else None,
    }

@app.get("/api/slots/machines")
def get_slot_machines():
    return {"machines": slots_engine.machine_list()}

@app.get("/api/slots/seed")
def get_slot_seed(card_id: str = Query(...), db: Session_ = Depends(get_db),
                   me: Player = Depends(current_player)):
    """
    The fairness commitment. `server_seed_hash` is published before any spin;
    the matching secret is only revealed when the seed is rotated.
    """
    require_self(me, card_id)
    player = db.query(Player).filter(Player.card_id == card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    seed = _active_seed(db, player.id)
    return {"player": player.name, "seed": _seed_public(seed)}

@app.post("/api/slots/seed/rotate")
def rotate_slot_seed(request: SlotSeedRotateRequest, db: Session_ = Depends(get_db),
                   me: Player = Depends(current_player)):
    """
    Reveal the current server seed and start a fresh one.

    Rotating is how a player audits the house: once the old seed is public,
    every spin made under it can be recomputed with /api/slots/verify.
    """
    require_self(me, request.card_id)
    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    client_seed = (request.client_seed or "").strip()
    if client_seed and not (1 <= len(client_seed) <= 64):
        raise HTTPException(status_code=400, detail="Client seed must be 1-64 characters")

    old = _active_seed(db, player.id, lock=True)
    old.active = False
    old.revealed_at = datetime.now(timezone.utc)

    server_seed = slots_engine.new_server_seed()
    new = SlotSeed(
        player_id=player.id,
        server_seed=server_seed,
        server_seed_hash=slots_engine.seed_hash(server_seed),
        client_seed=client_seed or slots_engine.new_client_seed(),
        nonce=0,
        active=True,
    )
    db.add(new)
    db.commit()
    db.refresh(new)

    return {
        "revealed": {
            "server_seed": old.server_seed,
            "server_seed_hash": old.server_seed_hash,
            "client_seed": old.client_seed,
            "spins_made": old.nonce,
        },
        "new_seed": _seed_public(new),
        "how_to_verify": ("SHA-256 of the revealed server_seed must equal the "
                          "server_seed_hash you were shown before playing. Then POST "
                          "/api/slots/verify with any nonce from 0 to spins_made-1 to "
                          "recompute that spin."),
    }

@app.post("/api/slots/spin")
def slot_spin(request: SlotSpinRequest, db: Session_ = Depends(get_db),
                   me: Player = Depends(current_player)):
    """One provably fair spin, wagering reward points."""
    require_self(me, request.card_id)
    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    if request.machine not in slots_engine.MACHINES:
        raise HTTPException(status_code=400, detail="Unknown machine")

    bet = round(float(request.bet), 2)
    if bet <= 0:
        raise HTTPException(status_code=400, detail="Bet must be positive")
    if player.reward_points < bet:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient points. Need {bet:.0f}, have {player.reward_points:.0f}")

    # Lock the seed row so two concurrent spins can never reuse a nonce.
    seed = _active_seed(db, player.id, lock=True)
    nonce = seed.nonce

    try:
        result = slots_engine.spin(request.machine, bet, seed.server_seed,
                                   seed.client_seed, nonce)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    seed.nonce = nonce + 1

    net = result["win"] - bet
    player.reward_points += net

    machine_name = slots_engine.MACHINES[request.machine]["name"]
    outcome = result["detail"] or "No win"
    record_event(
        db, player.id, "slot_spin",
        points_delta=net,
        metadata={
            "machine": request.machine,
            "bet": bet,
            "win": result["win"],
            "grid": result["grid"],
            "nonce": nonce,
            "client_seed": seed.client_seed,
            "server_seed_hash": seed.server_seed_hash,
        },
        description=f"{machine_name}: bet {bet:.0f}, {outcome} ({net:+.0f} pts)",
    )
    db.commit()
    db.refresh(player)

    result["net"] = net
    result["reward_points"] = player.reward_points
    result["next_nonce"] = seed.nonce
    return result

@app.post("/api/slots/verify")
def verify_slot_spin(request: SlotVerifyRequest):
    """
    Recompute any past spin from its revealed seed. Stateless and public — run it
    here, or run slots.verify_spin() yourself from the source.
    """
    try:
        return slots_engine.verify_spin(request.machine, request.bet,
                                        request.server_seed, request.client_seed,
                                        request.nonce)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/players/{player_id}/slot-history")
def get_slot_history(player_id: int, db: Session_ = Depends(get_db),
                     limit: int = Query(50, ge=1, le=200),
                   me: Player = Depends(current_player)):
    """Past spins with the seed context needed to audit each one."""
    if me.id != player_id:
        raise HTTPException(status_code=403, detail="That is not your ledger")
    events = (db.query(PlayerEvent)
                .filter(PlayerEvent.player_id == player_id,
                        PlayerEvent.event_type == "slot_spin")
                .order_by(PlayerEvent.created_at.desc())
                .limit(limit).all())

    return {"spins": [{
        "id": e.id,
        "points_delta": float(e.points_delta),
        "description": e.description,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        **(e.metadata_json or {}),
    } for e in events]}

# ============================================================
# Routes - Table games
# ============================================================
# Tables share the slots' seed pair on purpose: one rotation reveals the secret
# behind every game the player has touched, so a single audit covers the floor.

def _game_def(key: str) -> dict:
    """Games live in two engines; the routes should not care which."""
    if key in tables_engine.TABLES:
        return tables_engine.TABLES[key]
    if key in arcade_engine.GAMES:
        return arcade_engine.GAMES[key]
    raise HTTPException(status_code=400, detail="Unknown game")


def _is_instant(key: str) -> bool:
    return key in tables_engine.INSTANT_GAMES or key in arcade_engine.INSTANT_GAMES


def _is_round(key: str) -> bool:
    return key in tables_engine.ROUND_GAMES or key in arcade_engine.ROUND_GAMES


def _elapsed_seconds(state: dict) -> float:
    """Seconds since a crash round started, measured entirely on the server."""
    started = state.get("started_at")
    if not started:
        return 0.0
    began = datetime.fromisoformat(started)
    if began.tzinfo is None:
        began = began.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - began).total_seconds())


def _expire_stale_crash(db: Session_, player: Player) -> None:
    """
    Settle any crash round whose rocket has already gone.

    A player who closes the tab mid-flight leaves an "active" round behind. It
    busted at a point fixed before the round began, so the outcome is not in
    doubt — but leaving it open would block the next round and hold the stake.
    """
    rounds = (db.query(TableRound)
                .filter(TableRound.player_id == player.id,
                        TableRound.game == "crash",
                        TableRound.status == "active")
                .all())
    for rnd in rounds:
        state = dict(rnd.state_json or {})
        if state.get("stage") != "flying":
            continue
        bust_at = arcade_engine.crash_time_to(state.get("bust", 1.0))
        if _elapsed_seconds(state) >= bust_at:
            _settle_round(db, player, rnd, arcade_engine.crash_expire(state))


def _table_player(db: Session_, card_id: str) -> Player:
    player = db.query(Player).filter(Player.card_id == card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")
    return player


def _check_bet(game: dict, bet: float, player: Player, need: float = None) -> float:
    bet = round(float(bet), 2)
    if bet <= 0:
        raise HTTPException(status_code=400, detail="Bet must be positive")
    if bet < game["min_bet"] or bet > game["max_bet"]:
        raise HTTPException(
            status_code=400,
            detail=f"{game['name']} takes {game['min_bet']:,} to {game['max_bet']:,} per hand")
    required = bet if need is None else need
    if player.reward_points < required:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient points. Need {required:.0f}, have {player.reward_points:.0f}")
    return bet


def _round_public(rnd: TableRound, state: dict) -> dict:
    if rnd.game == "blackjack":
        view = tables_engine.blackjack_public(state)
    elif rnd.game == "mississippi":
        view = tables_engine.mississippi_public(state)
    elif rnd.game == "crash":
        view = arcade_engine.crash_public(state, _elapsed_seconds(state))
    elif rnd.game == "mines":
        view = arcade_engine.mines_public(state)
    else:
        raise HTTPException(status_code=400, detail="Unknown game")
    return {
        "round_id": rnd.id,
        "game": rnd.game,
        "status": rnd.status,
        "nonce": rnd.nonce,
        "client_seed": rnd.client_seed,
        "server_seed_hash": rnd.server_seed_hash,
        **view,
    }


def _settle_round(db: Session_, player: Player, rnd: TableRound, state: dict) -> None:
    """Credit the payout, close the round, and write one auditable event."""
    payout = round(float(state.get("payout", 0.0)), 2)
    extra_wagered = round(float(state["wagered"]), 2) - rnd.wagered
    if extra_wagered > 0:
        # Raises and doubles taken during the hand are debited at settle time,
        # having already been checked against the balance when they were made.
        player.reward_points -= extra_wagered
        rnd.wagered = round(float(state["wagered"]), 2)

    player.reward_points += payout
    rnd.payout = payout
    rnd.status = "settled"
    rnd.settled_at = datetime.now(timezone.utc)
    rnd.state_json = state

    net = payout - rnd.wagered
    game = _game_def(rnd.game)
    if rnd.game == "blackjack":
        outcome = ", ".join(h["result"] or "?" for h in state["hands"])
    elif rnd.game == "mississippi":
        outcome = state.get("label", "?")
    elif rnd.game == "crash":
        outcome = (f"cashed out at {state['cashed_at']:.2f}x" if state.get("cashed_at")
                   else f"busted at {state['bust']:.2f}x")
    else:
        outcome = (f"hit a mine on pick {len(state['picked'])}" if state.get("hit") is not None
                   else f"cashed out after {len(state['picked'])} safe")

    record_event(
        db, player.id, "table_round",
        points_delta=net,
        metadata={
            "game": rnd.game, "round_id": rnd.id,
            "wagered": rnd.wagered, "payout": payout,
            "nonce": rnd.nonce, "client_seed": rnd.client_seed,
            "server_seed_hash": rnd.server_seed_hash,
            "state": state,
        },
        description=f"{game['name']}: wagered {rnd.wagered:.0f}, {outcome} ({net:+.0f} pts)",
    )


@app.get("/api/tables/games")
def get_table_games():
    return {"games": tables_engine.table_list() + arcade_engine.game_list()}


@app.post("/api/tables/deal")
def table_deal(request: TableDealRequest, db: Session_ = Depends(get_db),
                   me: Player = Depends(current_player)):
    """
    Start a hand. Baccarat and fan-tan settle right here; blackjack and
    Mississippi Stud return a live round to act on.
    """
    require_self(me, request.card_id)
    game = _game_def(request.game)
    player = _table_player(db, request.card_id)

    # Mississippi Stud can be raised up to 9x the ante beyond it, so make sure
    # the player can at least cover the ante plus a 1x on every street. Plinko
    # charges the stake once per ball, so N balls need N times the stake.
    need = None
    if request.game == "mississippi":
        need = request.bet * 4
    elif request.game == "plinko":
        balls = max(1, min(int(request.balls or 1), arcade_engine.PLINKO_MAX_BALLS))
        need = request.bet * balls
    bet = _check_bet(game, request.bet, player, need=need)

    # One live hand at a time. Without this a player could deal blackjack, walk
    # off, deal a stud hand, and leave the first one open with its points already
    # debited — and the lobby only ever surfaces the most recent open round, so
    # those points would quietly strand. Instant games settle in this call and
    # can never strand anything, so they stay available.
    _expire_stale_crash(db, player)
    if _is_round(request.game):
        open_round = (db.query(TableRound)
                        .filter(TableRound.player_id == player.id,
                                TableRound.status == "active")
                        .order_by(TableRound.id.desc()).first())
        if open_round:
            raise HTTPException(
                status_code=409,
                detail=f"Finish your open {_game_def(open_round.game)['name']} "
                       f"round first — {open_round.wagered:.0f} points are still on it.")

    # Lock the seed row so two concurrent hands can never share a nonce.
    seed = _active_seed(db, player.id, lock=True)
    nonce = seed.nonce
    seed.nonce = nonce + 1

    if _is_instant(request.game):
        try:
            if request.game == "plinko":
                balls = max(1, min(int(request.balls or 1), arcade_engine.PLINKO_MAX_BALLS))
                # Each ball is its own bet at its own nonce, so each is separately
                # verifiable. The seed row is already locked, so they cannot collide.
                drops = []
                for b in range(balls):
                    d = arcade_engine.plinko_drop(
                        request.risk or "medium", seed.server_seed, seed.client_seed, nonce + b)
                    drops.append(arcade_engine.plinko_settle(d, bet))
                seed.nonce = nonce + balls
                total_payout = round(sum(d["payout"] for d in drops), 2)
                settled = {"payout": total_payout,
                           "verdict": ("win" if total_payout > bet * balls
                                       else "push" if total_payout == bet * balls else "lose")}
                detail = {"balls": balls, "drops": drops, "bet_per_ball": bet,
                          **drops[0], "payout": total_payout}
                bet = round(bet * balls, 2)
            elif request.game == "baccarat":
                deck = tables_engine.deck_for("baccarat", seed.server_seed, seed.client_seed, nonce)
                deal = tables_engine.baccarat_deal(deck)
                settled = tables_engine.baccarat_settle(deal, request.bet_type or "player", bet)
                detail = {**deal, **settled}
            else:
                draw = tables_engine.fan_tan_draw(seed.server_seed, seed.client_seed, nonce)
                settled = tables_engine.fan_tan_settle(
                    draw, request.bet_type or "fan", request.picks or [1], bet)
                detail = settled
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        net = settled["payout"] - bet
        player.reward_points += net
        record_event(
            db, player.id, "table_round",
            points_delta=net,
            metadata={"game": request.game, "bet": bet, "bet_type": request.bet_type,
                      "nonce": nonce, "client_seed": seed.client_seed,
                      "server_seed_hash": seed.server_seed_hash, "result": detail},
            description=f"{game['name']}: bet {bet:.0f} on {request.bet_type}, "
                        f"{settled['verdict']} ({net:+.0f} pts)",
        )
        db.commit()
        db.refresh(player)
        return {
            "game": request.game, "settled": True, "bet": bet,
            "nonce": nonce, "client_seed": seed.client_seed,
            "server_seed_hash": seed.server_seed_hash,
            "reward_points": player.reward_points, "net": net,
            **detail,
        }

    # --- live rounds ---
    try:
        if request.game == "blackjack":
            deck = tables_engine.deck_for(request.game, seed.server_seed, seed.client_seed, nonce)
            state = tables_engine.blackjack_start(deck, bet)
        elif request.game == "mississippi":
            deck = tables_engine.deck_for(request.game, seed.server_seed, seed.client_seed, nonce)
            state = tables_engine.mississippi_start(deck, bet)
        elif request.game == "crash":
            bust = arcade_engine.crash_point(seed.server_seed, seed.client_seed, nonce)
            state = arcade_engine.crash_start(bust, bet, request.target)
            # Stamped by the server, and the only clock the cash-out is measured
            # against — a client-supplied multiplier would just claim the bust.
            state["started_at"] = datetime.now(timezone.utc).isoformat()
        else:
            mine_count = request.mines or arcade_engine.MINES["default_mines"]
            layout = arcade_engine.mines_layout(
                mine_count, seed.server_seed, seed.client_seed, nonce)
            state = arcade_engine.mines_start(layout, mine_count, bet)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    rnd = TableRound(
        player_id=player.id, game=request.game, nonce=nonce,
        client_seed=seed.client_seed, server_seed_hash=seed.server_seed_hash,
        state_json=state, wagered=bet, status="active",
    )
    # The opening wager leaves the balance immediately, win or lose.
    player.reward_points -= bet
    db.add(rnd)
    db.flush()

    if state["stage"] in ("settled", "folded"):
        _settle_round(db, player, rnd, state)

    db.commit()
    db.refresh(rnd)
    db.refresh(player)

    out = _round_public(rnd, rnd.state_json)
    out["reward_points"] = player.reward_points
    return out


@app.post("/api/tables/action")
def table_action(request: TableActionRequest, db: Session_ = Depends(get_db),
                   me: Player = Depends(current_player)):
    """Hit, stand, double, split — or in Mississippi Stud, raise or fold."""
    require_self(me, request.card_id)
    player = _table_player(db, request.card_id)

    rnd = (db.query(TableRound)
             .filter(TableRound.id == request.round_id)
             .with_for_update()
             .first())
    if not rnd or rnd.player_id != player.id:
        raise HTTPException(status_code=404, detail="Round not found")
    if rnd.status != "active":
        raise HTTPException(status_code=400, detail="That hand is already finished")

    seed = db.query(SlotSeed).filter(
        SlotSeed.player_id == player.id,
        SlotSeed.server_seed_hash == rnd.server_seed_hash).first()
    if not seed:
        raise HTTPException(status_code=409,
                            detail="The seed for this hand was rotated — it can no longer be played out")

    # Re-derive the exact shoe this hand was dealt from. Nothing about the cards
    # is read back from the database. Arcade rounds have no shoe.
    deck = (tables_engine.deck_for(rnd.game, seed.server_seed, seed.client_seed, rnd.nonce)
            if rnd.game in tables_engine.ROUND_GAMES else None)
    # Deep copy, not dict(): a shallow copy shares the nested lists with the
    # value SQLAlchemy loaded, so mutating them changes the "old" value too and
    # the assignment below looks like a no-op. A mines pick that only appends to
    # state["picked"] would then never be written back at all.
    state = copy.deepcopy(rnd.state_json or {})

    # Any raise has to be affordable before it is applied.
    prospective = 0.0
    if rnd.game == "blackjack" and request.action in ("double", "split"):
        prospective = state["hands"][state["active"]]["bet"]
    elif rnd.game == "mississippi" and request.action == "raise":
        prospective = state["ante"] * (request.multiple or 0)
    if prospective and player.reward_points < prospective:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient points. Need {prospective:.0f}, have {player.reward_points:.0f}")

    try:
        if rnd.game == "blackjack":
            state = tables_engine.blackjack_act(state, deck, request.action)
        elif rnd.game == "mississippi":
            state = tables_engine.mississippi_act(state, deck, request.action,
                                                  request.multiple or 0)
        elif rnd.game == "crash":
            if request.action != "cashout":
                raise ValueError("The only move in crash is to cash out")
            # Elapsed time comes from the server's own stamp, never the client.
            state = arcade_engine.crash_cash_out(state, _elapsed_seconds(state))
        elif rnd.game == "mines":
            if request.action == "cashout":
                state = arcade_engine.mines_cash_out(state)
            elif request.action == "pick":
                state = arcade_engine.mines_pick(state, request.tile)
            else:
                raise ValueError("Unknown move")
        else:
            raise ValueError("Unknown game")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if state["stage"] in ("settled", "folded"):
        _settle_round(db, player, rnd, state)
    else:
        # Debit raises as they are made, so the balance on screen stays honest.
        extra = round(float(state["wagered"]), 2) - rnd.wagered
        if extra > 0:
            player.reward_points -= extra
            rnd.wagered = round(float(state["wagered"]), 2)
        rnd.state_json = state

    db.commit()
    db.refresh(rnd)
    db.refresh(player)

    out = _round_public(rnd, rnd.state_json)
    out["reward_points"] = player.reward_points
    return out


@app.get("/api/tables/round/{round_id}")
def get_table_round(round_id: int, card_id: str = Query(...), db: Session_ = Depends(get_db),
                   me: Player = Depends(current_player)):
    """Recover a hand that was interrupted — a closed tab, a dead phone."""
    require_self(me, card_id)
    player = _table_player(db, card_id)
    # Polling is how the client learns it busted, so this has to be a point where
    # a finished rocket actually settles.
    _expire_stale_crash(db, player)
    db.commit()
    rnd = db.query(TableRound).filter(TableRound.id == round_id).first()
    if not rnd or rnd.player_id != player.id:
        raise HTTPException(status_code=404, detail="Round not found")
    out = _round_public(rnd, rnd.state_json or {})
    out["reward_points"] = player.reward_points
    return out


@app.get("/api/tables/active")
def get_active_round(card_id: str = Query(...), db: Session_ = Depends(get_db),
                   me: Player = Depends(current_player)):
    """The player's unfinished hand, if they walked away from one."""
    require_self(me, card_id)
    player = _table_player(db, card_id)
    _expire_stale_crash(db, player)
    db.commit()
    rnd = (db.query(TableRound)
             .filter(TableRound.player_id == player.id, TableRound.status == "active")
             .order_by(TableRound.id.desc()).first())
    if not rnd:
        return {"active": None}
    return {"active": _round_public(rnd, rnd.state_json or {})}


@app.get("/api/players/{player_id}/table-history")
def get_table_history(player_id: int, db: Session_ = Depends(get_db),
                      limit: int = Query(50, ge=1, le=200),
                   me: Player = Depends(current_player)):
    """Past hands with the seed context needed to audit each one."""
    if me.id != player_id:
        raise HTTPException(status_code=403, detail="That is not your ledger")
    events = (db.query(PlayerEvent)
                .filter(PlayerEvent.player_id == player_id,
                        PlayerEvent.event_type == "table_round")
                .order_by(PlayerEvent.created_at.desc())
                .limit(limit).all())
    return {"rounds": [{
        "id": e.id,
        "points_delta": float(e.points_delta),
        "description": e.description,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        **(e.metadata_json or {}),
    } for e in events]}


# ============================================================
# Routes - Worker
# ============================================================
@app.get("/api/worker/rewards")
def list_preset_rewards():
    """List preset rewards"""
    return [{"key": k, **v} for k, v in PRESET_REWARDS.items()]

@app.post("/api/worker/redeem")
def worker_redeem(request: WorkerRedeemRequest, db: Session_ = Depends(get_db),
                  x_worker_pin: str = Header(None)):
    if x_worker_pin != WORKER_PIN:
        raise HTTPException(status_code=401, detail="Worker PIN required")
    """Worker redeems points for a preset reward"""
    if request.reward_key not in PRESET_REWARDS:
        raise HTTPException(status_code=400, detail="Invalid reward type")

    reward = PRESET_REWARDS[request.reward_key]
    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    if player.reward_points < reward["points"]:
        raise HTTPException(status_code=400, detail=f"Insufficient points. Need {reward['points']}, have {player.reward_points:.0f}")

    player.reward_points -= reward["points"]

    transaction = Transaction(
        player_id=player.id,
        amount=0,
        transaction_type="reward_redeem",
        description=f"Redeemed: {reward['name']} (-{reward['points']} pts)",
    )
    db.add(transaction)

    record_event(
        db, player.id, "reward_redeem",
        points_delta=-reward["points"],
        metadata_json={"reward_key": request.reward_key, "reward_name": reward["name"]},
        description=f"Redeemed: {reward['name']} (-{reward['points']} pts)",
    )

    db.commit()
    db.refresh(player)

    return {
        "message": f"Redeemed: {reward['name']}",
        "reward_points": player.reward_points,
        "player": player.name,
    }

# ============================================================
# Routes - Admin List
# ============================================================
@app.get("/api/admin/players")
def list_players(db: Session_ = Depends(get_db), _: None = Depends(require_admin)):
    """List all players"""
    players = db.query(Player).all()
    result = []
    for p in players:
        pnl = get_pnl(p)
        result.append({
            "id": p.id,
            "card_id": p.card_id,
            "name": p.name,
            "reward_points": p.reward_points,
            "total_cash_in": p.total_cash_in,
            "total_cash_out": p.total_cash_out,
            "pnl": pnl,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return result

@app.get("/api/admin/transactions/{player_id}")
def get_player_transactions(player_id: int, db: Session_ = Depends(get_db), _: None = Depends(require_admin)):
    """Get legacy transaction history"""
    transactions = db.query(Transaction).filter(
        Transaction.player_id == player_id
    ).order_by(Transaction.created_at.desc()).limit(50).all()

    return [{
        "id": t.id,
        "amount": t.amount,
        "type": t.transaction_type,
        "description": t.description,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in transactions]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
