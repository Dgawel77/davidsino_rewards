"""
Tests for the provably fair table games.

Runs under pytest OR plain stdlib:  python3 tests/test_tables.py

The slow exhaustive checks (all 2,598,960 five-card hands, the Monte Carlo
house-edge runs) are gated behind DAVIDSINO_SLOW=1 so the default run stays
quick. scripts/table_check.py runs them on demand.
"""
import itertools
import os
import sys
import unittest
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import tables  # noqa: E402

SS = "a" * 64
CS = "davidtest"
SLOW = os.getenv("DAVIDSINO_SLOW") == "1"


# ============================================================
# Deck & shuffle
# ============================================================
class TestDeck(unittest.TestCase):
    def test_single_deck_is_52_unique_cards(self):
        deck = tables.build_deck(1)
        self.assertEqual(len(deck), 52)
        self.assertEqual(len(set(deck)), 52)

    def test_six_decks_is_312_cards(self):
        self.assertEqual(len(tables.build_deck(6)), 312)

    def test_shuffle_is_a_permutation(self):
        for decks in (1, 6, 8):
            shuffled = tables.shuffle_deck(SS, CS, 0, decks)
            self.assertEqual(sorted(shuffled), sorted(tables.build_deck(decks)))

    def test_shuffle_is_deterministic(self):
        self.assertEqual(tables.shuffle_deck(SS, CS, 7, 6),
                         tables.shuffle_deck(SS, CS, 7, 6))

    def test_nonce_changes_the_shuffle(self):
        self.assertNotEqual(tables.shuffle_deck(SS, CS, 1, 6),
                            tables.shuffle_deck(SS, CS, 2, 6))

    def test_client_seed_changes_the_shuffle(self):
        self.assertNotEqual(tables.shuffle_deck(SS, "alice", 1, 6),
                            tables.shuffle_deck(SS, "bob", 1, 6))

    def test_server_seed_changes_the_shuffle(self):
        self.assertNotEqual(tables.shuffle_deck("a" * 64, CS, 1, 6),
                            tables.shuffle_deck("b" * 64, CS, 1, 6))

    def test_shuffle_spreads_a_card_across_every_position(self):
        """A biased shuffle would leave gaps or clumps; this catches an off-by-one."""
        seen = Counter()
        for n in range(4000):
            seen[tables.shuffle_deck(SS, CS, n, 1).index("A♠")] += 1
        self.assertEqual(len(seen), 52, "some deck positions never held the ace")
        # 4000 trials over 52 slots averages ~77; a broken shuffle skews far wider.
        self.assertGreater(min(seen.values()), 30)
        self.assertLess(max(seen.values()), 140)

    def test_card_helpers(self):
        self.assertEqual(tables.rank_of("10♦"), "10")
        self.assertEqual(tables.suit_of("10♦"), "♦")
        self.assertEqual(tables.rank_of("A♠"), "A")
        self.assertEqual(tables.rank_index("2♠"), 0)
        self.assertEqual(tables.rank_index("A♠"), 12)


# ============================================================
# Blackjack
# ============================================================
class TestBlackjackHands(unittest.TestCase):
    def test_hand_values(self):
        self.assertEqual(tables.hand_value(["A♠", "K♥"]), 21)
        self.assertEqual(tables.hand_value(["A♠", "A♥"]), 12)
        self.assertEqual(tables.hand_value(["A♠", "A♥", "9♦"]), 21)
        self.assertEqual(tables.hand_value(["10♠", "9♥", "5♦"]), 24)
        self.assertEqual(tables.hand_value(["A♠", "5♥", "9♦"]), 15)
        self.assertEqual(tables.hand_value(["K♠", "Q♥", "J♦"]), 30)

    def test_ace_demotes_only_as_far_as_needed(self):
        self.assertEqual(tables.hand_value(["A♠", "A♥", "A♦", "8♣"]), 21)

    def test_soft_detection(self):
        self.assertTrue(tables.is_soft(["A♠", "6♥"]))
        self.assertFalse(tables.is_soft(["A♠", "6♥", "10♦"]))   # ace forced to 1
        self.assertFalse(tables.is_soft(["10♠", "7♥"]))

    def test_blackjack_needs_exactly_two_cards(self):
        self.assertTrue(tables.is_blackjack(["A♠", "K♥"]))
        self.assertFalse(tables.is_blackjack(["7♠", "7♥", "7♦"]))


class TestBlackjackPlay(unittest.TestCase):
    def _stacked(self, cards):
        """A deck whose first cards are known, padded out with the rest."""
        rest = [c for c in tables.build_deck(6) if c not in cards]
        return list(cards) + rest

    def test_deal_order_is_player_dealer_player_dealer(self):
        deck = self._stacked(["9♠", "5♥", "7♦", "K♣"])
        st = tables.blackjack_start(deck, 100)
        self.assertEqual(st["hands"][0]["cards"], ["9♠", "7♦"])
        self.assertEqual(st["dealer"], ["5♥", "K♣"])

    def test_player_blackjack_pays_three_to_two(self):
        deck = self._stacked(["A♠", "5♥", "K♦", "9♣"])
        st = tables.blackjack_start(deck, 100)
        self.assertEqual(st["stage"], "settled")
        self.assertEqual(st["hands"][0]["result"], "blackjack")
        self.assertEqual(st["payout"], 250)          # 100 stake + 150 profit

    def test_both_blackjack_pushes(self):
        deck = self._stacked(["A♠", "A♥", "K♦", "K♣"])
        st = tables.blackjack_start(deck, 100)
        self.assertEqual(st["hands"][0]["result"], "push")
        self.assertEqual(st["payout"], 100)

    def test_dealer_blackjack_beats_a_plain_twenty(self):
        deck = self._stacked(["K♠", "A♥", "Q♦", "K♣"])
        st = tables.blackjack_start(deck, 100)
        self.assertEqual(st["hands"][0]["result"], "lose")
        self.assertEqual(st["payout"], 0)

    def test_dealer_stands_on_soft_seventeen(self):
        deck = self._stacked(["10♠", "A♥", "9♦", "6♣"])   # dealer A-6 = soft 17
        st = tables.blackjack_start(deck, 100)
        tables.blackjack_act(st, deck, "stand")
        self.assertEqual(len(st["dealer"]), 2, "dealer drew on soft 17")
        self.assertEqual(st["dealer_total"], 17)
        self.assertEqual(st["hands"][0]["result"], "win")   # 19 beats 17

    def test_dealer_draws_to_sixteen(self):
        deck = self._stacked(["10♠", "10♥", "9♦", "6♣"])   # dealer 16, must draw
        st = tables.blackjack_start(deck, 100)
        tables.blackjack_act(st, deck, "stand")
        self.assertGreater(len(st["dealer"]), 2)
        self.assertGreaterEqual(tables.hand_value(st["dealer"]), 17)

    def test_bust_loses_even_when_dealer_busts_later(self):
        deck = self._stacked(["10♠", "6♥", "9♦", "7♣", "5♠"])
        st = tables.blackjack_start(deck, 100)
        tables.blackjack_act(st, deck, "hit")             # 19 + 5 = 24
        self.assertEqual(st["hands"][0]["result"], "bust")
        self.assertEqual(st["payout"], 0)

    def test_double_doubles_the_bet_and_takes_exactly_one_card(self):
        deck = self._stacked(["6♠", "5♥", "5♦", "9♣", "9♠"])
        st = tables.blackjack_start(deck, 100)
        tables.blackjack_act(st, deck, "double")
        self.assertEqual(st["hands"][0]["bet"], 200)
        self.assertEqual(len(st["hands"][0]["cards"]), 3)
        self.assertEqual(st["wagered"], 200)

    def test_double_is_unavailable_after_hitting(self):
        deck = self._stacked(["6♠", "5♥", "5♦", "9♣", "2♠"])
        st = tables.blackjack_start(deck, 100)
        tables.blackjack_act(st, deck, "hit")
        self.assertNotIn("double", tables.blackjack_actions(st))

    def test_split_creates_two_hands_each_at_the_original_bet(self):
        deck = self._stacked(["8♠", "5♥", "8♦", "9♣", "3♠", "4♥"])
        st = tables.blackjack_start(deck, 100)
        self.assertIn("split", tables.blackjack_actions(st))
        tables.blackjack_act(st, deck, "split")
        self.assertEqual(len(st["hands"]), 2)
        self.assertEqual([h["bet"] for h in st["hands"]], [100, 100])
        self.assertEqual(st["wagered"], 200)
        for h in st["hands"]:
            self.assertEqual(len(h["cards"]), 2)

    def test_split_aces_draw_one_card_each_and_stand(self):
        deck = self._stacked(["A♠", "5♥", "A♦", "9♣", "3♠", "4♥"])
        st = tables.blackjack_start(deck, 100)
        tables.blackjack_act(st, deck, "split")
        self.assertEqual(st["stage"], "settled")
        for h in st["hands"]:
            self.assertEqual(len(h["cards"]), 2)

    def test_twenty_one_after_split_is_not_a_blackjack(self):
        deck = self._stacked(["A♠", "5♥", "A♦", "9♣", "K♠", "K♥"])
        st = tables.blackjack_start(deck, 100)
        tables.blackjack_act(st, deck, "split")
        for h in st["hands"]:
            self.assertEqual(tables.hand_value(h["cards"]), 21)
            self.assertNotEqual(h["result"], "blackjack")
            # Paid as a plain win at 2x, not 2.5x.
            self.assertIn(h["payout"], (0, 100, 200))

    def test_cannot_split_unlike_ranks(self):
        deck = self._stacked(["8♠", "5♥", "9♦", "9♣"])
        st = tables.blackjack_start(deck, 100)
        self.assertNotIn("split", tables.blackjack_actions(st))

    def test_cannot_split_twice(self):
        deck = self._stacked(["8♠", "5♥", "8♦", "9♣", "8♥", "4♥"])
        st = tables.blackjack_start(deck, 100)
        tables.blackjack_act(st, deck, "split")
        self.assertNotIn("split", tables.blackjack_actions(st))

    def test_illegal_action_raises(self):
        deck = self._stacked(["8♠", "5♥", "9♦", "9♣"])
        st = tables.blackjack_start(deck, 100)
        with self.assertRaises(ValueError):
            tables.blackjack_act(st, deck, "split")
        tables.blackjack_act(st, deck, "stand")
        with self.assertRaises(ValueError):
            tables.blackjack_act(st, deck, "hit")

    def test_push_returns_the_stake(self):
        deck = self._stacked(["10♠", "10♥", "9♦", "9♣"])   # both 19
        st = tables.blackjack_start(deck, 100)
        tables.blackjack_act(st, deck, "stand")
        self.assertEqual(st["hands"][0]["result"], "push")
        self.assertEqual(st["payout"], 100)

    def test_hole_card_is_hidden_while_the_hand_is_live(self):
        deck = self._stacked(["9♠", "5♥", "7♦", "K♣"])
        st = tables.blackjack_start(deck, 100)
        pub = tables.blackjack_public(st)
        self.assertEqual(pub["dealer"], ["5♥", "??"])
        self.assertNotIn("K♣", pub["dealer"])

    def test_hole_card_is_revealed_once_settled(self):
        deck = self._stacked(["9♠", "5♥", "7♦", "K♣"])
        st = tables.blackjack_start(deck, 100)
        tables.blackjack_act(st, deck, "stand")
        self.assertEqual(tables.blackjack_public(st)["dealer"][1], "K♣")

    def test_payout_never_exceeds_two_and_a_half_times_the_wager(self):
        for n in range(300):
            deck = tables.deck_for("blackjack", SS, CS, n)
            st = tables.blackjack_start(deck, 100)
            guard = 0
            while st["stage"] == "player":
                guard += 1
                self.assertLess(guard, 40, "hand never terminated")
                acts = tables.blackjack_actions(st)
                tables.blackjack_act(st, deck, "hit" if "hit" in acts else "stand")
            self.assertLessEqual(st["payout"], st["wagered"] * 2.5)


# ============================================================
# Baccarat
# ============================================================
class TestBaccarat(unittest.TestCase):
    def test_point_counting(self):
        self.assertEqual(tables.baccarat_points(["K♠", "Q♥"]), 0)
        self.assertEqual(tables.baccarat_points(["A♠", "2♥"]), 3)
        self.assertEqual(tables.baccarat_points(["9♠", "7♥"]), 6)   # 16 -> 6
        self.assertEqual(tables.baccarat_points(["10♠", "8♥"]), 8)

    def test_natural_stops_the_deal(self):
        rest = [c for c in tables.build_deck(8) if c not in ("9♠", "2♥", "K♦", "7♣")]
        deck = ["9♠", "2♥", "K♦", "7♣"] + rest         # player 9, banker 9
        r = tables.baccarat_deal(deck)
        self.assertTrue(r["natural"])
        self.assertEqual(len(r["player"]), 2)
        self.assertEqual(len(r["banker"]), 2)

    def test_player_draws_on_five_or_less(self):
        rest = [c for c in tables.build_deck(8) if c not in ("2♠", "3♥", "3♦", "2♣", "5♠")]
        deck = ["2♠", "3♥", "3♦", "2♣", "5♠"] + rest    # player 5, banker 5
        r = tables.baccarat_deal(deck)
        self.assertEqual(len(r["player"]), 3)

    def test_banker_payout_carries_the_commission(self):
        r = {"outcome": "banker"}
        self.assertEqual(tables.baccarat_settle(r, "banker", 100)["payout"], 195.0)

    def test_player_pays_even_money(self):
        r = {"outcome": "player"}
        self.assertEqual(tables.baccarat_settle(r, "player", 100)["payout"], 200.0)

    def test_tie_pays_eight_to_one(self):
        r = {"outcome": "tie"}
        self.assertEqual(tables.baccarat_settle(r, "tie", 100)["payout"], 900.0)

    def test_tie_pushes_player_and_banker(self):
        r = {"outcome": "tie"}
        for bt in ("player", "banker"):
            s = tables.baccarat_settle(r, bt, 100)
            self.assertEqual(s["verdict"], "push")
            self.assertEqual(s["payout"], 100.0)

    def test_losing_bet_returns_nothing(self):
        self.assertEqual(tables.baccarat_settle({"outcome": "banker"}, "player", 100)["payout"], 0.0)

    def test_unknown_bet_rejected(self):
        with self.assertRaises(ValueError):
            tables.baccarat_settle({"outcome": "tie"}, "dragon", 100)

    def test_outcome_matches_the_higher_total(self):
        for n in range(500):
            deck = tables.deck_for("baccarat", SS, CS, n)
            r = tables.baccarat_deal(deck)
            if r["player_points"] > r["banker_points"]:
                self.assertEqual(r["outcome"], "player")
            elif r["banker_points"] > r["player_points"]:
                self.assertEqual(r["outcome"], "banker")
            else:
                self.assertEqual(r["outcome"], "tie")

    def test_no_hand_ever_exceeds_three_cards(self):
        for n in range(2000):
            deck = tables.deck_for("baccarat", SS, CS, n)
            r = tables.baccarat_deal(deck)
            self.assertLessEqual(len(r["player"]), 3)
            self.assertLessEqual(len(r["banker"]), 3)

    def test_cards_come_off_the_top_of_the_shoe_in_order(self):
        """
        An eight-deck shoe holds eight of every card, so identity is not enough
        to prove nothing was dealt twice — instead check that exactly the top
        N cards were consumed, N being the number of cards on the table.
        """
        for n in range(500):
            deck = tables.deck_for("baccarat", SS, CS, n)
            r = tables.baccarat_deal(deck)
            dealt = r["player"] + r["banker"]
            self.assertEqual(sorted(dealt), sorted(deck[:len(dealt)]))

    # Published eight-deck punto banco probabilities.
    REFERENCE = {"player": 0.446247, "banker": 0.458597, "tie": 0.095156}

    def test_edges_follow_exactly_from_the_published_probabilities(self):
        """
        Zero-variance check of the payout logic: feed it the known outcome
        probabilities and the advertised edges must fall out exactly. This is
        deliberately separate from the sampling test below — a Monte Carlo run
        on the tie bet needs millions of coups to resolve 0.1%, so it can never
        pin down the payout arithmetic.
        """
        for bet_type, spec in tables.BACCARAT["bets"].items():
            ev = 0.0
            for outcome, p in self.REFERENCE.items():
                payout = tables.baccarat_settle({"outcome": outcome}, bet_type, 100.0)["payout"]
                ev += p * (payout - 100.0)
            edge = -ev / 100 * 100
            claimed = float(spec["edge"].rstrip("%"))
            self.assertAlmostEqual(edge, claimed, delta=0.02,
                                   msg=f"{bet_type}: {edge:.3f}% vs advertised {claimed}%")

    @unittest.skipUnless(SLOW, "set DAVIDSINO_SLOW=1")
    def test_dealt_outcomes_match_published_frequencies(self):
        """
        Checks the drawing rules, not the payouts. Outcome frequencies converge
        far faster than the tie bet's bankroll does, so this is the part of
        baccarat worth sampling.
        """
        N = 200000
        seen = Counter(tables.baccarat_deal(tables.deck_for("baccarat", SS, CS, n))["outcome"]
                       for n in range(N))
        for outcome, p in self.REFERENCE.items():
            observed = seen[outcome] / N
            self.assertAlmostEqual(observed, p, delta=0.005,
                                   msg=f"{outcome} came up {observed:.4%}, expected {p:.4%}")


# ============================================================
# Fan-Tan
# ============================================================
class TestFanTan(unittest.TestCase):
    def test_pile_range_splits_evenly_four_ways(self):
        counts = Counter((b % 4) or 4 for b in
                         range(tables.FAN_TAN_MIN_BEADS, tables.FAN_TAN_MAX_BEADS + 1))
        self.assertEqual(set(counts.values()), {24},
                         "pile range must be a multiple of four for a fair remainder")

    def test_draw_stays_in_range(self):
        for n in range(3000):
            d = tables.fan_tan_draw(SS, CS, n)
            self.assertGreaterEqual(d["beads"], tables.FAN_TAN_MIN_BEADS)
            self.assertLessEqual(d["beads"], tables.FAN_TAN_MAX_BEADS)
            self.assertIn(d["result"], (1, 2, 3, 4))

    def test_remainder_of_zero_counts_as_four(self):
        self.assertEqual(tables.fan_tan_settle(
            {"beads": 24, "result": 4}, "fan", [4], 100)["verdict"], "win")

    def test_draw_is_deterministic(self):
        self.assertEqual(tables.fan_tan_draw(SS, CS, 5), tables.fan_tan_draw(SS, CS, 5))

    def test_fan_pays_three_to_one_less_commission(self):
        s = tables.fan_tan_settle({"beads": 27, "result": 3}, "fan", [3], 100)
        self.assertEqual(s["verdict"], "win")
        self.assertEqual(s["payout"], 385.0)          # 100 + 300 - 5% of 300

    def test_kwok_pays_even_money_less_commission(self):
        s = tables.fan_tan_settle({"beads": 26, "result": 2}, "kwok", [1, 2], 100)
        self.assertEqual(s["payout"], 195.0)

    def test_nga_pays_one_to_three_less_commission(self):
        s = tables.fan_tan_settle({"beads": 26, "result": 2}, "nga", [1, 2, 3], 100)
        self.assertAlmostEqual(s["payout"], 100 + (100 / 3) * 0.95, places=2)

    def test_missing_the_number_loses_everything(self):
        s = tables.fan_tan_settle({"beads": 25, "result": 1}, "fan", [3], 100)
        self.assertEqual(s["verdict"], "lose")
        self.assertEqual(s["payout"], 0.0)

    def test_wrong_pick_count_rejected(self):
        with self.assertRaises(ValueError):
            tables.fan_tan_settle({"beads": 25, "result": 1}, "fan", [1, 2], 100)
        with self.assertRaises(ValueError):
            tables.fan_tan_settle({"beads": 25, "result": 1}, "kwok", [1], 100)

    def test_out_of_range_pick_rejected(self):
        with self.assertRaises(ValueError):
            tables.fan_tan_settle({"beads": 25, "result": 1}, "fan", [7], 100)
        with self.assertRaises(ValueError):
            tables.fan_tan_settle({"beads": 25, "result": 1}, "fan", [0], 100)

    def test_duplicate_picks_cannot_fake_a_wider_bet(self):
        with self.assertRaises(ValueError):
            tables.fan_tan_settle({"beads": 25, "result": 1}, "kwok", [2, 2], 100)

    def test_unknown_bet_rejected(self):
        with self.assertRaises(ValueError):
            tables.fan_tan_settle({"beads": 25, "result": 1}, "monte", [1], 100)

    def test_advertised_edges_are_exact(self):
        """Every remainder is 1-in-4, so the edge enumerates in four steps."""
        for bet_type, spec in tables.FAN_TAN["bets"].items():
            picks = list(range(1, spec["picks"] + 1))
            ev = sum(0.25 * (tables.fan_tan_settle({"beads": 0, "result": r},
                                                   bet_type, picks, 100.0)["payout"] - 100.0)
                     for r in (1, 2, 3, 4))
            edge = -ev / 100 * 100
            claimed = float(spec["edge"].rstrip("%"))
            self.assertAlmostEqual(edge, claimed, delta=0.01,
                                   msg=f"{bet_type}: measured {edge:.3f}% vs advertised {claimed}%")


# ============================================================
# Mississippi Stud
# ============================================================
class TestHandClassification(unittest.TestCase):
    def test_known_hands(self):
        cases = [
            (["10♠", "J♠", "Q♠", "K♠", "A♠"], "royal_flush"),
            (["5♥", "6♥", "7♥", "8♥", "9♥"], "straight_flush"),
            (["A♠", "2♠", "3♠", "4♠", "5♠"], "straight_flush"),   # the wheel
            (["7♠", "7♥", "7♦", "7♣", "2♠"], "quads"),
            (["8♠", "8♥", "8♦", "3♣", "3♠"], "full_house"),
            (["2♦", "5♦", "9♦", "J♦", "K♦"], "flush"),
            (["4♠", "5♥", "6♦", "7♣", "8♠"], "straight"),
            (["A♠", "2♥", "3♦", "4♣", "5♠"], "straight"),
            (["9♠", "9♥", "9♦", "2♣", "5♠"], "trips"),
            (["9♠", "9♥", "4♦", "4♣", "5♠"], "two_pair"),
            (["J♠", "J♥", "4♦", "7♣", "5♠"], "high_pair"),
            (["A♠", "A♥", "4♦", "7♣", "5♠"], "high_pair"),
            (["10♠", "10♥", "4♦", "7♣", "2♠"], "mid_pair"),
            (["6♠", "6♥", "4♦", "7♣", "2♠"], "mid_pair"),
            (["5♠", "5♥", "4♦", "7♣", "2♠"], "low_pair"),
            (["2♠", "5♥", "9♦", "J♣", "K♠"], "nothing"),
        ]
        for cards, expected in cases:
            self.assertEqual(tables.classify_five(cards), expected, cards)

    def test_king_high_straight_is_not_royal(self):
        self.assertEqual(tables.classify_five(["9♠", "10♠", "J♠", "Q♠", "K♠"]), "straight_flush")

    def test_ace_high_wrap_is_not_a_straight(self):
        self.assertEqual(tables.classify_five(["Q♠", "K♥", "A♦", "2♣", "3♠"]), "nothing")

    def test_needs_five_cards(self):
        with self.assertRaises(ValueError):
            tables.classify_five(["A♠", "K♥"])

    def test_pair_thresholds_sit_where_the_paytable_says(self):
        low = ["2", "3", "4", "5"]
        mid = ["6", "7", "8", "9", "10"]
        high = ["J", "Q", "K", "A"]
        for r in low:
            self.assertEqual(tables.classify_five([r + "♠", r + "♥", "7♦", "9♣", "Q♠"]), "low_pair")
        for r in mid:
            other = ["3♦", "5♣", "Q♠"]
            self.assertEqual(tables.classify_five([r + "♠", r + "♥"] + other), "mid_pair")
        for r in high:
            self.assertEqual(tables.classify_five([r + "♠", r + "♥", "3♦", "5♣", "9♠"]), "high_pair")

    @unittest.skipUnless(SLOW, "set DAVIDSINO_SLOW=1")
    def test_every_five_card_hand_matches_published_frequencies(self):
        counts = Counter(tables.classify_five(list(h))
                         for h in itertools.combinations(tables.build_deck(1), 5))
        known = {
            "royal_flush": 4, "straight_flush": 36, "quads": 624, "full_house": 3744,
            "flush": 5108, "straight": 10200, "trips": 54912, "two_pair": 123552,
            "high_pair": 337920, "mid_pair": 422400, "low_pair": 337920, "nothing": 1302540,
        }
        self.assertEqual(sum(counts.values()), 2598960)
        for category, expected in known.items():
            self.assertEqual(counts[category], expected, category)


class TestMississippiPlay(unittest.TestCase):
    def _stacked(self, cards):
        rest = [c for c in tables.build_deck(1) if c not in cards]
        return list(cards) + rest

    def test_deal_gives_two_hole_cards_and_no_community(self):
        deck = self._stacked(["A♠", "K♥"])
        st = tables.mississippi_start(deck, 25)
        self.assertEqual(st["hole"], ["A♠", "K♥"])
        self.assertEqual(st["community"], [])
        self.assertEqual(st["street"], 3)
        self.assertEqual(st["wagered"], 25)

    def test_each_raise_reveals_one_community_card(self):
        deck = self._stacked(["A♠", "K♥", "Q♦", "J♣", "10♠"])
        st = tables.mississippi_start(deck, 25)
        for expected in (1, 2, 3):
            tables.mississippi_act(st, deck, "raise", 1)
            self.assertEqual(len(st["community"]), expected)

    def test_full_hand_pays_the_paytable_on_everything_wagered(self):
        deck = self._stacked(["A♠", "K♠", "Q♠", "J♠", "10♠"])   # royal flush
        st = tables.mississippi_start(deck, 25)
        for _ in range(3):
            tables.mississippi_act(st, deck, "raise", 3)
        self.assertEqual(st["category"], "royal_flush")
        self.assertEqual(st["wagered"], 25 + 75 * 3)             # ante + three 3x raises
        self.assertEqual(st["payout"], st["wagered"] * 501)      # 500:1 plus the stake

    def test_mid_pair_pushes(self):
        deck = self._stacked(["8♠", "8♥", "2♦", "5♣", "K♠"])
        st = tables.mississippi_start(deck, 25)
        for _ in range(3):
            tables.mississippi_act(st, deck, "raise", 1)
        self.assertEqual(st["category"], "mid_pair")
        self.assertEqual(st["payout"], st["wagered"])            # stake back, no profit

    def test_low_pair_loses(self):
        deck = self._stacked(["3♠", "3♥", "2♦", "5♣", "K♠"])
        st = tables.mississippi_start(deck, 25)
        for _ in range(3):
            tables.mississippi_act(st, deck, "raise", 1)
        self.assertEqual(st["category"], "low_pair")
        self.assertEqual(st["payout"], 0)

    def test_fold_forfeits_and_ends_the_hand(self):
        deck = self._stacked(["2♠", "7♥", "Q♦", "J♣", "10♠"])
        st = tables.mississippi_start(deck, 25)
        tables.mississippi_act(st, deck, "fold")
        self.assertEqual(st["stage"], "folded")
        self.assertEqual(st["payout"], 0)
        self.assertEqual(st["wagered"], 25)

    def test_fold_still_shows_the_cards_so_the_deal_can_be_audited(self):
        deck = self._stacked(["2♠", "7♥", "Q♦", "J♣", "10♠"])
        st = tables.mississippi_start(deck, 25)
        tables.mississippi_act(st, deck, "fold")
        self.assertEqual(st["community"], ["Q♦", "J♣", "10♠"])

    def test_cannot_act_after_the_hand_ends(self):
        deck = self._stacked(["2♠", "7♥", "Q♦", "J♣", "10♠"])
        st = tables.mississippi_start(deck, 25)
        tables.mississippi_act(st, deck, "fold")
        with self.assertRaises(ValueError):
            tables.mississippi_act(st, deck, "raise", 1)

    def test_raise_multiple_must_be_one_two_or_three(self):
        deck = self._stacked(["A♠", "K♥"])
        st = tables.mississippi_start(deck, 25)
        for bad in (0, 4, 10, -1):
            with self.assertRaises(ValueError):
                tables.mississippi_act(st, deck, "raise", bad)

    def test_unknown_action_rejected(self):
        deck = self._stacked(["A♠", "K♥"])
        st = tables.mississippi_start(deck, 25)
        with self.assertRaises(ValueError):
            tables.mississippi_act(st, deck, "check")

    def test_community_cards_do_not_depend_on_player_choices(self):
        """The whole point: raising 1x or 3x cannot change which cards arrive."""
        deck = tables.deck_for("mississippi", SS, CS, 42)
        a = tables.mississippi_start(deck, 25)
        b = tables.mississippi_start(deck, 25)
        for mult, st in ((1, a), (3, b)):
            for _ in range(3):
                tables.mississippi_act(st, deck, "raise", mult)
        self.assertEqual(a["community"], b["community"])
        self.assertEqual(a["category"], b["category"])

    def test_house_always_keeps_an_edge_on_a_max_raise_strategy(self):
        """Raising 3x blind is the worst play there is; it must never beat the house."""
        wagered = payout = 0.0
        for n in range(4000):
            deck = tables.deck_for("mississippi", SS, CS, n)
            st = tables.mississippi_start(deck, 25)
            while st["stage"] == "playing":
                tables.mississippi_act(st, deck, "raise", 3)
            wagered += st["wagered"]
            payout += st["payout"]
        self.assertLess(payout, wagered, "blind max-raise beat the house")


# ============================================================
# Registry
# ============================================================
class TestRegistry(unittest.TestCase):
    def test_four_games_listed(self):
        keys = {g["key"] for g in tables.table_list()}
        self.assertEqual(keys, {"blackjack", "baccarat", "fan_tan", "mississippi"})

    def test_lobby_never_leaks_a_server_seed(self):
        blob = repr(tables.table_list())
        self.assertNotIn("server_seed", blob)

    def test_every_game_declares_its_limits_and_rules(self):
        for g in tables.table_list():
            self.assertGreater(g["min_bet"], 0)
            self.assertGreater(g["max_bet"], g["min_bet"])
            self.assertTrue(g["rules"])
            self.assertIsInstance(g["live"], bool)

    def test_live_flag_matches_the_engine(self):
        for g in tables.table_list():
            self.assertEqual(g["live"], g["key"] in tables.ROUND_GAMES)

    def test_deck_size_per_game(self):
        self.assertEqual(len(tables.deck_for("blackjack", SS, CS, 0)), 312)
        self.assertEqual(len(tables.deck_for("baccarat", SS, CS, 0)), 416)
        self.assertEqual(len(tables.deck_for("mississippi", SS, CS, 0)), 52)

    @unittest.skipUnless(SLOW, "set DAVIDSINO_SLOW=1")
    def test_blackjack_edge_under_basic_strategy_stays_under_one_percent(self):
        from bj_basic_strategy import simulate
        edge = simulate(100000)
        self.assertLess(edge, 1.0, f"blackjack edge {edge:.3f}% is too rich")
        self.assertGreater(edge, 0.0, f"blackjack edge {edge:.3f}% favours the player")


if __name__ == "__main__":
    unittest.main(verbosity=2)
