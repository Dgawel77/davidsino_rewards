#!/usr/bin/env python3
"""
Create the fixed roster for a small private deployment.

    python3 seed_players.py            # create anyone missing, print the cards
    python3 seed_players.py --show     # just print the existing cards

Idempotent: running it twice does not duplicate anyone, reset anyone's balance,
or change a card that already exists. Safe to run on every container boot.

WHY THE CARD IDS LOOK LIKE THAT
-------------------------------
The card ID is the password, and there is no second factor behind it. So by
default each player gets a random 16-hex-character one, shaped like an RFID UID
but not guessable.

Override it when you want something else:

    SEED_CARD_DAVID=04a2b3c4d5e6f7    # the real UID off your RFID card
    SEED_CARD_DAVID=david             # memorable, and trivially guessable

The second form is fine for a throwaway test with play money. It is not fine
once anything matters, because "david" is a password of "david". Lookups ignore
case either way.
"""
import os
import secrets
import sys

os.environ.setdefault("SEEDING", "1")

import main  # noqa: E402  (importing creates the tables)

# Names only. The card ID -- the credential -- is random unless supplied.
ROSTER = ["David", "Alex", "Max"]
START_POINTS = float(os.getenv("SEED_START_POINTS", "10000"))


def card_for(name: str) -> str:
    override = os.getenv(f"SEED_CARD_{name.upper()}")
    if override:
        return override.strip()
    # 16 hex chars: the shape of an RFID UID, but not guessable. Lower case
    # because it is easier to read out and type; lookups ignore case anyway.
    return secrets.token_hex(8)


def main_seed(show_only: bool = False) -> int:
    db = main.SessionLocal()
    try:
        rows = []
        for name in ROSTER:
            player = db.query(main.Player).filter(main.Player.name == name).first()
            if player:
                rows.append((name, player.card_id, player.reward_points, "existing"))
                continue
            if show_only:
                rows.append((name, "—", 0, "not created"))
                continue

            player = main.Player(
                card_id=card_for(name),
                name=name,
                reward_points=START_POINTS,
            )
            db.add(player)
            db.flush()
            main.record_event(
                db, player.id, "adjustment",
                points_delta=START_POINTS,
                description=f"Opening balance for {name}",
            )
            rows.append((name, player.card_id, START_POINTS, "created"))
        db.commit()

        width = max(len(c) for _, c, _, _ in rows) if rows else 10
        print(f"\n  {'PLAYER':<8}  {'CARD ID':<{width}}  {'POINTS':>10}  STATUS")
        print("  " + "-" * (8 + width + 26))
        for name, card, points, status in rows:
            print(f"  {name:<8}  {card:<{width}}  {points:>10,.0f}  {status}")
        print("\n  The card ID is the password. Hand each one out privately.\n")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main_seed(show_only="--show" in sys.argv))
