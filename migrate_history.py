"""
Migration: Add player_events table and backfill from transactions.
Run once after main.py is updated with the new schema.
"""
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://davidsino:davidsino_pass@localhost:5432/davidsino")

engine = create_engine(DATABASE_URL)

def run_migration():
    with engine.connect() as conn:
        # Check if player_events already exists
        result = conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'player_events'
            );
        """))
        if result.scalar():
            print("player_events table already exists, skipping creation")
        else:
            print("Creating player_events table...")
            conn.execute(text("""
                CREATE TABLE player_events (
                    id SERIAL PRIMARY KEY,
                    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    event_type VARCHAR(50) NOT NULL,
                    cash_amount NUMERIC(12,2) DEFAULT 0,
                    points_delta NUMERIC(12,2) DEFAULT 0,
                    pnl_impact NUMERIC(12,2) DEFAULT 0,
                    metadata JSONB DEFAULT '{}',
                    description TEXT,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
                CREATE INDEX idx_player_events_player_id ON player_events(player_id);
                CREATE INDEX idx_player_events_created_at ON player_events(created_at);
                CREATE INDEX idx_player_events_event_type ON player_events(event_type);
            """))
            conn.commit()
            print("player_events table created")

        # Backfill from transactions if player_events is empty
        result = conn.execute(text("SELECT COUNT(*) FROM player_events;"))
        event_count = result.scalar()
        
        if event_count == 0:
            print("Backfilling player_events from transactions...")
            conn.execute(text("""
                INSERT INTO player_events (player_id, event_type, cash_amount, points_delta, pnl_impact, metadata, description, created_at)
                SELECT 
                    t.player_id,
                    t.transaction_type,
                    CASE WHEN t.transaction_type IN ('deposit', 'cashout', 'loss', 'win') THEN t.amount ELSE 0 END,
                    CASE WHEN t.transaction_type IN ('reward_add', 'reward_redeem') THEN t.amount ELSE 0 END,
                    CASE 
                        WHEN t.transaction_type = 'deposit' THEN t.amount
                        WHEN t.transaction_type = 'cashout' THEN -t.amount
                        WHEN t.transaction_type = 'loss' THEN -t.amount
                        WHEN t.transaction_type = 'win' THEN t.amount
                        ELSE 0
                    END,
                    jsonb_build_object('legacy_transaction_id', t.id),
                    t.description,
                    t.created_at
                FROM transactions t
                ORDER BY t.created_at;
            """))
            conn.commit()
            print(f"Backfill complete")
        else:
            print(f"player_events already has {event_count} rows, skipping backfill")

        print("Migration complete!")

if __name__ == "__main__":
    run_migration()
