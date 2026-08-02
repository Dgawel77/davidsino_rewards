"""
Tests for the provably fair slot engine.

Runs under pytest OR plain stdlib:  python3 tests/test_slots.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import slots  # noqa: E402


SS = "a" * 64        # deterministic test server seed
CS = "davidtest"     # deterministic test client seed


class TestMachines(unittest.TestCase):
    def test_three_machines_listed(self):
        keys = {m["key"] for m in slots.machine_list()}
        self.assertEqual(keys, {"classic", "diamond_dave", "vig_city"})

    def test_machine_list_exposes_paytable_but_not_reel_weights(self):
        m = slots.machine_list()[0]
        self.assertIn("paytable", m)
        self.assertNotIn("reel_weights", m)
        self.assertNotIn("pay3", m)


class TestValidation(unittest.TestCase):
    def test_unknown_machine_rejected(self):
        with self.assertRaises(ValueError):
            slots.spin("nope", 100, SS, CS, 1)

    def test_bet_below_minimum_rejected(self):
        with self.assertRaises(ValueError):
            slots.spin("classic", 5, SS, CS, 1)

    def test_bet_above_maximum_rejected(self):
        with self.assertRaises(ValueError):
            slots.spin("classic", 10_000, SS, CS, 1)


class TestSpinShape(unittest.TestCase):
    def test_reel3_returns_one_row_of_three(self):
        r = slots.spin("classic", 10, SS, CS, 1)
        self.assertEqual(len(r["grid"]), 1)
        self.assertEqual(len(r["grid"][0]), 3)
        self.assertGreaterEqual(r["win"], 0)
        self.assertEqual(r["bet"], 10)

    def test_lines5x3_returns_three_rows_of_five(self):
        r = slots.spin("vig_city", 25, SS, CS, 1)
        self.assertEqual(len(r["grid"]), 3)
        for row in r["grid"]:
            self.assertEqual(len(row), 5)

    def test_spin_reports_seed_context(self):
        r = slots.spin("classic", 10, SS, CS, 7)
        self.assertEqual(r["nonce"], 7)
        self.assertEqual(r["client_seed"], CS)
        self.assertEqual(r["server_seed_hash"], slots.seed_hash(SS))


class TestProvableFairness(unittest.TestCase):
    def test_same_inputs_produce_same_grid(self):
        a = slots.spin("vig_city", 25, SS, CS, 42)
        b = slots.spin("vig_city", 25, SS, CS, 42)
        self.assertEqual(a["grid"], b["grid"])
        self.assertEqual(a["win"], b["win"])

    def test_different_nonce_produces_different_grid(self):
        grids = {
            tuple(tuple(row) for row in slots.spin("vig_city", 25, SS, CS, n)["grid"])
            for n in range(20)
        }
        self.assertGreater(len(grids), 1, "nonce must change the outcome")

    def test_different_client_seed_produces_different_grid(self):
        a = slots.spin("vig_city", 25, SS, "seed-one", 1)
        b = slots.spin("vig_city", 25, SS, "seed-two", 1)
        self.assertNotEqual(a["grid"], b["grid"])

    def test_different_server_seed_produces_different_grid(self):
        a = slots.spin("vig_city", 25, "a" * 64, CS, 1)
        b = slots.spin("vig_city", 25, "b" * 64, CS, 1)
        self.assertNotEqual(a["grid"], b["grid"])

    def test_seed_hash_is_sha256_of_server_seed(self):
        import hashlib
        self.assertEqual(slots.seed_hash(SS), hashlib.sha256(SS.encode()).hexdigest())

    def test_verify_spin_reproduces_original_and_confirms_commitment(self):
        original = slots.spin("classic", 10, SS, CS, 99)
        check = slots.verify_spin("classic", 10, SS, CS, 99)
        self.assertEqual(check["grid"], original["grid"])
        self.assertEqual(check["win"], original["win"])
        self.assertTrue(check["server_seed_hash_matches"])

    def test_float_stream_yields_requested_count_in_unit_interval(self):
        vals = list(slots.float_stream(SS, CS, 1, 15))
        self.assertEqual(len(vals), 15)
        for v in vals:
            self.assertGreaterEqual(v, 0.0)
            self.assertLess(v, 1.0)

    def test_float_stream_crosses_digest_boundary_deterministically(self):
        # 15 floats needs 2 HMAC digests (8 floats each); first 8 must match an 8-float run
        eight = list(slots.float_stream(SS, CS, 1, 8))
        fifteen = list(slots.float_stream(SS, CS, 1, 15))
        self.assertEqual(eight, fifteen[:8])

    def test_rolls_needed_matches_grid_size(self):
        self.assertEqual(slots.rolls_needed("classic"), 3)
        self.assertEqual(slots.rolls_needed("diamond_dave"), 3)
        self.assertEqual(slots.rolls_needed("vig_city"), 15)

    def test_new_seeds_are_unique(self):
        self.assertNotEqual(slots.new_server_seed(), slots.new_server_seed())
        self.assertNotEqual(slots.new_client_seed(), slots.new_client_seed())


class TestPayouts(unittest.TestCase):
    def _forced_reel3(self, machine_key, symbol, bet):
        """Find a nonce whose classic/dave spin yields three of `symbol`, else fabricate."""
        return slots._spin_reel3(slots.MACHINES[machine_key], bet,
                                 [self._roll_for(machine_key, symbol)] * 3)

    @staticmethod
    def _roll_for(machine_key, symbol):
        reel = slots.REELS[machine_key]
        idx = reel.index(symbol)
        return (idx + 0.5) / len(reel)

    def test_three_sevens_pays_120x_on_classic(self):
        r = self._forced_reel3("classic", "7️⃣", 10)
        self.assertEqual(r["win"], 10 * 120)
        self.assertIn("7️⃣", r["detail"])

    def test_cherry_pair_pays_double(self):
        machine = slots.MACHINES["classic"]
        rolls = [self._roll_for("classic", "🍒"), self._roll_for("classic", "🍒"),
                 self._roll_for("classic", "🔔")]
        r = slots._spin_reel3(machine, 10, rolls)
        self.assertEqual(r["win"], 20)

    def test_lemon_pair_pays_nothing(self):
        machine = slots.MACHINES["classic"]
        rolls = [self._roll_for("classic", "🍋"), self._roll_for("classic", "🍋"),
                 self._roll_for("classic", "🔔")]
        r = slots._spin_reel3(machine, 10, rolls)
        self.assertEqual(r["win"], 0)

    def test_clover_pair_pushes_on_diamond_dave(self):
        machine = slots.MACHINES["diamond_dave"]
        rolls = [self._roll_for("diamond_dave", "🍀"), self._roll_for("diamond_dave", "🍀"),
                 self._roll_for("diamond_dave", "➖")]
        r = slots._spin_reel3(machine, 100, rolls)
        self.assertEqual(r["win"], 100)

    def test_lone_clover_pays_nothing_on_diamond_dave(self):
        machine = slots.MACHINES["diamond_dave"]
        rolls = [self._roll_for("diamond_dave", "🍀"), self._roll_for("diamond_dave", "➖"),
                 self._roll_for("diamond_dave", "➖")]
        r = slots._spin_reel3(machine, 100, rolls)
        self.assertEqual(r["win"], 0)

    def test_blanks_pay_nothing_on_diamond_dave(self):
        machine = slots.MACHINES["diamond_dave"]
        rolls = [self._roll_for("diamond_dave", "➖")] * 3
        r = slots._spin_reel3(machine, 100, rolls)
        self.assertEqual(r["win"], 0)


class TestPaylines(unittest.TestCase):
    def test_wild_substitutes_for_symbol(self):
        target, run = slots._line_win(["🃏", "💎", "💎", "🍒", "🍒"],
                                      slots.VIG_CITY["line_pays"])
        self.assertEqual(target, "💎")
        self.assertEqual(run, 3)

    def test_all_wilds_score_as_sevens(self):
        target, run = slots._line_win([slots.WILD] * 5, slots.VIG_CITY["line_pays"])
        self.assertEqual(target, "7️⃣")
        self.assertEqual(run, 5)

    def test_run_stops_at_first_mismatch(self):
        target, run = slots._line_win(["🍒", "🍒", "🔔", "🍒", "🍒"],
                                      slots.VIG_CITY["line_pays"])
        self.assertEqual(target, "🍒")
        self.assertEqual(run, 2)

    def test_five_sevens_on_top_line_pays_top_multiplier(self):
        machine = slots.MACHINES["vig_city"]
        reel = slots.REELS["vig_city"]
        seven = (reel.index("7️⃣") + 0.5) / len(reel)
        cherry = (reel.index("🍒") + 0.5) / len(reel)
        # column-major fill: top row of each column is the first roll of that column
        rolls = []
        for _col in range(5):
            rolls += [seven, cherry, cherry]
        r = slots._spin_lines5x3(machine, 25, rolls)
        line_bet = 25 / 5
        top_mult = slots.VIG_CITY["line_pays"]["7️⃣"][2]
        self.assertEqual(r["lines"][0]["count"], 5)
        self.assertEqual(r["lines"][0]["symbol"], "7️⃣")
        self.assertEqual(r["lines"][0]["win"], round(line_bet * top_mult, 2))

    def test_no_win_when_lines_are_broken(self):
        machine = slots.MACHINES["vig_city"]
        reel = slots.REELS["vig_city"]

        def roll(sym):
            return (reel.index(sym) + 0.5) / len(reel)

        # every column a different symbol -> no run of 3 anywhere
        cols = ["🍒", "🍋", "🍇", "🔔", "⭐"]
        rolls = []
        for sym in cols:
            rolls += [roll(sym)] * 3
        r = slots._spin_lines5x3(machine, 25, rolls)
        self.assertEqual(r["win"], 0)
        self.assertEqual(r["lines"], [])


class TestHouseEdge(unittest.TestCase):
    """Guards against a paytable edit that flips the edge or over-tightens it."""

    def test_reel3_machines_have_exact_rtp_in_band(self):
        """3-reel machines are small enough to enumerate exhaustively — no sampling noise."""
        from itertools import product
        for key in ("classic", "diamond_dave"):
            machine = slots.MACHINES[key]
            reel = slots.REELS[key]
            n = len(reel)
            total = 0.0
            for combo in product(range(n), repeat=3):
                rolls = [(i + 0.5) / n for i in combo]
                total += slots._spin_reel3(machine, 1.0, rolls)["win"]
            rtp = total / (n ** 3) * 100
            self.assertGreaterEqual(rtp, 88.0, f"{key} RTP {rtp:.2f}% — too tight")
            self.assertLessEqual(rtp, 96.0, f"{key} RTP {rtp:.2f}% — house edge too thin")

    def test_lines5x3_sampled_rtp_in_wide_band(self):
        """40^15 grids can't be enumerated; sample with a band wide enough for noise."""
        n = 60_000
        server = slots.new_server_seed()
        total = 0.0
        for nonce in range(n):
            rolls = list(slots.float_stream(server, CS, nonce, 15))
            total += slots._spin_lines5x3(slots.MACHINES["vig_city"], 1.0, rolls)["win"]
        rtp = total / n * 100
        self.assertGreater(rtp, 80.0, f"vig_city RTP {rtp:.2f}% — too tight")
        self.assertLess(rtp, 110.0, f"vig_city RTP {rtp:.2f}% — house edge gone")


if __name__ == "__main__":
    unittest.main(verbosity=2)
