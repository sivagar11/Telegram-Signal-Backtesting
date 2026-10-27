"""Fixed stops measured from executable fills, gap losses and side symmetry."""
import unittest
import pandas as pd

from backtesting.midpoint import MidpointVariant, simulate_midpoint
from run_stop_comparison import VARIANTS
from test_midpoint_experiment import bar


class StopComparisonTests(unittest.TestCase):
    def setUp(self):
        self.signal = pd.Series(dict(message_id=1, timestamp_utc=pd.Timestamp("2026-10-20 10:00Z"),
            direction="BUY", entry_low=4120., entry_high=4125., stop_loss=4115., tp1=4126.))
        self.prices = pd.DataFrame([
            bar("2026-10-20 10:00:05Z"),
            bar("2026-10-20 10:00:10Z", opening=4125., high=4126., low=4121.5),
            bar("2026-10-20 10:00:15Z", opening=4127., high=4131., low=4126.)])

    def run_all(self, signal=None, prices=None):
        return [simulate_midpoint(self.signal if signal is None else signal, variant,
                                  self.prices if prices is None else prices, pd.DataFrame()) for variant in VARIANTS]

    def test_same_buy_entry_and_target_but_different_stop_outcomes(self):
        results = self.run_all()
        for result, sl, risk in zip(results, [4115., 4122., 4121.], [12., 5., 6.]):
            self.assertEqual(result["entry_price"], 4127.)
            self.assertEqual(result["target_price"], 4130.)
            self.assertEqual(result["stop_loss"], sl)
            self.assertEqual(result["initial_risk"], risk)
            self.assertEqual(result["original_stop_loss"], 4115.)
        self.assertEqual([r["points"] for r in results], [3., -5., 3.])
        self.assertEqual(self.signal.stop_loss, 4115.)
        self.assertEqual([r["stop_loss_mode"] for r in results], ["original", "fixed", "fixed"])

    def test_sell_stops_above_bid_entry_and_mirrored_outcomes(self):
        prices = self.prices.copy()
        for out, source in [("o", "o"), ("h", "l"), ("l", "h"), ("c", "c")]:
            prices[f"bid_{out}"] = 8250-self.prices[f"ask_{source}"]
            prices[f"ask_{out}"] = 8250-self.prices[f"bid_{source}"]
        signal = self.signal.copy()
        signal["direction"], signal["entry_low"], signal["entry_high"], signal["stop_loss"] = "SELL", 4125., 4130., 4135.
        results = self.run_all(signal, prices)
        for result, sl in zip(results, [4135., 4128., 4129.]):
            self.assertEqual(result["entry_price"], 4123.)
            self.assertEqual(result["target_price"], 4120.)
            self.assertEqual(result["stop_loss"], sl)
            self.assertEqual(result["original_stop_loss"], 4135.)
        self.assertEqual([r["points"] for r in results], [3., -5., 3.])

    def test_six_point_stop_can_also_be_hit_without_original_stop(self):
        prices = self.prices.copy()
        prices.loc[1, ["bid_l", "ask_l"]] = [4120.5, 4120.7]
        self.assertEqual([r["points"] for r in self.run_all(prices=prices)], [3., -5., -6.])

    def test_gap_can_exceed_nominal_fixed_loss(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z"),
                               bar("2026-10-20 10:00:10Z", opening=4119., high=4120., low=4118.)])
        for result in self.run_all(prices=prices)[1:]:
            self.assertEqual(result["status"], "STOP_GAP")
            self.assertEqual(result["points"], -8.)
            self.assertGreater(abs(result["points"]), result["initial_risk"])

    def test_invalid_fixed_distances_rejected(self):
        for points in [0., -1., float("inf"), float("nan")]:
            with self.subTest(points=points), self.assertRaises(ValueError):
                simulate_midpoint(self.signal, MidpointVariant("bad_stop", "market", target_from_entry=True,
                                  target_points=3., fixed_stop_points=points), self.prices, pd.DataFrame())


if __name__ == "__main__":
    unittest.main()
