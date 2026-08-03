"""
Davidsino Tables - provably fair blackjack, baccarat, fan-tan and Mississippi Stud.

All bets and wins are in reward points (never cash / PNL).

PROVABLE FAIRNESS
-----------------
Same commitment scheme as the slots (see slots.py). The one thing worth spelling
out for table games is WHEN the cards are decided:

    The entire shoe is shuffled ONCE, at the start of the round, from
    (server_seed, client_seed, nonce). Every card that will be dealt is fixed
    before you make a single decision.

That matters. It means the house cannot look at your hit and then pick a card to
bust you -- the card was already sitting at that position in the shoe. It also
means a round that spans several requests (blackjack, Mississippi Stud) is
verifiable end to end: reveal the server seed, re-run the shuffle, and every card
that appeared must match, in order.

The shuffle is a Fisher-Yates driven by the same float stream the slots use, so
one seed rotation audits every game on the floor.
"""
import slots

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = ["♠", "♥", "♦", "♣"]

# Rounds that span multiple requests cap the deck draw so a malicious client
# can't make us deal past the end of the shoe.
MAX_DRAW = 24


# ============================================================
# Cards
# ============================================================
def build_deck(decks: int = 1) -> list:
    """An ordered deck (or shoe). Shuffled separately so the order is auditable."""
    return [r + s for _ in range(decks) for s in SUITS for r in RANKS]


def shuffle_deck(server_seed: str, client_seed: str, nonce: int, decks: int = 1) -> list:
    """
    Fisher-Yates over the whole shoe, driven by the provably-fair float stream.

    Walking i from the top down and swapping with a uniformly chosen j <= i is the
    standard unbiased shuffle; using floats keeps it identical to what a player
    reproduces in the verifier.
    """
    deck = build_deck(decks)
    n = len(deck)
    floats = list(slots.float_stream(server_seed, client_seed, nonce, n - 1))
    for idx, i in enumerate(range(n - 1, 0, -1)):
        j = int(floats[idx] * (i + 1))
        if j > i:          # only reachable if a float were exactly 1.0
            j = i
        deck[i], deck[j] = deck[j], deck[i]
    return deck


def rank_of(card: str) -> str:
    """Card is rank + a one-character suit, so '10♦' -> '10'."""
    return card[:-1]


def suit_of(card: str) -> str:
    return card[-1]


def rank_index(card: str) -> int:
    """0 for a deuce, 12 for an ace. Used for straights and pair thresholds."""
    return RANKS.index(rank_of(card))


# ============================================================
# Blackjack
# ============================================================
BLACKJACK = {
    "key": "blackjack",
    "name": "Blackjack",
    "tagline": "Six decks, dealer stands on all 17, blackjack pays 3:2.",
    "min_bet": 25,
    "max_bet": 5000,
    "decks": 6,
    "rules": [
        "Six decks, shuffled fresh every hand",
        "Dealer stands on all 17, soft or hard",
        "Blackjack pays 3:2",
        "Double on any first two cards",
        "Split one time; split aces draw one card each",
        "No insurance, no surrender",
    ],
}


def hand_value(cards: list) -> int:
    """Best total <= 21 if one exists, otherwise the busted hard total."""
    total = 0
    aces = 0
    for c in cards:
        r = rank_of(c)
        if r == "A":
            total += 11
            aces += 1
        elif r in ("K", "Q", "J", "10"):
            total += 10
        else:
            total += int(r)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def is_soft(cards: list) -> bool:
    """True when an ace is still being counted as 11."""
    hard = sum(1 if rank_of(c) == "A" else
               10 if rank_of(c) in ("K", "Q", "J", "10") else int(rank_of(c))
               for c in cards)
    return any(rank_of(c) == "A" for c in cards) and hard + 10 <= 21


def is_blackjack(cards: list) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21


def blackjack_start(deck: list, bet: float) -> dict:
    """Deal player, dealer, player, dealer -- the dealer's second card is the hole."""
    state = {
        "pos": 4,
        "dealer": [deck[1], deck[3]],
        "hands": [{"cards": [deck[0], deck[2]], "bet": bet, "status": "active", "result": None,
                   "payout": 0.0}],
        "active": 0,
        "stage": "player",
        "wagered": bet,
    }

    player_bj = is_blackjack(state["hands"][0]["cards"])
    dealer_bj = is_blackjack(state["dealer"])
    if player_bj or dealer_bj:
        # Naturals end it immediately -- there is nothing to decide.
        state["hands"][0]["status"] = "blackjack" if player_bj else "stand"
        _blackjack_settle(state)
    return state


def blackjack_actions(state: dict) -> list:
    """What the player may legally do right now."""
    if state["stage"] != "player":
        return []
    hand = state["hands"][state["active"]]
    acts = ["hit", "stand"]
    if len(hand["cards"]) == 2:
        acts.append("double")
        # One split only, and only on a matching rank.
        if len(state["hands"]) == 1 and rank_of(hand["cards"][0]) == rank_of(hand["cards"][1]):
            acts.append("split")
    return acts


def _draw(state: dict, deck: list) -> str:
    card = deck[state["pos"]]
    state["pos"] += 1
    return card


def blackjack_act(state: dict, deck: list, action: str) -> dict:
    """
    Apply one player decision. Raises ValueError on anything illegal, so the
    route can reject a malformed or out-of-turn request without corrupting state.
    """
    if action not in blackjack_actions(state):
        raise ValueError(f"Cannot {action} right now")

    hand = state["hands"][state["active"]]

    if action == "hit":
        hand["cards"].append(_draw(state, deck))
        if hand_value(hand["cards"]) > 21:
            hand["status"] = "bust"
            _blackjack_advance(state, deck)

    elif action == "stand":
        hand["status"] = "stand"
        _blackjack_advance(state, deck)

    elif action == "double":
        state["wagered"] += hand["bet"]
        hand["bet"] *= 2
        hand["cards"].append(_draw(state, deck))
        hand["status"] = "bust" if hand_value(hand["cards"]) > 21 else "stand"
        _blackjack_advance(state, deck)

    elif action == "split":
        second = hand["cards"].pop()
        state["wagered"] += hand["bet"]
        new_hand = {"cards": [second], "bet": hand["bet"], "status": "active",
                    "result": None, "payout": 0.0}
        state["hands"].append(new_hand)
        hand["cards"].append(_draw(state, deck))
        new_hand["cards"].append(_draw(state, deck))
        if rank_of(hand["cards"][0]) == "A":
            # Split aces get exactly one card each.
            hand["status"] = "stand"
            new_hand["status"] = "stand"
            _blackjack_advance(state, deck)

    return state


def _blackjack_advance(state: dict, deck: list) -> None:
    """Move to the next unfinished hand, or hand it over to the dealer."""
    for i, h in enumerate(state["hands"]):
        if h["status"] == "active":
            state["active"] = i
            return
    _blackjack_dealer_play(state, deck)
    _blackjack_settle(state)


def _blackjack_dealer_play(state: dict, deck: list) -> None:
    # No reason to expose the hole card sequence if every hand already busted.
    if all(h["status"] == "bust" for h in state["hands"]):
        return
    while hand_value(state["dealer"]) < 17 and state["pos"] < len(deck) - 1:
        state["dealer"].append(_draw(state, deck))


def _blackjack_settle(state: dict) -> None:
    state["stage"] = "settled"
    dealer_total = hand_value(state["dealer"])
    dealer_bj = is_blackjack(state["dealer"])
    dealer_bust = dealer_total > 21

    total = 0.0
    for h in state["hands"]:
        cards = h["cards"]
        bet = h["bet"]
        total_h = hand_value(cards)

        if h["status"] == "bust":
            h["result"], h["payout"] = "bust", 0.0
        elif is_blackjack(cards) and len(state["hands"]) == 1:
            if dealer_bj:
                h["result"], h["payout"] = "push", bet
            else:
                # 3:2 -- stake back plus one and a half.
                h["result"], h["payout"] = "blackjack", bet * 2.5
        elif dealer_bj:
            h["result"], h["payout"] = "lose", 0.0
        elif dealer_bust or total_h > dealer_total:
            h["result"], h["payout"] = "win", bet * 2
        elif total_h == dealer_total:
            h["result"], h["payout"] = "push", bet
        else:
            h["result"], h["payout"] = "lose", 0.0
        total += h["payout"]

    state["payout"] = total
    state["dealer_total"] = dealer_total


def blackjack_public(state: dict) -> dict:
    """
    What the client is allowed to see. While the hand is live the dealer's hole
    card is withheld -- it exists in the shuffled shoe either way, so hiding it
    is presentation, not an opportunity to change it.
    """
    live = state["stage"] == "player"
    dealer = [state["dealer"][0], "??"] if live else state["dealer"]
    return {
        "dealer": dealer,
        "dealer_total": hand_value([state["dealer"][0]]) if live else state.get("dealer_total"),
        "hands": [{"cards": h["cards"], "bet": h["bet"], "total": hand_value(h["cards"]),
                   "soft": is_soft(h["cards"]), "status": h["status"],
                   "result": h["result"], "payout": h["payout"]}
                  for h in state["hands"]],
        "active": state["active"],
        "stage": state["stage"],
        "actions": blackjack_actions(state),
        "wagered": state["wagered"],
        "payout": state.get("payout", 0.0),
    }


# ============================================================
# Baccarat
# ============================================================
BACCARAT = {
    "key": "baccarat",
    "name": "Baccarat",
    "tagline": "Punto banco. Bet the Player, the Banker or the Tie — no decisions after that.",
    "min_bet": 25,
    "max_bet": 5000,
    "decks": 8,
    "bets": {
        # profit multiplier on a win; banker carries the traditional 5% commission
        "player": {"label": "Player", "pays": 1.0, "edge": "1.24%"},
        "banker": {"label": "Banker", "pays": 0.95, "edge": "1.06%"},
        "tie": {"label": "Tie", "pays": 8.0, "edge": "14.36%"},
    },
    "rules": [
        "Eight decks, shuffled fresh every coup",
        "Tens and face cards count zero, aces count one; only the last digit matters",
        "Banker wins pay 0.95 to 1 — the house takes its 5% there, not on the deal",
        "A tie pushes Player and Banker bets rather than losing them",
    ],
}


def baccarat_points(cards: list) -> int:
    total = 0
    for c in cards:
        r = rank_of(c)
        if r in ("10", "J", "Q", "K"):
            continue
        total += 1 if r == "A" else int(r)
    return total % 10


def baccarat_deal(deck: list) -> dict:
    """Standard punto banco drawing rules — nobody makes a choice here."""
    player = [deck[0], deck[2]]
    banker = [deck[1], deck[3]]
    pos = 4
    player_third = None

    p, b = baccarat_points(player), baccarat_points(banker)
    natural = p >= 8 or b >= 8

    if not natural:
        if p <= 5:
            player_third = deck[pos]
            pos += 1
            player.append(player_third)
            p = baccarat_points(player)

        if player_third is None:
            # Player stood: banker draws on 5 or less.
            if b <= 5:
                banker.append(deck[pos])
                pos += 1
        else:
            # The third-card table, keyed on the banker total and the card the
            # player drew. This is the part everyone gets wrong, so it is explicit.
            t = rank_of(player_third)
            third_val = 0 if t in ("10", "J", "Q", "K") else (1 if t == "A" else int(t))
            draw = (
                b <= 2
                or (b == 3 and third_val != 8)
                or (b == 4 and 2 <= third_val <= 7)
                or (b == 5 and 4 <= third_val <= 7)
                or (b == 6 and 6 <= third_val <= 7)
            )
            if draw:
                banker.append(deck[pos])
                pos += 1
        b = baccarat_points(banker)

    if p > b:
        outcome = "player"
    elif b > p:
        outcome = "banker"
    else:
        outcome = "tie"

    return {
        "player": player, "banker": banker,
        "player_points": p, "banker_points": b,
        "natural": natural, "outcome": outcome,
    }


def baccarat_settle(result: dict, bet_type: str, bet: float) -> dict:
    """Returns the gross payout, i.e. stake included when the bet wins or pushes."""
    if bet_type not in BACCARAT["bets"]:
        raise ValueError("Unknown baccarat bet")
    outcome = result["outcome"]

    if bet_type == outcome:
        pays = BACCARAT["bets"][bet_type]["pays"]
        payout = bet + bet * pays
        verdict = "win"
    elif outcome == "tie" and bet_type in ("player", "banker"):
        payout, verdict = bet, "push"
    else:
        payout, verdict = 0.0, "lose"

    return {"payout": round(payout, 2), "verdict": verdict, "outcome": outcome}


# ============================================================
# Fan-Tan
# ============================================================
# A handful of beads is drawn and counted out four at a time; you bet on what
# the remainder will be. The pile size is drawn from a range whose length is a
# multiple of four, which is what keeps each remainder exactly 1-in-4.
FAN_TAN_MIN_BEADS = 24
FAN_TAN_MAX_BEADS = 119          # 96 possible piles = 24 per remainder

FAN_TAN = {
    "key": "fan_tan",
    "name": "Fan-Tan",
    "tagline": "A handful of beads, counted out in fours. Bet the remainder.",
    "min_bet": 25,
    "max_bet": 5000,
    "commission": 0.05,
    "bets": {
        "fan": {"label": "Fan", "picks": 1, "pays": 3.0,
                "help": "One number. Pays 3 to 1.", "edge": "3.75%"},
        "kwok": {"label": "Kwok", "picks": 2, "pays": 1.0,
                 "help": "Two numbers, either one wins. Pays even money.", "edge": "2.50%"},
        "nga": {"label": "Nga Tan", "picks": 3, "pays": 1.0 / 3.0,
                "help": "Three numbers. Pays 1 to 3 — you win often, small.", "edge": "1.25%"},
    },
    "rules": [
        "The pile is between 24 and 119 beads, so every remainder is exactly 1 in 4",
        "A remainder of zero counts as 4",
        "The house takes 5% of winnings, never of the stake",
    ],
}


def fan_tan_draw(server_seed: str, client_seed: str, nonce: int) -> dict:
    span = FAN_TAN_MAX_BEADS - FAN_TAN_MIN_BEADS + 1
    roll = next(iter(slots.float_stream(server_seed, client_seed, nonce, 1)))
    beads = FAN_TAN_MIN_BEADS + min(int(roll * span), span - 1)
    remainder = beads % 4
    return {"beads": beads, "result": 4 if remainder == 0 else remainder}


def fan_tan_settle(draw: dict, bet_type: str, picks: list, bet: float) -> dict:
    spec = FAN_TAN["bets"].get(bet_type)
    if spec is None:
        raise ValueError("Unknown fan-tan bet")
    picks = sorted(set(int(p) for p in picks))
    if len(picks) != spec["picks"] or any(p < 1 or p > 4 for p in picks):
        raise ValueError(f"{spec['label']} needs {spec['picks']} number(s) from 1 to 4")

    if draw["result"] in picks:
        profit = bet * spec["pays"]
        profit -= profit * FAN_TAN["commission"]
        payout, verdict = bet + profit, "win"
    else:
        payout, verdict = 0.0, "lose"

    return {"payout": round(payout, 2), "verdict": verdict,
            "result": draw["result"], "beads": draw["beads"], "picks": picks}


# ============================================================
# Mississippi Stud
# ============================================================
MISSISSIPPI = {
    "key": "mississippi",
    "name": "Mississippi Stud",
    "tagline": "Two cards, three streets. Raise 1x to 3x or fold — you're playing the paytable, not a dealer.",
    "min_bet": 25,
    "max_bet": 1000,
    "decks": 1,
    "rules": [
        "Ante, then see your two cards",
        "On each street raise 1x, 2x or 3x the ante, or fold and lose what's in",
        "There is no dealer hand — the paytable is the only thing you're beating",
        "Payouts apply to everything wagered, ante and raises together",
        "A pair of sixes through tens returns your money",
    ],
    # Multiplier applied to total wagered. 1 means push (stake back, no profit).
    "paytable": [
        ("Royal flush", 500), ("Straight flush", 100), ("Four of a kind", 40),
        ("Full house", 10), ("Flush", 6), ("Straight", 4),
        ("Three of a kind", 3), ("Two pair", 2), ("Pair, jacks or better", 1),
        ("Pair, sixes through tens", 0), ("Pair, fives or lower", -1), ("Nothing", -1),
    ],
}

_MS_PAY = {
    "royal_flush": 500, "straight_flush": 100, "quads": 40, "full_house": 10,
    "flush": 6, "straight": 4, "trips": 3, "two_pair": 2,
    "high_pair": 1, "mid_pair": 0, "low_pair": -1, "nothing": -1,
}
_MS_LABEL = {
    "royal_flush": "Royal flush", "straight_flush": "Straight flush", "quads": "Four of a kind",
    "full_house": "Full house", "flush": "Flush", "straight": "Straight",
    "trips": "Three of a kind", "two_pair": "Two pair", "high_pair": "Pair, jacks or better",
    "mid_pair": "Pair, sixes through tens", "low_pair": "Pair, fives or lower", "nothing": "Nothing",
}


def classify_five(cards: list) -> str:
    """Rank a five-card hand into a Mississippi Stud paytable category."""
    if len(cards) != 5:
        raise ValueError("Need exactly five cards")

    idx = sorted(rank_index(c) for c in cards)
    counts = {}
    for i in idx:
        counts[i] = counts.get(i, 0) + 1
    shape = sorted(counts.values(), reverse=True)

    flush = len(set(suit_of(c) for c in cards)) == 1
    distinct = sorted(set(idx))
    straight = len(distinct) == 5 and distinct[4] - distinct[0] == 4
    # The wheel: A-2-3-4-5, with the ace playing low.
    wheel = distinct == [0, 1, 2, 3, 12]
    if wheel:
        straight = True

    if straight and flush:
        # Ten through ace, i.e. indices 8..12.
        if distinct == [8, 9, 10, 11, 12]:
            return "royal_flush"
        return "straight_flush"
    if shape[0] == 4:
        return "quads"
    if shape == [3, 2]:
        return "full_house"
    if flush:
        return "flush"
    if straight:
        return "straight"
    if shape[0] == 3:
        return "trips"
    if shape == [2, 2, 1]:
        return "two_pair"
    if shape[0] == 2:
        pair_rank = max(i for i, c in counts.items() if c == 2)
        if pair_rank >= RANKS.index("J"):
            return "high_pair"
        if pair_rank >= RANKS.index("6"):
            return "mid_pair"
        return "low_pair"
    return "nothing"


def mississippi_start(deck: list, ante: float) -> dict:
    """Deal the two hole cards. The three community cards are already fixed in the deck."""
    return {
        "hole": [deck[0], deck[1]],
        "community": [],
        "ante": ante,
        "bets": [],
        "street": 3,
        "wagered": ante,
        "stage": "playing",
    }


def mississippi_act(state: dict, deck: list, action: str, multiple: int = 0) -> dict:
    """
    action is 'fold' or 'raise'. A raise is 1x, 2x or 3x the ante.
    Streets run 3 -> 4 -> 5, revealing one community card each time.
    """
    if state["stage"] != "playing":
        raise ValueError("This hand is already finished")

    if action == "fold":
        state["stage"] = "folded"
        state["payout"] = 0.0
        state["category"] = "folded"
        state["label"] = "Folded"
        # Fold face-up so the player can still verify the deal against the seed.
        state["community"] = [deck[2], deck[3], deck[4]]
        return state

    if action != "raise":
        raise ValueError("Unknown action")
    if multiple not in (1, 2, 3):
        raise ValueError("Raise must be 1x, 2x or 3x")

    amount = state["ante"] * multiple
    state["bets"].append({"street": state["street"], "multiple": multiple, "amount": amount})
    state["wagered"] += amount

    # Reveal this street's community card: 3rd street -> deck[2], and so on.
    state["community"].append(deck[2 + len(state["community"])])

    if state["street"] < 5:
        state["street"] += 1
    else:
        _mississippi_settle(state)
    return state


def _mississippi_settle(state: dict) -> None:
    state["stage"] = "settled"
    category = classify_five(state["hole"] + state["community"])
    mult = _MS_PAY[category]
    state["category"] = category
    state["label"] = _MS_LABEL[category]
    # -1 means the wager is lost; 0 is a push, so the stake comes back.
    state["payout"] = 0.0 if mult < 0 else state["wagered"] * (mult + 1)


def mississippi_public(state: dict) -> dict:
    return {
        "hole": state["hole"],
        "community": state["community"],
        "ante": state["ante"],
        "bets": state["bets"],
        "street": state["street"],
        "wagered": state["wagered"],
        "stage": state["stage"],
        "payout": state.get("payout", 0.0),
        "category": state.get("category"),
        "label": state.get("label"),
    }


# ============================================================
# Registry
# ============================================================
TABLES = {
    BLACKJACK["key"]: BLACKJACK,
    BACCARAT["key"]: BACCARAT,
    FAN_TAN["key"]: FAN_TAN,
    MISSISSIPPI["key"]: MISSISSIPPI,
}

# Games that finish inside one request vs. games that hold a live round.
INSTANT_GAMES = {BACCARAT["key"], FAN_TAN["key"]}
ROUND_GAMES = {BLACKJACK["key"], MISSISSIPPI["key"]}


def table_list() -> list:
    """Lobby payload — everything the client needs to render the floor."""
    out = []
    for key in ("blackjack", "baccarat", "fan_tan", "mississippi"):
        g = TABLES[key]
        entry = {
            "key": g["key"], "name": g["name"], "tagline": g["tagline"],
            "min_bet": g["min_bet"], "max_bet": g["max_bet"],
            "rules": g["rules"], "live": key in ROUND_GAMES,
        }
        if "bets" in g:
            entry["bets"] = g["bets"]
        if key == "mississippi":
            entry["paytable"] = [[label, mult] for label, mult in g["paytable"]]
        out.append(entry)
    return out


def deck_for(game: str, server_seed: str, client_seed: str, nonce: int) -> list:
    decks = TABLES[game].get("decks", 1)
    return shuffle_deck(server_seed, client_seed, nonce, decks)
