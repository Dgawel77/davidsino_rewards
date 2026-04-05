"""
Davidsino Rewards - FastAPI Backend
Tracks player points, deposits, PNL, and event history for the casino loyalty program.
"""
import os
import json
from datetime import datetime, timezone, timedelta, date
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, Text, Numeric, func, desc, asc
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.dialects.postgresql import JSONB
from pydantic import BaseModel
from typing import Optional, List

load_dotenv()

# ============================================================
# Database Setup
# ============================================================
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://davidsino:davidsino_pass@localhost:5432/davidsino")
ADMIN_PIN = os.getenv("ADMIN_PIN", "1234")
WORKER_PIN = os.getenv("WORKER_PIN", "5678")

engine = create_engine(DATABASE_URL)
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
    metadata_json = Column(JSONB, default=dict)
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
