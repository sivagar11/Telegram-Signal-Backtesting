"""Immediate/range-return entries, correct quote sides and expiry uncertainty."""
import unittest
import pandas as pd

from backtesting.range_return import RangeReturnVariant, simulate_range_return
from test_midpoint_experiment import bar


class RangeReturnTests(unittest.TestCase):
    def setUp(self):
        self.signal = pd.Series(dict(message_id=1, timestamp_utc=pd.Timestamp("2026-10-20 10:00Z"),
            direction="BUY", entry_low=4120., entry_high=4125., stop_loss=4115., tp1=4140.))
        self.variant = RangeReturnVariant(3, 3.)

    def trade(self, prices, variant=None, signal=None):
        return simulate_range_return(self.signal if signal is None else signal, variant or self.variant,
                                      pd.DataFrame(prices), pd.DataFrame())

    def test_immediate_ask_inside_range_targets_upper_boundary_and_original_stop(self):
        prices = [bar("2026-10-20 10:00:05Z", opening=4122.8, high=4124., low=4122.),
                  bar("2026-10-20 10:00:10Z", opening=4124., high=4131., low=4123.)]
        for points in [3., 4., 5.]:
            result = self.trade(prices, RangeReturnVariant(3, points))
            self.assertEqual(result["entry_price"], 4123.)
            self.assertEqual(result["target_price"], 4125.+points)
            self.assertEqual(result["stop_loss"], 4115.)
            self.assertEqual(result["points"], 2.+points)
            self.assertEqual(result["entry_source"], "IMMEDIATE_IN_RANGE")

    def test_wait_from_above_fills_upper_boundary_on_ask_touch(self):
        prices = [bar("2026-10-20 10:00:05Z"),
                  bar("2026-10-20 10:00:10Z", high=4127., low=4124.6),
                  bar("2026-10-20 10:00:15Z", opening=4126., high=4131., low=4125.)]
        result = self.trade(prices)
        self.assertEqual(result["entry_price"], 4125.)
        self.assertEqual(result["entry_source"], "WAIT_BOUNDARY_TOUCH")
        self.assertEqual(result["approach"], "ABOVE")
        self.assertEqual(result["fill_time_upper"], pd.Timestamp("2026-10-20 10:00:15Z"))
        self.assertEqual(result["points"], 3.)

    def test_wait_from_below_fills_lower_boundary_but_target_stays_upper_anchored(self):
        prices = [bar("2026-10-20 10:00:05Z", opening=4118.8, high=4119.4, low=4118.),
                  bar("2026-10-20 10:00:10Z", opening=4118.8, high=4120.2, low=4118.),
                  bar("2026-10-20 10:00:15Z", opening=4124., high=4131., low=4123.)]
        result = self.trade(prices)
        self.assertEqual(result["entry_price"], 4120.)
        self.assertEqual(result["approach"], "BELOW")
        self.assertEqual(result["target_price"], 4128.)
        self.assertEqual(result["points"], 8.)

    def test_sell_mirrors_buy_using_bid_entry_and_ask_exit(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z"),
                  bar("2026-10-20 10:00:10Z", high=4127., low=4124.6),
                  bar("2026-10-20 10:00:15Z", opening=4126., high=4131., low=4125.)])
        mirrored = prices.copy()
        for out, source in [("o", "o"), ("h", "l"), ("l", "h"), ("c", "c")]:
            mirrored[f"bid_{out}"] = 8250-prices[f"ask_{source}"]
            mirrored[f"ask_{out}"] = 8250-prices[f"bid_{source}"]
        signal = self.signal.copy()
        signal["direction"], signal["entry_low"], signal["entry_high"], signal["stop_loss"] = "SELL", 4125., 4130., 4135.
        result = self.trade(mirrored, signal=signal)
        self.assertEqual(result["entry_price"], 4125.)
        self.assertEqual(result["target_price"], 4122.)
        self.assertEqual(result["points"], 3.)

    def test_bid_touch_does_not_count_as_ask_boundary_touch(self):
        prices = [bar(t, low=4124.9) for t in pd.date_range("2026-10-20 10:00:05Z", "2026-10-20 10:03Z", freq="5s")]
        self.assertEqual(self.trade(prices)["status"], "NO_FILL")

    def test_later_open_inside_uses_actual_quote_not_unobserved_boundary(self):
        prices = [bar("2026-10-20 10:00:05Z"),
                  bar("2026-10-20 10:00:10Z", opening=4123.8, high=4131., low=4123.)]
        result = self.trade(prices)
        self.assertEqual(result["entry_source"], "WAIT_OPEN_IN_RANGE")
        self.assertEqual(result["entry_price"], 4124.)
        self.assertEqual(result["points"], 4.)

    def test_jump_across_whole_range_is_not_a_fabricated_boundary_fill(self):
        prices = [bar("2026-10-20 10:00:05Z"),
                  bar("2026-10-20 10:00:10Z", opening=4118.8, high=4119.4, low=4118.)]
        result = self.trade(prices)
        self.assertEqual(result["status"], "AMBIGUOUS_ENTRY_GAP")
        self.assertFalse(result["entered"])

    def test_fill_bar_exit_is_ambiguous(self):
        result = self.trade([bar("2026-10-20 10:00:05Z", high=4131., low=4124.6)])
        self.assertEqual(result["status"], "AMBIGUOUS_ENTRY_EXIT")
        self.assertTrue(result["entered"])
        self.assertIsNone(result["points"])

    def test_pending_data_gap_prevents_invented_later_fill(self):
        result = self.trade([bar("2026-10-20 10:00:05Z"),
                  bar("2026-10-20 10:00:15Z", opening=4123.8, high=4131., low=4123.)])
        self.assertEqual(result["status"], "ENTRY_DATA_GAP")
        self.assertFalse(result["entered"])

    def test_three_and_five_minute_expiries_and_exact_deadline(self):
        prices = [bar(t) for t in pd.date_range("2026-10-20 10:00:05Z", "2026-10-20 10:04Z", freq="5s")]
        prices[-1] = bar("2026-10-20 10:04Z", opening=4123.8, high=4131., low=4123.)
        self.assertEqual(self.trade(prices)["status"], "NO_FILL")
        self.assertEqual(self.trade(prices, RangeReturnVariant(5, 3.))["points"], 4.)
        prices = prices[:36]
        prices[-1] = bar("2026-10-20 10:03Z", opening=4123.8, high=4131., low=4123.)
        self.assertEqual(self.trade(prices)["entry_time"], pd.Timestamp("2026-10-20 10:03Z"))

    def test_expiry_straddling_touch_is_unknown(self):
        signal = self.signal.copy()
        signal["timestamp_utc"] += pd.Timedelta(seconds=2)
        prices = [bar(t) for t in pd.date_range("2026-10-20 10:00:10Z", "2026-10-20 10:03Z", freq="5s")]
        prices[-1] = bar("2026-10-20 10:03Z", low=4124.6)
        self.assertEqual(self.trade(prices, signal=signal)["status"], "AMBIGUOUS_ENTRY_DEADLINE")


if __name__ == "__main__":
    unittest.main()
