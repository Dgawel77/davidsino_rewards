"""
Davidsino Arcade - provably fair crash, plinko and mines.

Same commitment scheme and the same seed pair as the slots and the tables, so
one rotation still audits the whole floor (see slots.py and tables.py).

WHAT IS DECIDED, AND WHEN
-------------------------
Every one of these games fixes its entire outcome from
(server_seed, client_seed, nonce) at the moment the bet is placed:

    crash   the bust multiplier is drawn before the rocket leaves the ground
    plinko  the ball's whole path is drawn before it is dropped
    mines   every mine is placed before the first tile is touched

So the house cannot watch you cash out and then bust you, cannot steer a ball
away from a good bucket, and cannot move a mine under the tile you pick. Reveal
the seed and any of it can be recomputed exactly.

The one thing the client must never see early is the crash multiplier and the
mine layout of a LIVE round -- those are stripped in the public views below, not
because the outcome could change, but because seeing them would spoil the game.
"""
import math

import slots

# Every game here is priced to the same 1% house edge, applied the same way:
# the fair payout multiplied by (1 - HOUSE_EDGE).
HOUSE_EDGE = 0.01


# ============================================================
# Crash  (the rocket game / aviator)
# ============================================================
# The multiplier climbs on a fixed exponential curve and busts at a point drawn
# up front. Cash out first and you keep bet x multiplier; leave it too long and
# you keep nothing.
CRASH_MAX = 1000.00          # a ceiling so a payout cannot be unbounded
CRASH_DOUBLE_SECONDS = 4.5   # how long the multiplier takes to double

CRASH = {
    "key": "crash",
    "name": "Crash",
    "tagline": "The multiplier climbs. Cash out before it busts — or lose the lot.",
    "min_bet": 25,
    "max_bet": 2500,
    "live": True,
    "rules": [
        "The bust point is drawn from the seed before the round starts",
        f"The multiplier doubles every {CRASH_DOUBLE_SECONDS:g} seconds",
        "Cash out and you keep your bet times the multiplier at that instant",
        "Set an auto cash-out and the round settles the moment it is dealt",
        f"Capped at {CRASH_MAX:,.0f}x",
        "House edge 1% at every cash-out target",
    ],
}


def crash_point(server_seed: str, client_seed: str, nonce: int) -> float:
    """
    Draw the bust multiplier.

    P(bust >= x) = (1 - edge) / x, which is what makes the edge identical at
    every target: cashing out at x returns x with probability (1-edge)/x, so the
    expected return is (1-edge) no matter how greedy or timid you are.
    """
    r = next(iter(slots.float_stream(server_seed, client_seed, nonce, 1)))
    if r >= 0.999999:
        r = 0.999999
    raw = (1.0 - HOUSE_EDGE) / (1.0 - r)
    # Floor to two decimals: the number shown is the number paid.
    m = math.floor(raw * 100) / 100.0
    return max(1.00, min(m, CRASH_MAX))


def crash_multiplier_at(elapsed: float) -> float:
    """The curve, floored to 2dp so display and payout can never disagree."""
    if elapsed <= 0:
        return 1.00
    raw = 2.0 ** (elapsed / CRASH_DOUBLE_SECONDS)
    return max(1.00, math.floor(raw * 100) / 100.0)


def crash_time_to(multiplier: float) -> float:
    """Seconds for the curve to reach a multiplier — used to animate the climb."""
    if multiplier <= 1.0:
        return 0.0
    return math.log2(multiplier) * CRASH_DOUBLE_SECONDS


def crash_start(bust: float, bet: float, target: float = None) -> dict:
    """
    Begin a round. `target` is an optional auto cash-out, which settles the round
    immediately — there is no decision left to make, so there is nothing to wait for.
    """
    state = {
        "bust": bust,
        "bet": bet,
        "target": target,
        "wagered": bet,
        "stage": "flying",
        "cashed_at": None,
    }
    if target is not None:
        if target < 1.01:
            raise ValueError("Auto cash-out must be at least 1.01x")
        if target > CRASH_MAX:
            raise ValueError(f"Auto cash-out cannot exceed {CRASH_MAX:,.0f}x")
        if bust >= target:
            state["cashed_at"] = target
            state["payout"] = round(bet * target, 2)
        else:
            state["payout"] = 0.0
        state["stage"] = "settled"
    return state


def crash_cash_out(state: dict, elapsed: float) -> dict:
    """
    Cash out at whatever the curve says NOW.

    `elapsed` is measured on the server from the round's own start time. The
    client never gets to name its own multiplier — it would simply claim the
    bust point every time.
    """
    if state["stage"] != "flying":
        raise ValueError("That round is already finished")

    reached = crash_multiplier_at(elapsed)
    state["stage"] = "settled"
    if reached >= state["bust"]:
        # The rocket was already gone by the time the request landed.
        state["cashed_at"] = None
        state["payout"] = 0.0
    else:
        state["cashed_at"] = reached
        state["payout"] = round(state["bet"] * reached, 2)
    return state


def crash_expire(state: dict) -> dict:
    """Settle a round nobody came back for. It busted; that is simply when."""
    if state["stage"] != "flying":
        return state
    state["stage"] = "settled"
    state["cashed_at"] = None
    state["payout"] = 0.0
    return state


def crash_public(state: dict, elapsed: float = 0.0) -> dict:
    live = state["stage"] == "flying"
    out = {
        "bet": state["bet"],
        "target": state.get("target"),
        "stage": state["stage"],
        "wagered": state["wagered"],
        "payout": state.get("payout", 0.0),
        "cashed_at": state.get("cashed_at"),
        "double_seconds": CRASH_DOUBLE_SECONDS,
        "elapsed": round(elapsed, 3),
    }
    # The bust point is withheld while the round is live -- knowing it would make
    # the game trivial. It was fixed before the round began either way.
    if not live:
        out["bust"] = state["bust"]
    return out


# ============================================================
# Plinko
# ============================================================
PLINKO_ROWS = 12

# Relative bucket shapes, centre outwards. These are scaled below so the game
# returns exactly (1 - HOUSE_EDGE) before rounding; the rounded tables and their
# true RTP are computed at import and exposed, so nothing here is asserted
# without being derived.
_PLINKO_SHAPES = {
    # index 0 is the centre bucket, index 6 the outermost.
    "low":    [0.60, 0.80, 1.00, 1.20, 1.50, 3.00, 9.00],
    "medium": [0.35, 0.50, 0.80, 1.30, 3.00, 12.00, 55.00],
    "high":   [0.20, 0.25, 0.35, 0.70, 3.50, 40.00, 400.00],
}


def _binomial(n: int, k: int) -> int:
    return math.comb(n, k)


def _bucket_probabilities(rows: int) -> list:
    total = 2 ** rows
    return [_binomial(rows, k) / total for k in range(rows + 1)]


def _build_plinko_table(shape: list, rows: int) -> list:
    """
    Turn a centre-outwards shape into a full symmetric payout row, scaled so the
    expected return is exactly (1 - HOUSE_EDGE), then rounded to two decimals.
    """
    half = rows // 2
    # Expand: index k counts how many times the ball went right.
    raw = []
    for k in range(rows + 1):
        distance = abs(k - half)
        raw.append(shape[min(distance, len(shape) - 1)])

    probs = _bucket_probabilities(rows)
    expected = sum(p * v for p, v in zip(probs, raw))
    scale = (1.0 - HOUSE_EDGE) / expected
    return [round(v * scale, 2) for v in raw]


PLINKO_TABLES = {risk: _build_plinko_table(shape, PLINKO_ROWS)
                 for risk, shape in _PLINKO_SHAPES.items()}


def plinko_rtp(risk: str) -> float:
    """Exact return, by enumerating all 13 buckets against the binomial."""
    probs = _bucket_probabilities(PLINKO_ROWS)
    return sum(p * v for p, v in zip(probs, PLINKO_TABLES[risk]))


# Dropping several at once is just several independent drops, each with its own
# nonce -- so every ball is separately verifiable rather than one draw smeared
# across a handful of balls.
PLINKO_MAX_BALLS = 10

PLINKO = {
    "key": "plinko",
    "name": "Plinko",
    "tagline": "Drop the ball through twelve rows of pins. The middle is a slow bleed; the edges pay.",
    "min_bet": 25,
    "max_bet": 2500,
    "live": False,
    "rows": PLINKO_ROWS,
    "max_balls": PLINKO_MAX_BALLS,
    "risks": {
        risk: {
            "label": risk.capitalize(),
            "table": PLINKO_TABLES[risk],
            "top": max(PLINKO_TABLES[risk]),
            "rtp": round(plinko_rtp(risk) * 100, 2),
        }
        for risk in ("low", "medium", "high")
    },
    "rules": [
        f"{PLINKO_ROWS} rows of pins, so {PLINKO_ROWS + 1} buckets",
        "Each pin is a coin flip drawn from the seed — the whole path is fixed before the drop",
        f"Drop up to {PLINKO_MAX_BALLS} balls at once; each one is its own bet and its own nonce",
        "Higher risk empties the middle to pay the edges; the return is the same either way",
        "House edge 1% on every risk setting",
    ],
}


def plinko_drop(risk: str, server_seed: str, client_seed: str, nonce: int) -> dict:
    if risk not in PLINKO_TABLES:
        raise ValueError("Unknown plinko risk")
    floats = list(slots.float_stream(server_seed, client_seed, nonce, PLINKO_ROWS))
    path = ["R" if f >= 0.5 else "L" for f in floats]
    bucket = sum(1 for step in path if step == "R")
    return {"path": path, "bucket": bucket, "multiplier": PLINKO_TABLES[risk][bucket]}


def plinko_settle(drop: dict, bet: float) -> dict:
    payout = round(bet * drop["multiplier"], 2)
    return {
        "payout": payout,
        "verdict": "win" if payout > bet else ("push" if payout == bet else "lose"),
        **drop,
    }


# ============================================================
# Mines
# ============================================================
MINES_TILES = 25

MINES = {
    "key": "mines",
    "name": "Mines",
    "tagline": "Twenty-five tiles, some of them loaded. Every safe pick pays more; one wrong pick pays nothing.",
    "min_bet": 25,
    "max_bet": 2500,
    "live": True,
    "tiles": MINES_TILES,
    "min_mines": 1,
    "max_mines": 24,
    "default_mines": 3,
    "rules": [
        f"{MINES_TILES} tiles; you choose how many are mined",
        "The layout is drawn from the seed before you touch anything",
        "Each safe tile raises the multiplier; cash out whenever you like",
        "One mine ends the round and takes the stake",
        "House edge 1%, whatever the mine count",
    ],
}


def mines_multiplier(mine_count: int, picks: int) -> float:
    """
    Fair inverse-probability payout, less the house edge.

    Surviving `picks` tiles has probability C(safe, picks) / C(25, picks), so the
    fair return is the reciprocal. Everything else in this game follows from it.
    """
    if picks <= 0:
        return 1.0
    safe = MINES_TILES - mine_count
    if picks > safe:
        raise ValueError("More picks than there are safe tiles")
    fair = _binomial(MINES_TILES, picks) / _binomial(safe, picks)
    # Round rather than truncate: flooring a 1.03x to two decimals throws away
    # most of a percent at shallow depths, which would quietly make the early
    # picks a far worse bet than the advertised 1%.
    return round(fair * (1.0 - HOUSE_EDGE), 2)


def mines_layout(mine_count: int, server_seed: str, client_seed: str, nonce: int) -> list:
    """Fisher-Yates over the tile indices; the first `mine_count` are mined."""
    if not (MINES["min_mines"] <= mine_count <= MINES["max_mines"]):
        raise ValueError(f"Mines must be between {MINES['min_mines']} and {MINES['max_mines']}")
    order = list(range(MINES_TILES))
    floats = list(slots.float_stream(server_seed, client_seed, nonce, MINES_TILES - 1))
    for idx, i in enumerate(range(MINES_TILES - 1, 0, -1)):
        j = int(floats[idx] * (i + 1))
        if j > i:
            j = i
        order[i], order[j] = order[j], order[i]
    return sorted(order[:mine_count])


def mines_start(layout: list, mine_count: int, bet: float) -> dict:
    return {
        "mines": layout,
        "mine_count": mine_count,
        "bet": bet,
        "wagered": bet,
        "picked": [],
        "stage": "picking",
        "hit": None,
    }


def mines_pick(state: dict, tile: int) -> dict:
    if state["stage"] != "picking":
        raise ValueError("That round is already finished")
    if not isinstance(tile, int) or not (0 <= tile < MINES_TILES):
        raise ValueError("No such tile")
    if tile in state["picked"]:
        raise ValueError("You already turned that one over")

    if tile in state["mines"]:
        state["picked"].append(tile)
        state["hit"] = tile
        state["stage"] = "settled"
        state["payout"] = 0.0
        return state

    state["picked"].append(tile)
    safe = MINES_TILES - state["mine_count"]
    if len(state["picked"]) >= safe:
        # Every safe tile turned over — there is nothing left to risk.
        state["stage"] = "settled"
        state["payout"] = round(state["bet"] * mines_multiplier(state["mine_count"], safe), 2)
    return state


def mines_cash_out(state: dict) -> dict:
    if state["stage"] != "picking":
        raise ValueError("That round is already finished")
    picks = len(state["picked"])
    if picks == 0:
        raise ValueError("Turn at least one tile over first")
    state["stage"] = "settled"
    state["payout"] = round(state["bet"] * mines_multiplier(state["mine_count"], picks), 2)
    return state


def mines_public(state: dict) -> dict:
    live = state["stage"] == "picking"
    picks = len(state["picked"])
    safe_picks = picks - (1 if state.get("hit") is not None else 0)
    out = {
        "bet": state["bet"],
        "mine_count": state["mine_count"],
        "picked": state["picked"],
        "picks": safe_picks,
        "stage": state["stage"],
        "wagered": state["wagered"],
        "payout": state.get("payout", 0.0),
        "hit": state.get("hit"),
        "multiplier": mines_multiplier(state["mine_count"], safe_picks) if safe_picks else 1.0,
    }
    safe = MINES_TILES - state["mine_count"]
    if safe_picks < safe:
        out["next_multiplier"] = mines_multiplier(state["mine_count"], safe_picks + 1)
    # The layout stays secret until the round is over — it was fixed at the deal.
    if not live:
        out["mines"] = state["mines"]
    return out


# ============================================================
# Registry
# ============================================================
GAMES = {
    CRASH["key"]: CRASH,
    PLINKO["key"]: PLINKO,
    MINES["key"]: MINES,
}

INSTANT_GAMES = {PLINKO["key"]}
ROUND_GAMES = {CRASH["key"], MINES["key"]}


def game_list() -> list:
    out = []
    for key in ("crash", "plinko", "mines"):
        g = GAMES[key]
        entry = {
            "key": g["key"], "name": g["name"], "tagline": g["tagline"],
            "min_bet": g["min_bet"], "max_bet": g["max_bet"],
            "rules": g["rules"], "live": g["live"], "arcade": True,
        }
        if key == "plinko":
            entry["rows"] = g["rows"]
            entry["risks"] = g["risks"]
            entry["max_balls"] = g["max_balls"]
        if key == "mines":
            entry["tiles"] = g["tiles"]
            entry["min_mines"] = g["min_mines"]
            entry["max_mines"] = g["max_mines"]
            entry["default_mines"] = g["default_mines"]
            # The whole ladder, so a player can see what they are playing for.
            entry["ladder"] = [
                [m, [mines_multiplier(m, k) for k in range(1, min(6, MINES_TILES - m) + 1)]]
                for m in (1, 3, 5, 10, 24)
            ]
        if key == "crash":
            entry["max_multiplier"] = CRASH_MAX
            entry["double_seconds"] = CRASH_DOUBLE_SECONDS
        out.append(entry)
    return out
