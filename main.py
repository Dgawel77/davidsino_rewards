"""
Davidsino Rewards - FastAPI Backend
Tracks player points, deposits, PNL, and event history for the casino loyalty program.
"""
import os
import json
from datetime import datetime, timezone, timedelta, date
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Depends, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, Numeric, Boolean, JSON, func, desc, asc
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.dialects.postgresql import JSONB
from pydantic import BaseModel
from typing import Optional, List

import slots as slots_engine
import payments as payments_lib
import tables as tables_engine

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
    # Instant games (baccarat, fan-tan) settle in this one call, so they carry
    # their bet selection with the deal.
    bet_type: Optional[str] = None
    picks: Optional[List[int]] = None

class TableActionRequest(BaseModel):
    card_id: str
    round_id: int
    action: str
    multiple: Optional[int] = None

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

app.mount("/static", StaticFiles(directory="static"), name="static")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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

def record_event(db: Session, player_id: int, event_type: str, cash_amount: float = 0,
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
@app.get("/")
def serve_frontend():
    return FileResponse("static/index.html")

@app.get("/api/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.post("/api/scan")
def scan_card(request: ScanRequest, db: Session = Depends(get_db)):
    """Scan a card and return player info"""
    player = db.query(Player).filter(Player.card_id == request.card_id).first()
    if not player:
        return {"registered": False, "card_id": request.card_id}

    pnl = get_pnl(player)
    return {
        "registered": True,
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

@app.get("/api/players/search")
def search_players(query: str = Query(..., min_length=1), db: Session = Depends(get_db)):
    """Search players by name or card_id (partial match)"""
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
def get_player_history(player_id: int, db: Session = Depends(get_db),
                       limit: int = Query(100, ge=1, le=500),
                       offset: int = Query(0, ge=0)):
    """Get paginated event history for a player"""
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
def get_daily_pnl(player_id: int, db: Session = Depends(get_db)):
    """Get daily PNL summary with running total for a player"""
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
def get_player_summary(player_id: int, db: Session = Depends(get_db)):
    """Get full account summary including roast"""
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
def generate_roast(player_id: int, db: Session = Depends(get_db)):
    """Generate and cache a new AI roast for a player"""
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
def get_leaderboard(db: Session = Depends(get_db),
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
            "card_id": p.card_id,
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
def register_player(request: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new player"""
    existing = db.query(Player).filter(Player.card_id == request.card_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Card already registered")

    player = Player(card_id=request.card_id, name=request.name)
    db.add(player)
    db.commit()
    db.refresh(player)

    record_event(db, player.id, "registration", description=f"Registered as {request.name}")
    db.commit()

    return {"message": "Player registered", "player_id": player.id}

@app.post("/api/admin/deposit")
def record_deposit(request: DepositRequest, db: Session = Depends(get_db)):
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
def record_cashout(request: LossRequest, db: Session = Depends(get_db)):
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
def add_reward_points(request: AdjustmentRequest, db: Session = Depends(get_db)):
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
def redeem_points(request: AdjustmentRequest, db: Session = Depends(get_db)):
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
def create_deposit_request(request: DepositRequestCreate, db: Session = Depends(get_db)):
    """Create a pending deposit and return everything needed to send the money."""
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
        "qr_url": f"/api/payments/request/{req.id}/qr",
    }

@app.get("/api/payments/request/{req_id}")
def get_deposit_request(req_id: int, db: Session = Depends(get_db)):
    """Poll a request. Instructions come from the stored snapshot so the address
    and quoted rate never drift after the player has been shown them."""
    req = db.query(PendingDeposit).filter(PendingDeposit.id == req_id).first()
    if not req:
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
def attach_txid(req_id: int, body: TxidRequest, db: Session = Depends(get_db)):
    """Player records the transaction hash so the dealer can verify on-chain."""
    req = db.query(PendingDeposit).filter(PendingDeposit.id == req_id).first()
    if not req:
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
def deposit_request_qr(req_id: int, db: Session = Depends(get_db)):
    """QR of the payment URI for this request (address + exact amount when known)."""
    req = db.query(PendingDeposit).filter(PendingDeposit.id == req_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    data = (req.instructions_json or {}).get("uri")
    png = payments_lib.qr_png(data)
    if not png:
        raise HTTPException(status_code=503, detail="QR rendering unavailable")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})

@app.get("/api/admin/pending-deposits")
def list_pending_deposits(db: Session = Depends(get_db), _: bool = Depends(require_admin),
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

def _claim_pending(db: Session, req_id: int, new_status: str) -> PendingDeposit:
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
def confirm_pending_deposit(req_id: int, db: Session = Depends(get_db),
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
def cancel_pending_deposit(req_id: int, db: Session = Depends(get_db),
                           _: bool = Depends(require_admin)):
    """Dealer rejects a request — nothing is credited."""
    req = _claim_pending(db, req_id, "cancelled")
    db.commit()
    return {"message": "Request cancelled", "request_id": req.id}

# ============================================================
# Routes - Slots (provably fair; bets and wins are reward points, never cash)
# ============================================================
def _active_seed(db: Session, player_id: int, lock: bool = False) -> SlotSeed:
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
def get_slot_seed(card_id: str = Query(...), db: Session = Depends(get_db)):
    """
    The fairness commitment. `server_seed_hash` is published before any spin;
    the matching secret is only revealed when the seed is rotated.
    """
    player = db.query(Player).filter(Player.card_id == card_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    seed = _active_seed(db, player.id)
    return {"player": player.name, "seed": _seed_public(seed)}

@app.post("/api/slots/seed/rotate")
def rotate_slot_seed(request: SlotSeedRotateRequest, db: Session = Depends(get_db)):
    """
    Reveal the current server seed and start a fresh one.

    Rotating is how a player audits the house: once the old seed is public,
    every spin made under it can be recomputed with /api/slots/verify.
    """
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
def slot_spin(request: SlotSpinRequest, db: Session = Depends(get_db)):
    """One provably fair spin, wagering reward points."""
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
def get_slot_history(player_id: int, db: Session = Depends(get_db),
                     limit: int = Query(50, ge=1, le=200)):
    """Past spins with the seed context needed to audit each one."""
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

def _table_player(db: Session, card_id: str) -> Player:
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
    else:
        view = tables_engine.mississippi_public(state)
    return {
        "round_id": rnd.id,
        "game": rnd.game,
        "status": rnd.status,
        "nonce": rnd.nonce,
        "client_seed": rnd.client_seed,
        "server_seed_hash": rnd.server_seed_hash,
        **view,
    }


def _settle_round(db: Session, player: Player, rnd: TableRound, state: dict) -> None:
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
    game = tables_engine.TABLES[rnd.game]
    if rnd.game == "blackjack":
        outcome = ", ".join(h["result"] or "?" for h in state["hands"])
    else:
        outcome = state.get("label", "?")

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
    return {"games": tables_engine.table_list()}


@app.post("/api/tables/deal")
def table_deal(request: TableDealRequest, db: Session = Depends(get_db)):
    """
    Start a hand. Baccarat and fan-tan settle right here; blackjack and
    Mississippi Stud return a live round to act on.
    """
    if request.game not in tables_engine.TABLES:
        raise HTTPException(status_code=400, detail="Unknown game")
    game = tables_engine.TABLES[request.game]
    player = _table_player(db, request.card_id)

    # Mississippi Stud can be raised up to 9x the ante beyond it, so make sure
    # the player can at least cover the ante plus a 1x on every street.
    upfront = request.bet * 4 if request.game == "mississippi" else request.bet
    bet = _check_bet(game, request.bet, player, need=upfront if request.game == "mississippi" else None)

    # Lock the seed row so two concurrent hands can never share a nonce.
    seed = _active_seed(db, player.id, lock=True)
    nonce = seed.nonce
    seed.nonce = nonce + 1

    if request.game in tables_engine.INSTANT_GAMES:
        try:
            if request.game == "baccarat":
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
    deck = tables_engine.deck_for(request.game, seed.server_seed, seed.client_seed, nonce)
    if request.game == "blackjack":
        state = tables_engine.blackjack_start(deck, bet)
    else:
        state = tables_engine.mississippi_start(deck, bet)

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
def table_action(request: TableActionRequest, db: Session = Depends(get_db)):
    """Hit, stand, double, split — or in Mississippi Stud, raise or fold."""
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
    # is read back from the database.
    deck = tables_engine.deck_for(rnd.game, seed.server_seed, seed.client_seed, rnd.nonce)
    state = dict(rnd.state_json or {})

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
        else:
            state = tables_engine.mississippi_act(state, deck, request.action,
                                                  request.multiple or 0)
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
def get_table_round(round_id: int, card_id: str = Query(...), db: Session = Depends(get_db)):
    """Recover a hand that was interrupted — a closed tab, a dead phone."""
    player = _table_player(db, card_id)
    rnd = db.query(TableRound).filter(TableRound.id == round_id).first()
    if not rnd or rnd.player_id != player.id:
        raise HTTPException(status_code=404, detail="Round not found")
    out = _round_public(rnd, rnd.state_json or {})
    out["reward_points"] = player.reward_points
    return out


@app.get("/api/tables/active")
def get_active_round(card_id: str = Query(...), db: Session = Depends(get_db)):
    """The player's unfinished hand, if they walked away from one."""
    player = _table_player(db, card_id)
    rnd = (db.query(TableRound)
             .filter(TableRound.player_id == player.id, TableRound.status == "active")
             .order_by(TableRound.id.desc()).first())
    if not rnd:
        return {"active": None}
    return {"active": _round_public(rnd, rnd.state_json or {})}


@app.get("/api/players/{player_id}/table-history")
def get_table_history(player_id: int, db: Session = Depends(get_db),
                      limit: int = Query(50, ge=1, le=200)):
    """Past hands with the seed context needed to audit each one."""
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
def worker_redeem(request: WorkerRedeemRequest, db: Session = Depends(get_db)):
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
def list_players(db: Session = Depends(get_db)):
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
def get_player_transactions(player_id: int, db: Session = Depends(get_db)):
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
