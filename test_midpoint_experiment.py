"""Limit-side execution, expiry, uncertainty, original risk and prefix reuse."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from backtesting.midpoint import VARIANTS, simulate_midpoint
from market_data.indicator_history import write_verified_page
from market_data.wait_windows import download_wait_windows
from run_midpoint_experiment import summarize


def bar(time, opening=4126.8, high=4127.8, low=4125.8, close=None, spread=.2):
    close = opening if close is None else close
    return dict(time=pd.Timestamp(time), bid_o=opening, bid_h=high, bid_l=low, bid_c=close,
                ask_o=opening+spread, ask_h=high+spread, ask_l=low+spread, ask_c=close+spread)


class MidpointTests(unittest.TestCase):
    def setUp(self):
        self.signal = pd.Series(dict(message_id=1, timestamp_utc=pd.Timestamp("2026-10-20 10:00Z"),
            direction="BUY", entry_low=4120., entry_high=4125., stop_loss=4115., tp1=4140.))
        self.a, self.market, self.b, self.c, self.d = VARIANTS
        self.empty = pd.DataFrame(columns=list(bar("2026-10-20 10:00Z")))

    def run_trade(self, prices, variant=None, signal=None, m1=None):
        return simulate_midpoint(self.signal if signal is None else signal, variant or self.b,
                                 prices, self.empty if m1 is None else m1)

    def test_example_target_risk_and_actual_entry_target(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z"),
            bar("2026-10-20 10:00:10Z", high=4127., low=4122., close=4123.),
            bar("2026-10-20 10:00:15Z", opening=4123., high=4131., low=4122., close=4130.)])
        b, d = self.run_trade(prices), self.run_trade(prices, self.d)
        self.assertEqual(b["entry_price"], 4122.5)
        self.assertEqual(b["initial_risk"], 7.5)
        self.assertEqual(b["target_price"], 4130.)
        self.assertEqual(b["points"], 7.5)
        self.assertEqual(b["nominal_reward_risk"], 1.)
        self.assertEqual(b["fill_kind"], "INTRABAR_LIMIT")
        self.assertEqual(b["fill_time_upper"], pd.Timestamp("2026-10-20 10:00:15Z"))
        self.assertEqual(d["target_price"], 4127.5)
        self.assertEqual(d["points"], 5.)

    def test_marketable_limit_fills_at_improved_ask_and_target_d_uses_actual_fill(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z", opening=4121.8, high=4122.8, low=4120.8),
                               bar("2026-10-20 10:00:10Z", opening=4124., high=4131., low=4123.)])
        result = self.run_trade(prices)
        self.assertEqual(result["entry_price"], 4122.)
        self.assertEqual(result["initial_risk"], 7.)
        self.assertEqual(result["points"], 8.)
        self.assertEqual(result["midpoint_price_improvement"], .5)
        self.assertEqual(self.run_trade(prices, self.d)["target_price"], 4127.)

    def test_buy_touch_uses_ask_not_bid(self):
        prices = pd.DataFrame([bar(t, low=4122.4) for t in pd.date_range("2026-10-20 10:00:05Z", "2026-10-20 10:03Z", freq="5s")])
        result = self.run_trade(prices)
        self.assertEqual(result["status"], "NO_FILL")  # BID touches, ASK low = 4122.6.

    def test_sell_is_exact_bid_ask_mirror(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z"),
            bar("2026-10-20 10:00:10Z", high=4127., low=4122., close=4123.),
            bar("2026-10-20 10:00:15Z", opening=4123., high=4131., low=4122., close=4130.)])
        mirrored = prices.copy()
        for output, source in [("o", "o"), ("h", "l"), ("l", "h"), ("c", "c")]:
            mirrored[f"bid_{output}"] = 8250 - prices[f"ask_{source}"]
            mirrored[f"ask_{output}"] = 8250 - prices[f"bid_{source}"]
        signal = self.signal.copy()
        signal["direction"], signal["entry_low"], signal["entry_high"], signal["stop_loss"] = "SELL", 4125., 4130., 4135.
        result = self.run_trade(mirrored, signal=signal)
        self.assertEqual(result["entry_price"], 4127.5)
        self.assertEqual(result["target_price"], 4120.)
        self.assertEqual(result["points"], 7.5)

    def test_fill_bar_possible_exit_is_explicitly_ambiguous(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z", high=4131., low=4122.)])
        result = self.run_trade(prices)
        self.assertTrue(result["entered"])
        self.assertEqual(result["status"], "AMBIGUOUS_ENTRY_EXIT")
        self.assertIsNone(result["points"])

    def test_open_fill_can_exit_in_its_own_bar(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z", opening=4122.3, high=4131., low=4122.)])
        result = self.run_trade(prices)
        self.assertEqual(result["fill_kind"], "OPEN")
        self.assertEqual(result["status"], "FIXED_TARGET")
        self.assertEqual(result["points"], 7.5)

    def test_deadline_straddling_touch_is_not_assumed_fill_or_miss(self):
        signal = self.signal.copy()
        signal["timestamp_utc"] += pd.Timedelta(seconds=2)
        prices = pd.DataFrame([bar(t) for t in pd.date_range("2026-10-20 10:00:10Z", "2026-10-20 10:03Z", freq="5s")])
        prices.loc[len(prices)-1, ["bid_l", "ask_l"]] = [4122., 4122.2]
        result = self.run_trade(prices, signal=signal)
        self.assertEqual(result["status"], "AMBIGUOUS_ENTRY_DEADLINE")
        self.assertFalse(result["entered"])

    def test_opening_quote_at_exact_deadline_is_eligible(self):
        prices = pd.DataFrame([bar(t) for t in pd.date_range("2026-10-20 10:00:05Z", "2026-10-20 10:03Z", freq="5s")])
        prices.iloc[-1] = pd.Series(bar("2026-10-20 10:03Z", opening=4122.3, high=4131., low=4122.))
        result = self.run_trade(prices)
        self.assertTrue(result["entered"])
        self.assertEqual(result["entry_time"], pd.Timestamp("2026-10-20 10:03Z"))
        self.assertEqual(result["status"], "FIXED_TARGET")

    def test_missing_pending_data_cannot_become_no_fill_or_later_fill(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z"),
                               bar("2026-10-20 10:00:15Z", opening=4122.3, high=4131., low=4122.)])
        self.assertEqual(self.run_trade(prices)["status"], "ENTRY_DATA_GAP")
        self.assertEqual(self.run_trade(prices.iloc[:1])["status"], "ENTRY_WINDOW_INCOMPLETE")

    def test_five_minute_window_can_fill_after_three_minute_expiry(self):
        prices = pd.DataFrame([bar(t) for t in pd.date_range("2026-10-20 10:00:05Z", "2026-10-20 10:04Z", freq="5s")])
        prices.iloc[-1] = pd.Series(bar("2026-10-20 10:04Z", opening=4122.3, high=4131., low=4122.))
        self.assertEqual(self.run_trade(prices, self.b)["status"], "NO_FILL")
        result = self.run_trade(prices, self.c)
        self.assertTrue(result["entered"])
        self.assertEqual(result["wait_seconds"], 240.)

    def test_pre_processing_touch_is_ignored_and_clock_starts_at_message(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00Z", opening=4122.3, high=4131., low=4122.)]
            + [bar(t) for t in pd.date_range("2026-10-20 10:00:05Z", "2026-10-20 10:03Z", freq="5s")])
        self.assertEqual(self.run_trade(prices)["status"], "NO_FILL")
        late = pd.DataFrame([bar("2026-10-20 10:00:25Z")])
        self.assertEqual(self.run_trade(late)["status"], "ENTRY_QUOTE_TOO_LATE")

    def test_original_stop_gaps_and_unused_tp1(self):
        signal = self.signal.copy()
        signal["tp1"] = 4000.  # Not an exit in this experiment.
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z", opening=4122.3, high=4123., low=4122.),
                               bar("2026-10-20 10:00:10Z", opening=4110., high=4111., low=4109.)])
        result = self.run_trade(prices, signal=signal)
        self.assertEqual(result["stop_loss"], 4115.)
        self.assertEqual(result["status"], "STOP_GAP")
        self.assertEqual(result["points"], -12.5)

    def test_later_bar_both_levels_is_ambiguous(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z", opening=4122.3, high=4123., low=4122.),
                               bar("2026-10-20 10:00:10Z", opening=4123., high=4131., low=4114.)])
        result = self.run_trade(prices)
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertIsNone(result["points"])

    def test_hypothetical_and_executable_immediate_entries_are_distinct(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z", high=4131.)])
        boundary, market = self.run_trade(prices, self.a), self.run_trade(prices, self.market)
        self.assertEqual(boundary["entry_price"], 4125.)
        self.assertTrue(boundary["hypothetical"])
        self.assertEqual(market["entry_price"], 4127.)
        self.assertFalse(market["hypothetical"])
        self.assertEqual(boundary["target_price"], market["target_price"])

    def test_coarse_exit_beyond_five_hours_is_not_used(self):
        fine = pd.DataFrame([bar(t, opening=4122.3, high=4123., low=4122.) for t in pd.date_range(
            "2026-10-20 10:00:05Z", "2026-10-20 10:00:55Z", freq="5s")])
        coarse = pd.DataFrame([bar(t, opening=4122.3, high=4123., low=4122.) for t in pd.date_range(
            "2026-10-20 10:00Z", "2026-10-20 15:00Z", freq="min")])
        coarse.loc[len(coarse)-1, ["bid_h", "ask_h"]] = [4131., 4131.2]
        self.assertEqual(self.run_trade(fine, m1=coarse)["status"], "UNRESOLVED")

    def test_invalid_executable_entry_is_not_reported_as_filled(self):
        prices = pd.DataFrame([bar("2026-10-20 10:00:05Z", opening=4130., high=4131., low=4129.)])
        result = self.run_trade(prices, self.market)
        self.assertEqual(result["status"], "INVALID_SL_TP")
        self.assertFalse(result["entered"])
        self.assertIsNone(result["points"])

    def test_no_fill_stays_in_received_signal_denominator(self):
        signals = pd.DataFrame([self.signal.to_dict(), {**self.signal.to_dict(), "message_id": 2}])
        results = []
        for _, signal in signals.iterrows():
            prices = pd.DataFrame([bar(t, high=4131.) for t in pd.date_range("2026-10-20 10:00:05Z", "2026-10-20 10:03Z", freq="5s")])
            results.append(self.run_trade(prices, self.market, signal=signal))
            if signal.message_id == 1:
                prices.loc[0] = pd.Series(bar("2026-10-20 10:00:05Z", opening=4122.3, high=4131., low=4122.))
            results.append(self.run_trade(prices, self.b, signal=signal))
        _, summary = summarize(pd.DataFrame(results), signals)
        row = summary[summary.scope.eq("full") & summary.direction_group.eq("ALL")
                      & summary.variant.eq(self.b.name)].iloc[0]
        self.assertEqual(row.total_signals, 2)
        self.assertEqual(row.known_fills, 1)
        self.assertEqual(row.no_fill, 1)
        self.assertEqual(row.net_points, 7.5)
        self.assertEqual(row.points_per_received_signal, 3.75)


class PrefixTests(unittest.TestCase):
    def test_verified_prefix_is_extended_only_by_missing_tail_and_then_reused(self):
        timestamp = pd.Timestamp("2026-10-20 10:00Z")
        signals = pd.DataFrame([dict(message_id=1, timestamp_utc=timestamp)])
        calls = []

        def frame(start, end, tf):
            return pd.DataFrame([bar(t) for t in pd.date_range(start, end, freq="5s" if tf == "S5" else "min", inclusive="left")])

        class Client:
            def get_candles(self, start, end, tf):
                calls.append((start, end, tf))
                return frame(start, end, tf)

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"OANDA_INSTRUMENT": "XAU_USD", "OANDA_ENV": "practice"}):
            root = Path(directory)
            source = []
            for tf, minutes in [("S5", 1), ("M1", 2)]:
                end = timestamp + pd.Timedelta(minutes=minutes)
                request = dict(instrument="XAU_USD", environment="practice", price="BA", granularity=tf,
                               start=timestamp.isoformat(), end=end.isoformat())
                path = root / f"source_{tf}.csv"
                write_verified_page(path, frame(timestamp, end, tf), request, {"source": "test"})
                source.append({"message_id": 1, **request, "file": str(path)})
            builder = lambda signal: {tf: (timestamp, timestamp+pd.Timedelta(minutes=4)) for tf in ["S5", "M1"]}
            with patch("market_data.wait_windows.OandaClient", Client):
                manifest = download_wait_windows(signals, root, request_builder=builder, window_dir=root/"new", source_manifest=pd.DataFrame(source))
                self.assertEqual({(a, tf) for a, _, tf in calls}, {(timestamp+pd.Timedelta(minutes=1), "S5"), (timestamp+pd.Timedelta(minutes=2), "M1")})
                self.assertEqual(len(manifest), 2)
                count = len(calls)
                download_wait_windows(signals, root, request_builder=builder, window_dir=root/"new", source_manifest=pd.DataFrame(source))
                self.assertEqual(len(calls), count)


if __name__ == "__main__":
    unittest.main()
