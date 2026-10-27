"""Single elapsed-minute quote check and original-stop/target regression tests."""
import unittest
import pandas as pd

from backtesting.midpoint import MidpointVariant, simulate_midpoint
from test_midpoint_experiment import bar


class RangeWaitTests(unittest.TestCase):
    def setUp(self):
        self.signal = pd.Series(dict(message_id=1, timestamp_utc=pd.Timestamp("2026-10-20 10:00Z"),
            direction="BUY", entry_low=4120., entry_high=4125., stop_loss=4115., tp1=4140.))
        self.variant = MidpointVariant("wait_range", "market", entry_delay_seconds=60, require_in_range=True)
        self.empty = pd.DataFrame()

    def run_trade(self, prices):
        return simulate_midpoint(self.signal, self.variant, pd.DataFrame(prices), self.empty)

    def test_sixty_elapsed_seconds_and_no_extra_five_second_delay(self):
        self.signal["timestamp_utc"] = pd.Timestamp("2026-10-20 10:00:20Z")
        result = self.run_trade([bar("2026-10-20 10:00:25Z", opening=4121.8, high=4123., low=4121.),
                                bar("2026-10-20 10:01:20Z", opening=4122.8, high=4124., low=4122.)])
        self.assertTrue(result["entered"])
        self.assertEqual(result["entry_time"], pd.Timestamp("2026-10-20 10:01:20Z"))
        self.assertEqual(result["wait_seconds"], 60.)
        self.assertEqual(result["entry_price"], 4123.)

    def test_outside_quote_skips_even_if_next_quote_returns_to_range(self):
        result = self.run_trade([bar("2026-10-20 10:01Z"),
                                bar("2026-10-20 10:01:05Z", opening=4122.8, high=4124., low=4122.)])
        self.assertEqual(result["status"], "OUTSIDE_ENTRY_RANGE")
        self.assertFalse(result["entered"])
        self.assertEqual(result["checked_quote_time"], pd.Timestamp("2026-10-20 10:01Z"))
        self.assertIsNone(result["points"])

    def test_buy_uses_ask_even_when_bid_is_in_range(self):
        result = self.run_trade([bar("2026-10-20 10:01Z", opening=4124.9, high=4126., low=4124.)])
        self.assertEqual(result["status"], "OUTSIDE_ENTRY_RANGE")
        self.assertAlmostEqual(result["checked_quote_price"], 4125.1)

    def test_sell_uses_bid_even_when_ask_is_in_range(self):
        self.signal["direction"], self.signal["stop_loss"] = "SELL", 4130.
        result = self.run_trade([bar("2026-10-20 10:01Z", opening=4119.9, high=4121., low=4119.)])
        self.assertEqual(result["status"], "OUTSIDE_ENTRY_RANGE")
        self.assertEqual(result["checked_quote_price"], 4119.9)

    def test_exact_boundaries_are_included_on_both_sides(self):
        for direction in ["BUY", "SELL"]:
            for boundary in [4120., 4125.]:
                with self.subTest(direction=direction, boundary=boundary):
                    self.signal["direction"] = direction
                    self.signal["stop_loss"] = 4115. if direction == "BUY" else 4130.
                    opening = boundary-.2 if direction == "BUY" else boundary
                    result = self.run_trade([bar("2026-10-20 10:01Z", opening=opening, high=opening+1., low=opening-1.)])
                    self.assertTrue(result["entered"])
                    self.assertTrue(result["quote_inside_range"])
                    self.assertEqual(result["entry_price"], boundary)

    def test_first_quote_rounds_forward_and_late_quote_is_unknown(self):
        self.signal["timestamp_utc"] += pd.Timedelta(seconds=2)
        valid = bar("2026-10-20 10:01:05Z", opening=4122.8, high=4124., low=4122.)
        self.assertEqual(self.run_trade([valid])["wait_seconds"], 63.)
        late = {**valid, "time": pd.Timestamp("2026-10-20 10:01:20Z")}
        result = self.run_trade([late])
        self.assertEqual(result["status"], "ENTRY_QUOTE_TOO_LATE")
        self.assertIsNone(result["quote_inside_range"])

    def test_original_stop_and_boundary_target_are_retained(self):
        result = self.run_trade([bar("2026-10-20 10:01Z", opening=4122.8, high=4124., low=4122.),
                                bar("2026-10-20 10:01:05Z", opening=4124., high=4131., low=4123.)])
        self.assertEqual(result["stop_loss"], 4115.)
        self.assertEqual(result["initial_risk"], 8.)
        self.assertEqual(result["target_price"], 4130.)
        self.assertEqual(result["points"], 7.)

    def test_intrabar_range_touch_cannot_replace_outside_open(self):
        result = self.run_trade([bar("2026-10-20 10:01Z", low=4122.)])
        self.assertEqual(result["status"], "OUTSIDE_ENTRY_RANGE")


if __name__ == "__main__":
    unittest.main()
