"""Offline checks for indicator causality, formulas, and comparison accounting."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtesting.indicators import (FILTERS, SECONDS, atr, build_signal_features,
                                    filter_decisions, rsi, wilder_average)
from market_data.indicator_history import download_history
from market_data.oanda_client import OandaClient, PRICE_COLUMNS
from run_indicator_experiment import compare


def history(timeframe, count):
    times = pd.date_range("2026-01-01", periods=count, freq=pd.Timedelta(seconds=SECONDS[timeframe]), tz="UTC")
    close = 4000 + np.arange(count) * .01
    return pd.DataFrame({"time": times, "bid_o": close - .2, "bid_h": close + 1,
        "bid_l": close - 1, "bid_c": close, "ask_o": close, "ask_h": close + 1.2,
        "ask_l": close - .8, "ask_c": close + .2})


class IndicatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.histories = {tf: history(tf, 1100 * 3600 // seconds) for tf, seconds in SECONDS.items()}
        cls.time = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=1050, minutes=2)
        cls.signals = pd.DataFrame([dict(message_id=1, timestamp_utc=cls.time, direction="BUY")])

    def test_wilder_seed_and_recurrence(self):
        values = wilder_average(pd.Series(range(1, 16)))
        self.assertTrue(values.iloc[:13].isna().all())
        self.assertEqual(values.iloc[13], 7.5)
        self.assertAlmostEqual(values.iloc[14], (7.5 * 13 + 15) / 14)

    def test_rsi_edge_cases(self):
        self.assertEqual(rsi(pd.Series(range(30))).iloc[-1], 100)
        self.assertEqual(rsi(pd.Series(range(30, 0, -1))).iloc[-1], 0)
        self.assertEqual(rsi(pd.Series([10.] * 30)).iloc[-1], 50)

    def test_atr_includes_gaps(self):
        frame = pd.DataFrame({"bid_h": [11., 21.], "bid_l": [9., 19.], "bid_c": [10., 20.]})
        self.assertEqual(atr(frame, period=2).iloc[-1], 6.5)

    def test_future_and_open_candles_cannot_change_decisions(self):
        before = build_signal_features(self.signals, self.histories)
        changed = {}
        for tf, frame in self.histories.items():
            frame = frame.copy()
            # Includes the candle currently open at signal arrival.
            mask = frame.time + pd.Timedelta(seconds=SECONDS[tf]) > self.time
            frame.loc[mask, PRICE_COLUMNS[1:]] *= 2
            changed[tf] = frame
            self.assertLessEqual(before.iloc[0][f"{tf}_close_time"], self.time)
        after = build_signal_features(self.signals, changed)
        pd.testing.assert_frame_equal(before, after)
        pd.testing.assert_frame_equal(filter_decisions(before), filter_decisions(after))

    def test_mixed_timestamp_resolutions_join_and_ema_warmup_fails_closed(self):
        signals = self.signals.copy()
        signals["timestamp_utc"] = signals.timestamp_utc.astype("datetime64[us, UTC]")
        histories = {tf: frame.copy() for tf, frame in self.histories.items()}
        for frame in histories.values():
            frame["time"] = frame.time.astype("datetime64[ns, UTC]")
        features = build_signal_features(signals, histories)
        self.assertTrue(features.iloc[0].trend_ready)
        histories["H1"] = histories["H1"].iloc[-100:].copy()
        decisions = filter_decisions(build_signal_features(signals, histories)).set_index("filter")
        self.assertEqual(decisions.loc["A_trend", "decision"], "INDICATOR_UNAVAILABLE")

    def test_exact_close_is_available_and_levels_exclude_latest_bar(self):
        signals = self.signals.copy()
        signals["timestamp_utc"] = self.time.floor("h")
        features = build_signal_features(signals, self.histories).iloc[0]
        self.assertEqual(features.H1_close_time, signals.iloc[0].timestamp_utc)
        frame = self.histories["M15"]
        eligible = frame[frame.time + pd.Timedelta(minutes=15) <= signals.iloc[0].timestamp_utc]
        self.assertAlmostEqual(features.support20, eligible.iloc[-21:-1].bid_l.min())
        self.assertAlmostEqual(features.resistance20, eligible.iloc[-21:-1].bid_h.max())

    def test_stale_history_fails_closed_but_baseline_is_unfiltered(self):
        signals = self.signals.copy()
        signals["timestamp_utc"] = self.histories["H1"].time.max() + pd.Timedelta(days=3)
        decisions = filter_decisions(build_signal_features(signals, self.histories)).set_index("filter")
        self.assertEqual(decisions.loc["baseline", "decision"], "ACCEPTED")
        self.assertTrue(decisions.drop("baseline").decision.eq("INDICATOR_UNAVAILABLE").all())

    def test_missing_rule_never_passes_and_conjunctions_are_distinct(self):
        features = pd.DataFrame([{**{"message_id": 1},
            **{r: True for r in ["trend", "sr", "rsi", "candle", "atr"]},
            **{f"{r}_ready": True for r in ["trend", "sr", "rsi", "candle", "atr"]}}])
        features.loc[0, "sr_ready"] = False
        features.loc[0, "rsi"] = False
        decisions = filter_decisions(features).set_index("filter")
        self.assertEqual(decisions.loc["B_trend_sr", "decision"], "INDICATOR_UNAVAILABLE")
        self.assertEqual(decisions.loc["C_trend_rsi", "decision"], "FILTER_REJECTED")
        self.assertEqual(decisions.loc["D_trend_candle", "decision"], "ACCEPTED")

    def test_rejections_keep_denominator_and_count_missed_winners(self):
        signals = pd.DataFrame({"message_id": [1, 2, 3, 4]})
        baseline = pd.DataFrame({"message_id": [1, 2, 3, 4], "strategy": ["fixed_5"] * 4,
            "timestamp_utc": pd.date_range("2026-01-01", periods=4, tz="UTC"),
            "status": ["FIXED_TARGET", "STOP", "DATA_GAP", "AMBIGUOUS"],
            "points": [5., -10., np.nan, np.nan], "r_multiple": [.5, -1., np.nan, np.nan]})
        decisions = pd.DataFrame({"message_id": [1, 2, 3, 4], "filter": ["A_trend"] * 4,
            "decision": ["FILTER_REJECTED", "ACCEPTED", "ACCEPTED", "INDICATOR_UNAVAILABLE"]})
        _, summaries = compare(baseline, decisions, signals)
        row = summaries[summaries.scope.eq("full")].iloc[0]
        self.assertEqual(row.total_signals, 4)
        self.assertEqual(row.accepted_signals, 2)
        self.assertEqual(row.completed_trades, 1)
        self.assertEqual(row.winning_signals_rejected, 1)
        self.assertEqual(row.net_points, -10)
        self.assertEqual(row.points_per_received_signal, -2.5)
        self.assertEqual(row.accepted_data_gaps, 1)

    def test_empty_accepted_sample_has_zero_points_and_no_win_rate(self):
        signals = pd.DataFrame({"message_id": [1, 2]})
        baseline = pd.DataFrame({"message_id": [1, 2], "strategy": ["fixed_5"] * 2,
            "timestamp_utc": pd.date_range("2026-01-01", periods=2, tz="UTC"),
            "status": ["FIXED_TARGET", "STOP"], "points": [5., -10.], "r_multiple": [.5, -1.]})
        decisions = pd.DataFrame({"message_id": [1, 2], "filter": ["A_trend"] * 2,
            "decision": ["FILTER_REJECTED"] * 2})
        _, summaries = compare(baseline, decisions, signals)
        row = summaries[summaries.scope.eq("full")].iloc[0]
        self.assertEqual(row.completed_trades, 0)
        self.assertEqual(row.net_points, 0)
        self.assertTrue(pd.isna(row.win_rate_pct))

    def test_new_oanda_timeframes_respect_pagination(self):
        for tf in SECONDS:
            client = OandaClient.__new__(OandaClient)
            requests = []
            client.request = lambda params: requests.append(params) or {"candles": []}
            start = pd.Timestamp("2026-01-01", tz="UTC")
            end = start + pd.Timedelta(seconds=SECONDS[tf] * 5001)
            self.assertTrue(client.get_candles(start, end, tf).empty)
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0]["to"], requests[1]["from"])

    def test_downloader_reuses_exact_pages(self):
        class FakeClient:
            calls = 0

            def get_candles(self, start, stop, tf):
                FakeClient.calls += 1
                return pd.DataFrame({"time": [start], **{c: [4000.] for c in PRICE_COLUMNS[1:]}})

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            with patch("market_data.indicator_history.HISTORY_DIR", Path(directory)), \
                 patch("market_data.indicator_history.OandaClient", FakeClient):
                download_history(self.signals)
                calls = FakeClient.calls
                download_history(self.signals)
                self.assertEqual(FakeClient.calls, calls)
                self.assertEqual(len(list(Path(directory).glob("*.json"))), calls)


if __name__ == "__main__":
    unittest.main()
