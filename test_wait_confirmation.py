"""Current-candle timing, executable fills and unchanged exit-rule checks."""
import unittest
from unittest.mock import patch

import pandas as pd

from backtesting import engine
from backtesting.strategies import Strategy
from backtesting.wait_confirmation import (confirmation_decision, current_candle_bounds,
                                           directional_candle, simulate_confirmed)
from market_data.wait_windows import requests_for_signal


def candle(time, opening=100., close=102., high=103., low=99., spread=.2):
    return {"time": pd.Timestamp(time), "bid_o": opening, "bid_c": close, "bid_h": high, "bid_l": low,
            "ask_o": opening+spread, "ask_c": close+spread, "ask_h": high+spread, "ask_l": low+spread}


class WaitTests(unittest.TestCase):
    def setUp(self):
        self.signal = pd.Series(dict(message_id=1, timestamp_utc=pd.Timestamp("2026-10-20 10:02:20", tz="UTC"),
            direction="BUY", entry_low=99., entry_high=100., stop_loss=90., tp1=120.))
        self.features = dict(trend=True, trend_ready=True, candle=True, candle_ready=True,
                             M5_close_time=pd.Timestamp("2026-10-20 10:00", tz="UTC"),
                             M5_available_time=pd.Timestamp("2026-10-20 10:00:02", tz="UTC"))
        self.m1 = pd.DataFrame([candle("2026-10-20 10:01Z"), candle("2026-10-20 10:02Z")])
        self.m5 = pd.DataFrame([candle("2026-10-20 09:55Z"), candle("2026-10-20 10:00Z")])
        self.strategy = Strategy("fixed_5", partial_fraction=1., partial_points=5.)
        self.settings = patch.multiple(engine, ENTRY_MODE="market", STOP_LOSS_MODE="fixed", FIXED_STOP_POINTS=10., ENTRY_DELAY_SECONDS=5)
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def test_current_interval_and_exact_boundary_wait_for_new_candle(self):
        start, end = current_candle_bounds(self.signal.timestamp_utc, "M1")
        self.assertEqual(start, pd.Timestamp("2026-10-20 10:02Z"))
        self.assertEqual(end, pd.Timestamp("2026-10-20 10:03Z"))
        start, end = current_candle_bounds(self.signal.timestamp_utc, "M5")
        self.assertEqual(start, pd.Timestamp("2026-10-20 10:00Z"))
        self.assertEqual(end, pd.Timestamp("2026-10-20 10:05Z"))
        _, end = current_candle_bounds(pd.Timestamp("2026-10-20 10:05Z"), "M5")
        self.assertEqual(end, pd.Timestamp("2026-10-20 10:10Z"))

    def test_body_threshold_and_sell_symmetry(self):
        quote = candle("2026-10-20 10:02Z")
        self.assertTrue(directional_candle(quote, "BUY"))  # Exactly half range.
        self.assertFalse(directional_candle(quote, "SELL"))
        quote["bid_o"], quote["bid_c"] = quote["bid_c"], quote["bid_o"]
        self.assertTrue(directional_candle(quote, "SELL"))
        quote["bid_c"] = quote["bid_o"]
        self.assertFalse(directional_candle(quote, "BUY"))

    def test_current_candle_cannot_be_replaced_by_bullish_previous_bar(self):
        self.m1.loc[1, ["bid_o", "bid_c"]] = [102., 100.]
        row = confirmation_decision(self.signal, "current_M1_candle", self.features, self.m1, self.m5)
        self.assertEqual(row["decision"], "CANDLE_REJECTED")
        self.assertEqual(row["candle_open_time"], self.m1.iloc[1].time)
        self.assertEqual(row["entry_ready_time"], pd.Timestamp("2026-10-20 10:03:02Z"))

    def test_missing_exact_current_bar_does_not_fall_back(self):
        row = confirmation_decision(self.signal, "current_M1_candle", self.features, self.m1.iloc[:1], self.m5)
        self.assertEqual(row["decision"], "CONFIRMATION_UNAVAILABLE")

    def test_latest_and_current_are_distinct_and_trend_is_frozen(self):
        latest = confirmation_decision(self.signal, "latest_M5_candle", self.features, self.m1, self.m5)
        current = confirmation_decision(self.signal, "current_M5_candle", self.features, self.m1, self.m5)
        self.assertEqual(latest["candle_close_time"], pd.Timestamp("2026-10-20 10:00Z"))
        self.assertEqual(current["candle_close_time"], pd.Timestamp("2026-10-20 10:05Z"))
        self.features["trend"] = False
        row = confirmation_decision(self.signal, "current_M5_trend_candle", self.features, self.m1, self.m5)
        self.assertEqual(row["decision"], "TREND_REJECTED")
        self.features["trend_ready"] = False
        row = confirmation_decision(self.signal, "current_M5_trend_candle", self.features, self.m1, self.m5)
        self.assertEqual(row["decision"], "TREND_UNAVAILABLE")

    def test_minimum_processing_delay_is_not_added_twice(self):
        self.signal["timestamp_utc"] = pd.Timestamp("2026-10-20 10:02:59Z")
        row = confirmation_decision(self.signal, "current_M1_candle", self.features, self.m1, self.m5)
        self.assertEqual(row["entry_ready_time"], pd.Timestamp("2026-10-20 10:03:04Z"))

    def test_entry_uses_next_ask_after_publication_not_confirmation_high_low(self):
        decision = confirmation_decision(self.signal, "current_M1_candle", self.features, self.m1, self.m5)
        # Pre-confirmation bar crosses both stop and target but cannot exit a future trade.
        prices = pd.DataFrame([candle("2026-10-20 10:02:55Z", high=130., low=80.),
            candle("2026-10-20 10:03:00Z", opening=105., close=105., high=106., low=104.),
            candle("2026-10-20 10:03:05Z", opening=105., close=110., high=111., low=104.)])
        result = simulate_confirmed(self.signal, self.strategy, prices, self.m1.iloc[:0], decision)
        self.assertEqual(result["entry_time"], pd.Timestamp("2026-10-20 10:03:05Z"))
        self.assertEqual(result["timestamp_utc"], self.signal.timestamp_utc)
        self.assertEqual(result["entry_price"], 105.2)
        self.assertEqual(result["status"], "FIXED_TARGET")
        self.assertAlmostEqual(result["points"], 5.)
        self.assertEqual(result["wait_seconds"], 45.)

    def test_sell_entry_uses_bid(self):
        self.signal["direction"] = "SELL"
        self.signal["tp1"] = 80.
        prices = pd.DataFrame([candle("2026-10-20 10:03:05Z", opening=100., close=95., high=101., low=94.)])
        ready = pd.Timestamp("2026-10-20 10:03:02Z")
        entry, _ = engine.get_entry(self.signal, prices, ready_time=ready)
        self.assertEqual(entry["price"], 100.)
        self.assertEqual(entry["sl"], 110.)

    def test_no_late_quote_substitution_and_already_passed_tp_rejected(self):
        decision = confirmation_decision(self.signal, "current_M1_candle", self.features, self.m1, self.m5)
        late = pd.DataFrame([candle("2026-10-20 10:03:20Z")])
        result = simulate_confirmed(self.signal, self.strategy, late, self.m1.iloc[:0], decision)
        self.assertEqual(result["status"], "ENTRY_QUOTE_TOO_LATE")
        self.assertFalse(result["entered"])
        passed = pd.DataFrame([candle("2026-10-20 10:03:05Z", opening=121., close=121., high=122., low=120.)])
        result = simulate_confirmed(self.signal, self.strategy, passed, self.m1.iloc[:0], decision)
        self.assertEqual(result["status"], "INVALID_SL_TP")
        self.assertFalse(result["entered"])

    def test_rejected_confirmation_never_enters(self):
        self.m1.loc[1, "bid_c"] = 100.
        decision = confirmation_decision(self.signal, "current_M1_candle", self.features, self.m1, self.m5)
        prices = pd.DataFrame([candle("2026-10-20 10:03:05Z")])
        result = simulate_confirmed(self.signal, self.strategy, prices, self.m1.iloc[:0], decision)
        self.assertEqual(result["status"], "CANDLE_REJECTED")
        self.assertFalse(result["entered"])
        self.assertIsNone(result["points"])

    def test_partial_target_exclusion_is_not_counted_as_entered(self):
        self.signal["tp1"] = 104.
        decision = confirmation_decision(self.signal, "current_M1_candle", self.features, self.m1, self.m5)
        prices = pd.DataFrame([candle("2026-10-20 10:03:05Z")])
        result = simulate_confirmed(self.signal, Strategy("partial_50", .5, 5.), prices, self.m1.iloc[:0], decision)
        self.assertEqual(result["status"], "TP1_BEFORE_PARTIAL_TARGET")
        self.assertFalse(result["entered"])

    def test_exit_after_five_hour_deadline_is_not_used(self):
        decision = confirmation_decision(self.signal, "current_M1_candle", self.features, self.m1, self.m5)
        start = pd.Timestamp("2026-10-20 10:03:05Z")
        times = pd.date_range(start, start + pd.Timedelta(hours=5), freq="5s")
        prices = pd.DataFrame([candle(t, close=100., high=101., low=99.) for t in times])
        prices.loc[len(prices)-1, ["bid_h", "ask_h"]] = [110., 110.2]
        result = simulate_confirmed(self.signal, self.strategy, prices, self.m1.iloc[:0], decision)
        self.assertEqual(result["status"], "UNRESOLVED")
        self.assertEqual(result["points"], 0.)

    def test_download_window_covers_confirmation_and_five_hour_exit_horizon(self):
        windows = requests_for_signal(self.signal)
        self.assertEqual(windows["S5"][1], pd.Timestamp("2026-10-20 10:08Z"))
        self.assertEqual(windows["M1"][1], pd.Timestamp("2026-10-20 15:06Z"))


if __name__ == "__main__":
    unittest.main()
