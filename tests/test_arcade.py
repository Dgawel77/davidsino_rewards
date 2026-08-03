"""
Tests for the provably fair arcade games: crash, plinko and mines.

Runs under pytest OR plain stdlib:  python3 tests/test_arcade.py

Slow statistical checks are gated behind DAVIDSINO_SLOW=1. The important ones
are not statistical at all — plinko's return and mines' edge are both exactly
enumerable, so they are checked exactly.
"""
import math
import os
import sys
import unittest
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import arcade  # noqa: E402

SS = "a" * 64
CS = "davidtest"
SLOW = os.getenv("DAVIDSINO_SLOW") == "1"


# ============================================================
# Crash
# ============================================================
class TestCrashCurve(unittest.TestCase):
    def test_starts_at_one(self):
        self.assertEqual(arcade.crash_multiplier_at(0), 1.00)
        self.assertEqual(arcade.crash_multiplier_at(-5), 1.00)

    def test_doubles_on_schedule(self):
        d = arcade.CRASH_DOUBLE_SECONDS
        self.assertAlmostEqual(arcade.crash_multiplier_at(d), 2.00, places=2)
        self.assertAlmostEqual(arcade.crash_multiplier_at(2 * d), 4.00, places=2)
        self.assertAlmostEqual(arcade.crash_multiplier_at(3 * d), 8.00, places=2)

    def test_curve_is_monotonic(self):
        prev = 0
        for i in range(0, 200):
            m = arcade.crash_multiplier_at(i * 0.1)
            self.assertGreaterEqual(m, prev)
            prev = m

    def test_time_to_inverts_the_curve(self):
        for target in (1.5, 2.0, 3.7, 10.0, 100.0):
            t = arcade.crash_time_to(target)
            self.assertAlmostEqual(arcade.crash_multiplier_at(t), target, delta=0.02)


class TestCrashPoint(unittest.TestCase):
    def test_never_below_one(self):
        for n in range(5000):
            self.assertGreaterEqual(arcade.crash_point(SS, CS, n), 1.00)

    def test_never_above_the_cap(self):
        for n in range(5000):
            self.assertLessEqual(arcade.crash_point(SS, CS, n), arcade.CRASH_MAX)

    def test_deterministic(self):
        self.assertEqual(arcade.crash_point(SS, CS, 11), arcade.crash_point(SS, CS, 11))

    def test_seeds_change_it(self):
        self.assertNotEqual(arcade.crash_point(SS, CS, 1), arcade.crash_point(SS, CS, 2))
        self.assertNotEqual(arcade.crash_point(SS, "alice", 1), arcade.crash_point(SS, "bob", 1))
        self.assertNotEqual(arcade.crash_point("a" * 64, CS, 1), arcade.crash_point("b" * 64, CS, 1))

    def test_two_decimals_only(self):
        for n in range(500):
            m = arcade.crash_point(SS, CS, n)
            self.assertEqual(round(m, 2), m)

    def test_survival_curve_matches_theory(self):
        """
        P(bust >= x) should be (1 - edge) / x. That identity is the whole reason
        the edge is the same at every cash-out target, so it is worth pinning.
        """
        N = 60000
        points = [arcade.crash_point(SS, CS, n) for n in range(N)]
        for target in (1.20, 1.50, 2.00, 4.00):
            hit = sum(1 for p in points if p >= target) / N
            theory = (1 - arcade.HOUSE_EDGE) / target
            self.assertAlmostEqual(hit, theory, delta=0.012,
                                   msg=f"{target}x hit {hit:.4f}, theory {theory:.4f}")

    @unittest.skipUnless(SLOW, "set DAVIDSINO_SLOW=1")
    def test_return_is_near_one_minus_edge_at_every_target(self):
        N = 400000
        points = [arcade.crash_point(SS, CS, n) for n in range(N)]
        for target in (1.10, 1.50, 2.00, 3.00, 5.00):
            ret = sum(1 for p in points if p >= target) / N * target
            self.assertAlmostEqual(ret, 1 - arcade.HOUSE_EDGE, delta=0.02,
                                   msg=f"target {target}x returned {ret:.4f}")


class TestCrashRound(unittest.TestCase):
    def test_auto_target_wins_when_the_bust_is_higher(self):
        st = arcade.crash_start(3.00, 100, target=2.00)
        self.assertEqual(st["stage"], "settled")
        self.assertEqual(st["cashed_at"], 2.00)
        self.assertEqual(st["payout"], 200.0)

    def test_auto_target_loses_when_the_bust_is_lower(self):
        st = arcade.crash_start(1.50, 100, target=2.00)
        self.assertEqual(st["payout"], 0.0)
        self.assertIsNone(st["cashed_at"])

    def test_auto_target_wins_on_an_exact_tie(self):
        st = arcade.crash_start(2.00, 100, target=2.00)
        self.assertEqual(st["payout"], 200.0)

    def test_target_must_be_sane(self):
        for bad in (1.00, 0.5, -1, arcade.CRASH_MAX + 1):
            with self.assertRaises(ValueError):
                arcade.crash_start(5.00, 100, target=bad)

    def test_manual_cash_out_pays_the_curve(self):
        st = arcade.crash_start(10.00, 100)
        self.assertEqual(st["stage"], "flying")
        arcade.crash_cash_out(st, arcade.CRASH_DOUBLE_SECONDS)   # exactly 2x
        self.assertEqual(st["cashed_at"], 2.00)
        self.assertEqual(st["payout"], 200.0)

    def test_cashing_out_after_the_bust_pays_nothing(self):
        st = arcade.crash_start(2.00, 100)
        arcade.crash_cash_out(st, arcade.CRASH_DOUBLE_SECONDS * 3)   # ~8x, long gone
        self.assertEqual(st["payout"], 0.0)
        self.assertIsNone(st["cashed_at"])

    def test_cannot_cash_out_twice(self):
        st = arcade.crash_start(10.00, 100)
        arcade.crash_cash_out(st, 1.0)
        with self.assertRaises(ValueError):
            arcade.crash_cash_out(st, 1.0)

    def test_abandoned_round_settles_as_a_loss(self):
        st = arcade.crash_start(3.00, 100)
        arcade.crash_expire(st)
        self.assertEqual(st["stage"], "settled")
        self.assertEqual(st["payout"], 0.0)

    def test_bust_is_hidden_while_flying_and_shown_after(self):
        st = arcade.crash_start(4.20, 100)
        self.assertNotIn("bust", arcade.crash_public(st, 1.0))
        arcade.crash_cash_out(st, 1.0)
        self.assertEqual(arcade.crash_public(st, 1.0)["bust"], 4.20)

    def test_payout_never_exceeds_the_bust(self):
        for n in range(2000):
            bust = arcade.crash_point(SS, CS, n)
            st = arcade.crash_start(bust, 100)
            # Try to cash out slightly past the bust every time.
            arcade.crash_cash_out(st, arcade.crash_time_to(bust) + 0.05)
            self.assertEqual(st["payout"], 0.0)


# ============================================================
# Plinko
# ============================================================
class TestPlinko(unittest.TestCase):
    def test_three_risk_levels(self):
        self.assertEqual(set(arcade.PLINKO_TABLES), {"low", "medium", "high"})

    def test_table_has_one_bucket_per_outcome(self):
        for table in arcade.PLINKO_TABLES.values():
            self.assertEqual(len(table), arcade.PLINKO_ROWS + 1)

    def test_tables_are_symmetric(self):
        for risk, table in arcade.PLINKO_TABLES.items():
            self.assertEqual(table, table[::-1], risk)

    def test_edges_pay_more_than_the_middle(self):
        for risk, table in arcade.PLINKO_TABLES.items():
            middle = arcade.PLINKO_ROWS // 2
            self.assertGreater(table[0], table[middle], risk)

    def test_higher_risk_pays_a_bigger_top_and_a_worse_middle(self):
        low, med, high = (arcade.PLINKO_TABLES[r] for r in ("low", "medium", "high"))
        mid = arcade.PLINKO_ROWS // 2
        self.assertLess(low[0], med[0])
        self.assertLess(med[0], high[0])
        self.assertGreater(low[mid], med[mid])
        self.assertGreater(med[mid], high[mid])

    def test_return_is_exact_and_matches_the_advertised_edge(self):
        """13 buckets and a binomial — no sampling needed, this is exact."""
        for risk in arcade.PLINKO_TABLES:
            rtp = arcade.plinko_rtp(risk)
            self.assertAlmostEqual(rtp, 1 - arcade.HOUSE_EDGE, delta=0.005,
                                   msg=f"{risk} returns {rtp:.5f}")

    def test_advertised_rtp_matches_the_table(self):
        for risk, spec in arcade.PLINKO["risks"].items():
            self.assertAlmostEqual(spec["rtp"], arcade.plinko_rtp(risk) * 100, places=2)
            self.assertEqual(spec["top"], max(arcade.PLINKO_TABLES[risk]))

    def test_drop_path_length_matches_the_rows(self):
        d = arcade.plinko_drop("medium", SS, CS, 0)
        self.assertEqual(len(d["path"]), arcade.PLINKO_ROWS)
        self.assertTrue(all(step in ("L", "R") for step in d["path"]))

    def test_bucket_is_the_number_of_rights(self):
        for n in range(300):
            d = arcade.plinko_drop("high", SS, CS, n)
            self.assertEqual(d["bucket"], d["path"].count("R"))

    def test_multiplier_matches_the_bucket(self):
        for n in range(300):
            d = arcade.plinko_drop("low", SS, CS, n)
            self.assertEqual(d["multiplier"], arcade.PLINKO_TABLES["low"][d["bucket"]])

    def test_deterministic_and_seed_sensitive(self):
        self.assertEqual(arcade.plinko_drop("low", SS, CS, 3),
                         arcade.plinko_drop("low", SS, CS, 3))
        self.assertNotEqual(arcade.plinko_drop("low", SS, CS, 3)["path"],
                            arcade.plinko_drop("low", SS, CS, 4)["path"])

    def test_risk_changes_the_payout_but_not_the_path(self):
        a = arcade.plinko_drop("low", SS, CS, 9)
        b = arcade.plinko_drop("high", SS, CS, 9)
        self.assertEqual(a["path"], b["path"], "risk must not steer the ball")

    def test_unknown_risk_rejected(self):
        with self.assertRaises(ValueError):
            arcade.plinko_drop("insane", SS, CS, 0)

    def test_settle_pays_bet_times_multiplier(self):
        d = {"path": [], "bucket": 6, "multiplier": 2.5}
        self.assertEqual(arcade.plinko_settle(d, 100)["payout"], 250.0)

    def test_buckets_land_roughly_on_the_binomial(self):
        N = 40000
        seen = Counter(arcade.plinko_drop("medium", SS, CS, n)["bucket"] for n in range(N))
        rows = arcade.PLINKO_ROWS
        for k in range(rows + 1):
            expected = math.comb(rows, k) / 2 ** rows
            self.assertAlmostEqual(seen[k] / N, expected, delta=0.012, msg=f"bucket {k}")


# ============================================================
# Mines
# ============================================================
class TestMines(unittest.TestCase):
    def test_layout_has_the_right_number_of_mines(self):
        for count in (1, 3, 5, 12, 24):
            layout = arcade.mines_layout(count, SS, CS, 0)
            self.assertEqual(len(layout), count)
            self.assertEqual(len(set(layout)), count)
            self.assertTrue(all(0 <= t < arcade.MINES_TILES for t in layout))

    def test_layout_is_deterministic_and_seed_sensitive(self):
        self.assertEqual(arcade.mines_layout(3, SS, CS, 1), arcade.mines_layout(3, SS, CS, 1))
        self.assertNotEqual(arcade.mines_layout(3, SS, CS, 1), arcade.mines_layout(3, SS, CS, 2))

    def test_mine_count_bounds_enforced(self):
        for bad in (0, -1, 25, 100):
            with self.assertRaises(ValueError):
                arcade.mines_layout(bad, SS, CS, 0)

    def test_every_tile_can_be_mined(self):
        seen = set()
        for n in range(3000):
            seen.update(arcade.mines_layout(3, SS, CS, n))
        self.assertEqual(len(seen), arcade.MINES_TILES, "some tile was never a mine")

    def test_multiplier_grows_with_each_safe_pick(self):
        prev = 1.0
        for k in range(1, 20):
            m = arcade.mines_multiplier(3, k)
            self.assertGreater(m, prev)
            prev = m

    def test_multiplier_matches_the_fair_price_less_the_edge(self):
        """The whole paytable is one formula; check it against the probability."""
        for mine_count in (1, 3, 5, 10, 24):
            safe = arcade.MINES_TILES - mine_count
            for k in range(1, min(safe, 8) + 1):
                survive = math.comb(safe, k) / math.comb(arcade.MINES_TILES, k)
                edge = 1 - arcade.mines_multiplier(mine_count, k) * survive
                self.assertAlmostEqual(edge, arcade.HOUSE_EDGE, delta=0.006,
                                       msg=f"{mine_count} mines, {k} picks: edge {edge:.4f}")

    def test_more_mines_pays_more_for_the_same_depth(self):
        self.assertLess(arcade.mines_multiplier(1, 3), arcade.mines_multiplier(5, 3))
        self.assertLess(arcade.mines_multiplier(5, 3), arcade.mines_multiplier(10, 3))

    def test_cannot_ask_for_more_picks_than_safe_tiles(self):
        with self.assertRaises(ValueError):
            arcade.mines_multiplier(24, 2)

    def test_safe_pick_advances_without_settling(self):
        st = arcade.mines_start([0, 1, 2], 3, 100)
        arcade.mines_pick(st, 5)
        self.assertEqual(st["stage"], "picking")
        self.assertEqual(st["picked"], [5])
        self.assertIsNone(st["hit"])

    def test_hitting_a_mine_ends_it_with_nothing(self):
        st = arcade.mines_start([7], 1, 100)
        arcade.mines_pick(st, 7)
        self.assertEqual(st["stage"], "settled")
        self.assertEqual(st["payout"], 0.0)
        self.assertEqual(st["hit"], 7)

    def test_cash_out_pays_the_ladder(self):
        st = arcade.mines_start([0, 1, 2], 3, 100)
        for tile in (5, 6, 7):
            arcade.mines_pick(st, tile)
        arcade.mines_cash_out(st)
        self.assertEqual(st["payout"], round(100 * arcade.mines_multiplier(3, 3), 2))

    def test_cannot_cash_out_with_nothing_turned_over(self):
        st = arcade.mines_start([0], 1, 100)
        with self.assertRaises(ValueError):
            arcade.mines_cash_out(st)

    def test_cannot_pick_the_same_tile_twice(self):
        st = arcade.mines_start([0], 1, 100)
        arcade.mines_pick(st, 5)
        with self.assertRaises(ValueError):
            arcade.mines_pick(st, 5)

    def test_tile_must_be_on_the_board(self):
        st = arcade.mines_start([0], 1, 100)
        for bad in (-1, 25, 99, None, "3"):
            with self.assertRaises(ValueError):
                arcade.mines_pick(st, bad)

    def test_clearing_every_safe_tile_settles_automatically(self):
        mines = list(range(24))                  # 24 mines, one safe tile
        st = arcade.mines_start(mines, 24, 100)
        arcade.mines_pick(st, 24)
        self.assertEqual(st["stage"], "settled")
        self.assertEqual(st["payout"], round(100 * arcade.mines_multiplier(24, 1), 2))

    def test_cannot_act_after_settling(self):
        st = arcade.mines_start([7], 1, 100)
        arcade.mines_pick(st, 7)
        with self.assertRaises(ValueError):
            arcade.mines_pick(st, 8)
        with self.assertRaises(ValueError):
            arcade.mines_cash_out(st)

    def test_layout_hidden_while_live_and_revealed_after(self):
        st = arcade.mines_start([1, 2, 3], 3, 100)
        arcade.mines_pick(st, 10)
        self.assertNotIn("mines", arcade.mines_public(st))
        arcade.mines_cash_out(st)
        self.assertEqual(arcade.mines_public(st)["mines"], [1, 2, 3])

    def test_public_view_shows_what_the_next_pick_would_pay(self):
        st = arcade.mines_start([1, 2, 3], 3, 100)
        self.assertEqual(arcade.mines_public(st)["next_multiplier"],
                         arcade.mines_multiplier(3, 1))
        arcade.mines_pick(st, 10)
        self.assertEqual(arcade.mines_public(st)["next_multiplier"],
                         arcade.mines_multiplier(3, 2))

    def test_a_mine_does_not_count_as_a_safe_pick(self):
        st = arcade.mines_start([9], 1, 100)
        arcade.mines_pick(st, 1)
        arcade.mines_pick(st, 9)
        self.assertEqual(arcade.mines_public(st)["picks"], 1)


# ============================================================
# Registry
# ============================================================
class TestRegistry(unittest.TestCase):
    def test_three_games(self):
        self.assertEqual({g["key"] for g in arcade.game_list()},
                         {"crash", "plinko", "mines"})

    def test_never_leaks_a_seed_or_a_layout(self):
        blob = repr(arcade.game_list())
        self.assertNotIn("server_seed", blob)

    def test_every_game_declares_limits_and_rules(self):
        for g in arcade.game_list():
            self.assertGreater(g["min_bet"], 0)
            self.assertGreater(g["max_bet"], g["min_bet"])
            self.assertTrue(g["rules"])

    def test_live_flag_matches_the_engine(self):
        for g in arcade.game_list():
            self.assertEqual(g["live"], g["key"] in arcade.ROUND_GAMES)

    def test_keys_do_not_collide_with_the_table_games(self):
        import tables
        self.assertFalse(set(arcade.GAMES) & set(tables.TABLES))


if __name__ == "__main__":
    unittest.main(verbosity=2)
