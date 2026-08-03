"""
Basic strategy for the house rules in tables.BLACKJACK, used to measure the edge.

This is a measuring instrument, not part of the game. It exists so we can state
the blackjack house edge as something checked rather than something claimed.

Rules assumed (must match tables.BLACKJACK):
    six decks, dealer HITS soft 17, blackjack pays 3:2,
    double on any first two cards, double after split, one split only.

The hit-soft-17 deviations from the stands-on-17 chart are marked H17 below.
They matter: playing the S17 chart against an H17 dealer gives away more than
the rule itself costs.

    python3 scripts/bj_basic_strategy.py 200000
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tables  # noqa: E402


def upcard_value(card: str) -> int:
    r = tables.rank_of(card)
    if r == "A":
        return 11
    return 10 if r in ("K", "Q", "J", "10") else int(r)


def pair_rank(card: str) -> str:
    r = tables.rank_of(card)
    if r == "A":
        return "A"
    return "10" if r in ("K", "Q", "J", "10") else r


def decide(hand: dict, up: int, actions: list) -> str:
    cards = hand["cards"]
    total = tables.hand_value(cards)
    soft = tables.is_soft(cards)
    can_double = "double" in actions
    can_split = "split" in actions

    if can_split:
        p = pair_rank(cards[0])
        if p in ("A", "8"):
            return "split"
        if p == "9":
            return "split" if up in (2, 3, 4, 5, 6, 8, 9) else "stand"
        if p == "10":
            return "stand"
        if p == "7":
            return "split" if up <= 7 else "hit"
        if p == "6":
            return "split" if up <= 6 else "hit"
        if p == "4":
            return "split" if up in (5, 6) else "hit"
        if p in ("2", "3"):
            return "split" if up <= 7 else "hit"
        # A pair of fives is never split — it plays as a hard ten.

    if soft:
        kicker = total - 11
        if kicker >= 9:                     # soft 20+
            return "stand"
        if kicker == 8:                     # soft 19 — H17: double against a six
            return "double" if (can_double and up == 6) else "stand"
        if kicker == 7:                     # soft 18 — H17: double from 2, not 3
            if can_double and 2 <= up <= 6:
                return "double"
            return "stand" if up in (7, 8) else "hit"
        if kicker == 6:
            return "double" if (can_double and 3 <= up <= 6) else "hit"
        if kicker in (4, 5):
            return "double" if (can_double and 4 <= up <= 6) else "hit"
        if kicker in (2, 3):
            return "double" if (can_double and 5 <= up <= 6) else "hit"
        return "hit"

    if total >= 17:
        return "stand"
    if 13 <= total <= 16:
        return "stand" if up <= 6 else "hit"
    if total == 12:
        return "stand" if 4 <= up <= 6 else "hit"
    if total == 11:
        return "double" if can_double else "hit"        # H17: double against an ace too
    if total == 10:
        return "double" if (can_double and up <= 9) else "hit"
    if total == 9:
        return "double" if (can_double and 3 <= up <= 6) else "hit"
    return "hit"


def play_hand(nonce: int, bet: float = 100.0, server_seed: str = "a" * 64,
              client_seed: str = "davidtest") -> dict:
    deck = tables.deck_for("blackjack", server_seed, client_seed, nonce)
    state = tables.blackjack_start(deck, bet)
    up = upcard_value(state["dealer"][0])

    guard = 0
    while state["stage"] == "player":
        guard += 1
        if guard > 40:
            raise RuntimeError("hand failed to terminate")
        actions = tables.blackjack_actions(state)
        move = decide(state["hands"][state["active"]], up, actions)
        if move not in actions:
            move = "hit" if "hit" in actions else "stand"
        tables.blackjack_act(state, deck, move)
    return state


def simulate(rounds: int = 100000, bet: float = 100.0) -> float:
    """Returns the house edge as a percentage of the opening bet."""
    staked = returned = 0.0
    for n in range(rounds):
        state = play_hand(n, bet)
        staked += state["wagered"]
        returned += state["payout"]
    return (staked - returned) / (rounds * bet) * 100


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100000
    edge = simulate(n)
    print(f"blackjack, basic strategy, {n:,} hands")
    print(f"  house edge on the opening bet: {edge:.3f}%")
    print("  (published reference for six decks, hit soft 17, DAS and unlimited")
    print("   resplits is ~0.62%; allowing only one split accounts for the rest)")
