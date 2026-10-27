"""Experiment 1.1: audited history, publication delay and support rejection."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from backtesting import engine
from backtesting.indicators import filter_decisions
from backtesting.indicator_refinement import REFINED_FILTERS, build_refined_features
from backtesting.metrics import calculate_metrics
from market_data.downloader import load_signals
from market_data.history_validation import SCHEDULE_SOURCES, HOLIDAY_CLOSURES
from market_data.indicator_history import download_validated_history
from run_indicator_experiment import compare

OUTPUT = Path("reports/experiment_1_1")
LATENCIES = (0, 2)


def file_hashes(paths):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def preserve_first_pass():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / "preserved_experiment_1_sha256.json"
    hashes = file_hashes(sorted(p for p in Path("reports/experiment_1").rglob("*") if p.is_file()))
    if not hashes:
        raise ValueError("First-pass reports must exist before refinement")
    if path.exists():
        if json.loads(path.read_text()) != hashes:
            raise ValueError("First-pass output files changed since preservation")
    else:
        path.write_text(json.dumps(hashes, indent=2) + "\n")


def write_report(summary, quality, signals, changed_decisions, feature_changes, reproduction_changes):
    lines = ["# Experiment 1.1", "", "Rules frozen in EXPERIMENT_1_1.md; Experiment 1 files are preserved by SHA-256.",
             "Primary publication latency: 2 seconds; zero-second sensitivity uses otherwise identical logic.",
             "All tables below use the fixed +5 exit and fixed 10-point stop with hypothetical boundary entry.",
             "P/L uses completed outcomes only, in normalized price points; unavailable and rejected signals remain in per-signal denominators.", "",
             "## History quality", "",
             "| Timeframe | Candles | Unexplained missing | Scheduled absent slots | Observed outside published schedule |",
             "|---|---:|---:|---:|---:|"]
    for _, row in quality.iterrows():
        lines.append(f"| {row.timeframe} | {row.candles} | {row.unexpected_missing_candles} | {row.scheduled_absent_candles} | {row.observed_outside_schedule} |")
    lines += ["", "Observed candles outside the published schedule are retained if valid. In particular, broker history can include the opening five-minute bucket at 18:00 NY while the published gold session opens at 18:05. This discrepancy is reported rather than silently changing the calendar.",
              "Scheduled closures are not data faults; all unexplained gaps are listed in history_gaps.csv and reset indicator state.", "",
              f"The audited zero-delay run changed {reproduction_changes} original-filter decisions versus Experiment 1; "
              f"the two-second delay changed {len(changed_decisions)} decisions and {len(feature_changes)} signal/timeframe feature timestamps versus the audited zero-delay run.", ""]
    recheck_file = OUTPUT / "gap_rechecks.csv"
    if recheck_file.exists():
        for _, check in pd.read_csv(recheck_file).iterrows():
            lines += [f"Direct OANDA recheck ({check.checked_utc}): {check.timeframe} {check.start_utc} to "
                      f"{check.end_utc} returned {check.returned_candles} complete candles for "
                      f"{check.original_missing_candles} originally missing slots. See gap_rechecks.csv.", ""]
    lines += [
              "## Support/resistance comparison (full period, two-second delay)", "",
              "| Filter | Accepted | Unavailable | Completed | Win % | Points | PF | Expectancy | Max DD | Points/signal |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    sr_names = ["baseline", "standalone_sr", "B_trend_sr", "E_all_confirmation", "F_confirmation_atr",
                "standalone_sr_touch", "B_touch_trend", "E_touch_confirmation", "F_touch_confirmation_atr"]
    rows = summary[summary.publication_latency_seconds.eq(2) & summary.scope.eq("full")
                   & summary.direction_group.eq("ALL") & summary.strategy.eq("fixed_5")].set_index("filter")

    def display(value):
        return "—" if pd.isna(value) else str(value)

    for name in sr_names:
        r = rows.loc[name]
        lines.append(f"| {name} | {r.accepted_signals} | {r.unavailable_signals} | {r.completed_trades} | {display(r.win_rate_pct)} | {r.net_points} | {display(r.profit_factor)} | {display(r.expectancy_points)} | {r.max_drawdown_points} | {r.points_per_received_signal} |")
    for scope in ["full", "validation_last_30pct"]:
        lines += ["", f"## Candle filters by direction: {scope}", "",
                  "Split uses the same chronological boundary for BUY, SELL and combined results.", "",
                  "| Latency | Direction | Filter | Received | Accepted | Completed | Wins | Losses | Win % | Points | PF | Max DD | Points/signal |",
                  "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        selected = summary[summary.scope.eq(scope) & summary.strategy.eq("fixed_5")
                           & summary["filter"].isin(["baseline", "standalone_candle", "D_trend_candle"])]
        for _, r in selected.iterrows():
            lines.append(f"| {r.publication_latency_seconds}s | {r.direction_group} | {r['filter']} | {r.total_signals} | {r.accepted_signals} | {r.completed_trades} | {r.wins} | {r.losses} | {display(r.win_rate_pct)} | {r.net_points} | {display(r.profit_factor)} | {r.max_drawdown_points} | {r.points_per_received_signal} |")
    lines += ["", "## Interpretation", "",
              "Support-touch thresholds are a separately registered research hypothesis; the original proximity rule remains unchanged. All configurations, including zero-trade results, are reported.",
              "Two seconds is an assumed latency, not measured broker publication latency. The zero-second comparison measures sensitivity to this assumption.",
              "Direction and validation cells can be very small. Historical validation was already examined in Experiment 1, so it is not untouched out-of-sample evidence.",
              "Gap handling, publication delay, and support design are distinct changes; use the zero-delay reproduction check and latency_decision_changes.csv to separate their effects.",
              "Upcoming midpoint ATR checks must use actual entry-to-original-stop distance, not this experiment's ten-point constant. Experiment 2 has not been run.", "",
              "Execution limitations from Experiment 1 remain: hypothetical fills, intrabar ambiguity and incomplete-trade exclusions, no account sizing, costs beyond the available quotes or portfolio equity simulation.", "",
              "Calendar references: " + "; ".join(SCHEDULE_SOURCES), ""]
    # Concrete observations, without selecting a winner or changing thresholds.
    primary = summary[summary.publication_latency_seconds.eq(2) & summary.strategy.eq("fixed_5")]
    def result(scope, direction, name):
        return primary[primary.scope.eq(scope) & primary.direction_group.eq(direction)
                       & primary["filter"].eq(name)].iloc[0]
    touch = result("full", "ALL", "B_touch_trend")
    lines += ["## Observations", "",
              f"Trend + touch/rejection accepted {touch.accepted_signals} signals and completed {touch.completed_trades}, "
              f"producing {touch.net_points} points. Its sample is too small to establish an advantage.", ""]
    for direction in ["BUY", "SELL"]:
        base = result("validation_last_30pct", direction, "baseline")
        candle = result("validation_last_30pct", direction, "standalone_candle")
        trend = result("validation_last_30pct", direction, "D_trend_candle")
        lines += [f"Validation {direction}: baseline {base.net_points} points / {base.completed_trades} completed; "
                  f"candle-only {candle.net_points} / {candle.completed_trades}; "
                  f"trend + candle {trend.net_points} / {trend.completed_trades}.", ""]
    (OUTPUT / "REPORT.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()
    preserve_first_pass()
    engine.ENTRY_MODE, engine.STOP_LOSS_MODE = "boundary", "fixed"
    engine.FIXED_STOP_POINTS, engine.ENTRY_DELAY_SECONDS = 10., 5
    signals = load_signals()
    histories, gaps, quality = download_validated_history(signals, OUTPUT, force=args.force_download)
    baseline = engine.run_all_strategies(signals)
    old = pd.read_csv("reports/experiment_1/baseline_trades.csv")
    pd.testing.assert_frame_equal(calculate_metrics(baseline), calculate_metrics(old), check_dtype=False)
    baseline.to_csv(OUTPUT / "baseline_trades.csv", index=False)
    boundary = int(len(signals) * .7)
    scopes = {"full": set(signals.message_id),
              "development_first_70pct": set(signals.iloc[:boundary].message_id),
              "validation_last_30pct": set(signals.iloc[boundary:].message_id)}
    summaries, decisions_by_latency, features_by_latency = [], {}, {}
    for latency in LATENCIES:
        features = build_refined_features(signals, histories, gaps, latency_seconds=latency)
        features_by_latency[latency] = features
        decisions = filter_decisions(features, REFINED_FILTERS)
        decisions_by_latency[latency] = decisions
        features.to_csv(OUTPUT / f"signal_features_latency_{latency}.csv", index=False)
        decisions.to_csv(OUTPUT / f"filter_decisions_latency_{latency}.csv", index=False)
        for direction in ["ALL", "BUY", "SELL"]:
            subset = signals if direction == "ALL" else signals[signals.direction.eq(direction)]
            detail, summary = compare(baseline[baseline.message_id.isin(subset.message_id)],
                                      decisions[decisions.message_id.isin(subset.message_id)], subset,
                                      filters=REFINED_FILTERS, scope_ids=scopes)
            summary["direction_group"] = direction
            summary["publication_latency_seconds"] = latency
            summaries.append(summary)
            if direction == "ALL":
                detail.to_csv(OUTPUT / f"filtered_trades_latency_{latency}.csv", index=False)
    summary = pd.concat(summaries, ignore_index=True)
    summary.to_csv(OUTPUT / "comparison.csv", index=False)
    summary[summary["filter"].isin(["baseline", "standalone_candle", "D_trend_candle"])].to_csv(OUTPUT / "direction_comparison.csv", index=False)
    changes = decisions_by_latency[0].merge(decisions_by_latency[2], on=["message_id", "filter"],
                                            suffixes=("_0s", "_2s"), validate="one_to_one")
    changes = changes[changes.decision_0s.ne(changes.decision_2s)]
    changes.to_csv(OUTPUT / "latency_decision_changes.csv", index=False)
    feature_changes = []
    for tf in ["H1", "M15", "M5"]:
        first = features_by_latency[0].set_index("message_id")[f"{tf}_close_time"]
        second = features_by_latency[2].set_index("message_id")[f"{tf}_close_time"]
        for message_id in first.index[first.ne(second) & ~(first.isna() & second.isna())]:
            feature_changes.append(dict(message_id=int(message_id), timeframe=tf,
                                        close_time_0s=first.loc[message_id], close_time_2s=second.loc[message_id]))
    feature_changes = pd.DataFrame(feature_changes, columns=["message_id", "timeframe", "close_time_0s", "close_time_2s"])
    feature_changes.to_csv(OUTPUT / "latency_feature_changes.csv", index=False)
    original = pd.read_csv("reports/experiment_1/filter_decisions.csv")
    reproduced = original.merge(decisions_by_latency[0], on=["message_id", "filter"], suffixes=("_original", "_audited"), validate="one_to_one")
    reproduced[reproduced.decision_original.ne(reproduced.decision_audited)].to_csv(OUTPUT / "zero_latency_decision_changes.csv", index=False)
    reproduction_changes = int(reproduced.decision_original.ne(reproduced.decision_audited).sum())
    write_report(summary, quality, signals, changes, feature_changes, reproduction_changes)
    metadata = {"created_utc": pd.Timestamp.now(tz="UTC").isoformat(), "filters": REFINED_FILTERS,
                "publication_latencies_seconds": LATENCIES, "primary_latency_seconds": 2,
                "validation_start_utc": signals.iloc[boundary].timestamp_utc.isoformat(),
                "calendar_sources": SCHEDULE_SOURCES, "holiday_closures_ny": HOLIDAY_CLOSURES,
                "unexplained_missing_slots": int(quality.unexpected_missing_candles.sum()),
                "zero_latency_changed_decisions": reproduction_changes,
                "latency_changed_decisions": len(changes), "latency_changed_feature_timestamps": len(feature_changes),
                "gap_recheck_sha256": file_hashes(sorted(OUTPUT.glob("gap_recheck*.csv"))),
                "input_sha256": file_hashes([Path("EXPERIMENT_1_1.md"), Path(__file__),
                    Path("backtesting/indicators.py"), Path("backtesting/indicator_refinement.py"),
                    Path("backtesting/engine.py"), Path("backtesting/metrics.py"), Path("backtesting/strategies.py"),
                    Path("market_data/indicator_history.py"), Path("market_data/history_validation.py"),
                    Path("market_data/oanda_client.py"), Path("data/signals/gold_signals_2026.csv")]
                    + sorted(Path("data/prices/indicators/validated_v2").glob("*.csv"))
                    + sorted(engine.PRICE_DIR.glob("*.csv")))}
    (OUTPUT / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    preserve_first_pass()
    print(summary[summary.publication_latency_seconds.eq(2) & summary.scope.eq("validation_last_30pct")
                  & summary.strategy.eq("fixed_5") & summary["filter"].isin(["baseline", "standalone_candle", "D_trend_candle"])][
                      ["direction_group", "filter", "accepted_signals", "completed_trades", "net_points", "profit_factor", "max_drawdown_points"]].to_string(index=False))
    print(f"\nReports saved to {OUTPUT}; first-pass report hashes unchanged.")


if __name__ == "__main__":
    main()
