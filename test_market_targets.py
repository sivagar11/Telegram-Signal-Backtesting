"""Actual-fill targets, original stops, side symmetry and common-set accounting."""
import unittest
import pandas as pd

from backtesting.midpoint import MidpointVariant, simulate_midpoint
from run_market_targets import VARIANTS, summarize
from test_midpoint_experiment import bar


class MarketTargetTests(unittest.TestCase):
    def setUp(self):
        self.signal = pd.Series(dict(message_id=1, timestamp_utc=pd.Timestamp("2026-10-20 10:00Z"),
            direction="BUY", entry_low=4120., entry_high=4125., stop_loss=4115., tp1=4126.))
        self.prices = pd.DataFrame([
            bar("2026-10-20 10:00:05Z", high=4130.5),
            bar("2026-10-20 10:00:10Z", opening=4128., high=4131.5, low=4127.),
            bar("2026-10-20 10:00:15Z", opening=4130., high=4132.5, low=4129.)])
        self.empty = pd.DataFrame()

    def run_all(self, signal=None, prices=None):
        return [simulate_midpoint(self.signal if signal is None else signal, v,
                self.prices if prices is None else prices, self.empty) for v in VARIANTS]

    def test_three_targets_share_actual_entry_and_original_stop(self):
        results = self.run_all()
        for distance, result in zip([3, 4, 5], results):
            self.assertEqual(result["entry_price"], 4127.)  # ASK, outside range and beyond Telegram TP1.
            self.assertEqual(result["stop_loss"], 4115.)
            self.assertEqual(result["initial_risk"], 12.)
            self.assertEqual(result["target_price"], 4127.+distance)
            self.assertEqual(result["points"], distance)
            self.assertEqual(result["status"], "FIXED_TARGET")
            self.assertEqual(result["entry_time"], pd.Timestamp("2026-10-20 10:00:05Z"))

    def test_sell_targets_are_below_bid_fill(self):
        mirrored = self.prices.copy()
        for out, source in [("o", "o"), ("h", "l"), ("l", "h"), ("c", "c")]:
            mirrored[f"bid_{out}"] = 8250-self.prices[f"ask_{source}"]
            mirrored[f"ask_{out}"] = 8250-self.prices[f"bid_{source}"]
        signal = self.signal.copy()
        signal["direction"], signal["entry_low"], signal["entry_high"], signal["stop_loss"] = "SELL", 4125., 4130., 4135.
        for distance, result in zip([3, 4, 5], self.run_all(signal, mirrored)):
            self.assertEqual(result["entry_price"], 4123.)
            self.assertEqual(result["target_price"], 4123.-distance)
            self.assertEqual(result["initial_risk"], 12.)
            self.assertEqual(result["points"], distance)

    def test_stop_gaps_use_original_price_and_actual_open(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z"),
                               bar("2026-10-20 10:00:10Z", opening=4114., high=4115., low=4113.)])
        for result in self.run_all(prices=prices):
            self.assertEqual(result["status"], "STOP_GAP")
            self.assertEqual(result["points"], -13.)
            self.assertEqual(result["stop_loss"], 4115.)

    def test_invalid_target_distances_are_rejected(self):
        for distance in [0., -1., float("nan"), float("inf")]:
            with self.subTest(distance=distance), self.assertRaises(ValueError):
                simulate_midpoint(self.signal, MidpointVariant("invalid", "market", target_from_entry=True,
                                  target_points=distance), self.prices, self.empty)

    def test_common_completed_set_excludes_signal_unresolved_under_one_target(self):
        rows, signals = [], []
        for message_id, prices in [(1, self.prices), (2, self.prices.iloc[:2])]:
            signal = self.signal.copy()
            signal["message_id"] = message_id
            signals.append(signal.to_dict())
            for variant, result in zip(VARIANTS, self.run_all(signal, prices)):
                result["target_points"] = variant.target_points
                rows.append(result)
        summary = summarize(pd.DataFrame(rows), pd.DataFrame(signals))
        full = summary[summary.scope.eq("full") & summary.direction_group.eq("ALL")].set_index("target_points")
        self.assertEqual(full.loc[3., "completed_trades"], 2)
        self.assertEqual(full.loc[5., "completed_trades"], 1)
        for distance in [3., 4., 5.]:
            self.assertEqual(full.loc[distance, "common_completed_trades"], 1)
            self.assertEqual(full.loc[distance, "common_completed_points"], distance)
            self.assertEqual(full.loc[distance, "total_signals"], 2)


if __name__ == "__main__":
    unittest.main()
