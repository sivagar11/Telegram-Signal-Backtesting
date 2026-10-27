"""Offline regression checks for Experiment 1.1's methodological changes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtesting.indicators import SECONDS, build_signal_features
from backtesting.indicator_refinement import (build_refined_features, refined_candle_features,
                                              support_rejection)
from market_data.history_validation import audit_history, expected_slots, validate_candles
from market_data.indicator_history import read_verified_page, write_verified_page, atomic_write
from market_data.oanda_client import OandaClient, PRICE_COLUMNS
from run_indicator_experiment import compare
from test_indicator_experiment import history


def constant_candles(times):
    return pd.DataFrame({"time": times, "bid_o": 100., "bid_h": 101., "bid_l": 99.,
        "bid_c": 100., "ask_o": 100.2, "ask_h": 101.2, "ask_l": 99.2, "ask_c": 100.2})


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.start = pd.Timestamp("2026-01-05 13:00", tz="UTC")
        self.end = self.start + pd.Timedelta(hours=5)
        self.frame = constant_candles(pd.date_range(self.start, self.end, freq="5min", inclusive="left"))

    def test_bad_timestamps_schema_and_prices_are_rejected(self):
        variants = []
        variants.append(pd.concat([self.frame, self.frame.iloc[-1:]], ignore_index=True))
        variants.append(self.frame.iloc[::-1].copy())
        variants.append(self.frame.drop(columns="ask_c"))
        for column, value in [("bid_o", 102.), ("bid_c", np.nan), ("bid_l", -1.),
                              ("ask_c", 99.9), ("bid_h", np.inf)]:
            bad = self.frame.copy()
            bad.loc[0, column] = value
            variants.append(bad)
        bad = self.frame.copy()
        bad["time"] = bad.time.dt.tz_localize(None)
        variants.append(bad)
        bad = self.frame.copy()
        bad.loc[0, "time"] += pd.Timedelta(seconds=1)
        variants.append(bad)
        bad = self.frame.assign(complete=False)
        variants.append(bad)
        for i, frame in enumerate(variants):
            with self.subTest(case=i), self.assertRaises(ValueError):
                validate_candles(frame, self.start, self.end, "M5")

    def test_internal_and_trailing_truncation_are_detected(self):
        for removed in [[30], [58, 59], [0, 1]]:
            with self.subTest(removed=removed):
                frame, gaps, stats = audit_history(self.frame.drop(index=removed).reset_index(drop=True), self.start, self.end, "M5")
                self.assertEqual(stats["unexpected_missing_candles"], len(removed))
                self.assertEqual(gaps[gaps.kind.eq("UNEXPECTED_GAP")].missing_candles.sum(), len(removed))
                if removed == [30]:
                    self.assertTrue(frame.iloc[30].reset_before)
                    self.assertEqual(frame.reset_before.sum(), 1)

    def test_empty_api_response_is_a_coverage_failure_not_timezone_error(self):
        frame, gaps, stats = audit_history(self.frame.iloc[:0], self.start, self.end, "M5")
        self.assertTrue(frame.empty)
        self.assertEqual(stats["unexpected_missing_candles"], len(self.frame))
        self.assertEqual(gaps.kind.tolist(), ["UNEXPECTED_GAP"])

    def test_weekends_dst_and_holidays_do_not_create_false_gaps(self):
        intervals = [("2026-03-06", "2026-03-10"), ("2026-04-02", "2026-04-06"),
                     ("2026-06-19", "2026-06-22"), ("2025-12-24", "2025-12-27")]
        for start, end in intervals:
            slots = expected_slots(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), "H1")
            frame = constant_candles(slots.index[slots.eq("OPEN")])
            audited, gaps, stats = audit_history(frame, pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), "H1")
            self.assertEqual(stats["unexpected_missing_candles"], 0)
            self.assertFalse(audited.reset_before.any())
            self.assertFalse(gaps.kind.eq("UNEXPECTED_GAP").any())
        slots = expected_slots(pd.Timestamp("2026-03-08", tz="UTC"), pd.Timestamp("2026-03-09", tz="UTC"), "H1")
        self.assertEqual(slots.loc[pd.Timestamp("2026-03-08 22:00", tz="UTC")], "OPEN")
        self.assertEqual(slots.loc[pd.Timestamp("2026-03-08 21:00", tz="UTC")], "WEEKEND")

    def test_coarse_candle_overlapping_partial_session_is_expected(self):
        start = pd.Timestamp("2026-01-05 23:00", tz="UTC")
        self.assertEqual(expected_slots(start, start + pd.Timedelta(hours=1), "H1").iloc[0], "OPEN")

    def test_calendar_outside_documented_coverage_fails(self):
        with self.assertRaises(ValueError):
            expected_slots(pd.Timestamp("2027-01-01", tz="UTC"), pd.Timestamp("2027-01-02", tz="UTC"), "M5")

    def test_interrupted_cache_commit_cannot_reuse_stale_metadata(self):
        request = dict(start=self.start.isoformat(), end=self.end.isoformat(), granularity="M5")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page.csv"
            write_verified_page(path, self.frame, request, {"source": "test"})
            read_verified_page(path, request)
            modified = self.frame.copy()
            modified.loc[0, PRICE_COLUMNS[1:]] += .1

            def interrupted(path, payload):
                if path.suffix == ".json":
                    raise OSError("Simulated interruption before metadata commit")
                atomic_write(path, payload)

            with patch("market_data.indicator_history.atomic_write", interrupted), self.assertRaises(OSError):
                write_verified_page(path, modified, request, {"source": "test"})
            with self.assertRaises(ValueError):
                read_verified_page(path, request)

    def test_modified_or_truncated_cache_and_mismatched_request_fail(self):
        request = dict(start=self.start.isoformat(), end=self.end.isoformat(), granularity="M5")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page.csv"
            write_verified_page(path, self.frame, request, {"source": "test"})
            with self.assertRaises(ValueError):
                read_verified_page(path, {**request, "end": self.start.isoformat()})
            path.write_bytes(path.read_bytes()[:100])
            with self.assertRaises(ValueError):
                read_verified_page(path, request)

    def test_conflicting_api_duplicates_raise_but_identical_overlap_is_allowed(self):
        candle = dict(time=self.start.isoformat(), complete=True,
                      bid=dict(o="100", h="101", l="99", c="100"),
                      ask=dict(o="100.2", h="101.2", l="99.2", c="100.2"))
        client = OandaClient.__new__(OandaClient)
        client.request = lambda params: {"candles": [candle, candle]}
        self.assertEqual(len(client.get_candles(self.start, self.end, "M5")), 1)
        changed = {**candle, "bid": {**candle["bid"], "c": "100.1"}}
        client.request = lambda params: {"candles": [candle, changed]}
        with self.assertRaises(ValueError):
            client.get_candles(self.start, self.end, "M5")

    def test_unexplained_gap_resets_atr_and_blocks_signals_during_gap(self):
        frame, gaps, _ = audit_history(self.frame.drop(index=30).reset_index(drop=True), self.start, self.end, "M5")
        features = refined_candle_features(frame, "M5")
        self.assertFalse(pd.isna(features.iloc[29].atr_m5))
        self.assertTrue(pd.isna(features.iloc[30].atr_m5))
        self.assertFalse(pd.isna(features.iloc[43].atr_m5))
        signal_time = self.start + pd.Timedelta(minutes=30 * 5 + 6)
        signals = pd.DataFrame([dict(message_id=1, timestamp_utc=signal_time, direction="BUY")])
        histories = {"H1": history("H1", 1100), "M15": history("M15", 4400), "M5": frame}
        result = build_refined_features(signals, histories, gaps).iloc[0]
        self.assertTrue(result.M5_gap_active)
        self.assertFalse(result.candle_ready)
        self.assertFalse(result.rsi_ready)


class RefinementTests(unittest.TestCase):
    def test_direction_comparison_uses_global_validation_boundary(self):
        # Eight BUYs precede two SELLs; global last-30% includes just one BUY.
        signals = pd.DataFrame({"message_id": range(1, 9)})
        baseline = pd.DataFrame({"message_id": range(1, 9), "strategy": ["fixed_5"] * 8,
            "timestamp_utc": pd.date_range("2026-01-05", periods=8, tz="UTC"),
            "status": ["FIXED_TARGET"] * 8, "points": [5.] * 8, "r_multiple": [.5] * 8})
        decisions = pd.DataFrame({"message_id": range(1, 9), "filter": ["baseline"] * 8,
                                 "decision": ["ACCEPTED"] * 8})
        _, summary = compare(baseline, decisions, signals, filters={"baseline": ()},
                             scope_ids={"full": set(range(1, 11)), "validation_last_30pct": {8, 9, 10}})
        validation = summary[summary.scope.eq("validation_last_30pct")].iloc[0]
        self.assertEqual(validation.total_signals, 1)
        self.assertEqual(validation.net_points, 5)

    def test_touch_rejection_is_directional_recent_and_uses_prior_level(self):
        frame = constant_candles(pd.date_range("2026-01-05", periods=60, freq="15min", tz="UTC"))
        frame["bid_o"] = 100.1
        frame.loc[55, ["bid_l", "bid_o", "bid_c", "bid_h"]] = [99.1, 99.7, 100.5, 100.7]
        buy = support_rejection(frame)
        self.assertFalse(buy.iloc[54].sr_touch_buy)
        self.assertTrue(buy.iloc[55:58].sr_touch_buy.all())
        self.assertFalse(buy.iloc[58].sr_touch_buy)
        mirrored = frame.copy()
        for out, source in [("o", "o"), ("h", "l"), ("l", "h"), ("c", "c")]:
            mirrored[f"bid_{out}"] = 200 - frame[f"bid_{source}"]
        pd.testing.assert_series_equal(buy.sr_touch_buy, support_rejection(mirrored).sr_touch_sell, check_names=False)
        # Breaking far below old support must not redefine support to that new low.
        frame.loc[55, "bid_l"] = 95
        self.assertFalse(support_rejection(frame).iloc[55].sr_touch_buy)

    def test_publication_delay_prevents_early_use_of_just_closed_bar(self):
        histories = {tf: history(tf, 1100 * 3600 // seconds) for tf, seconds in SECONDS.items()}
        timestamp = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=1050)
        for seconds, expected_previous in [(0, True), (1, True), (2, False)]:
            signals = pd.DataFrame([dict(message_id=1, timestamp_utc=timestamp + pd.Timedelta(seconds=seconds), direction="BUY")])
            row = build_signal_features(signals, histories, publication_latency_seconds=2).iloc[0]
            for tf in SECONDS:
                self.assertEqual(row[f"{tf}_close_time"] < timestamp, expected_previous)
                self.assertLessEqual(row[f"{tf}_available_time"], signals.iloc[0].timestamp_utc)

    def test_future_and_unpublished_candles_cannot_change_refined_features(self):
        histories = {tf: history(tf, 1100 * 3600 // seconds) for tf, seconds in SECONDS.items()}
        timestamp = pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=1050, seconds=1)
        signals = pd.DataFrame([dict(message_id=1, timestamp_utc=timestamp, direction="SELL")])
        gaps = pd.DataFrame(columns=["kind", "timeframe", "start_utc", "end_utc"])
        first = build_refined_features(signals, histories, gaps)
        altered = {}
        for tf, frame in histories.items():
            frame = frame.copy()
            mask = frame.time + pd.Timedelta(seconds=SECONDS[tf] + 2) > timestamp
            frame.loc[mask, PRICE_COLUMNS[1:]] *= 2
            altered[tf] = frame
        pd.testing.assert_frame_equal(first, build_refined_features(signals, altered, gaps))


if __name__ == "__main__":
    unittest.main()
