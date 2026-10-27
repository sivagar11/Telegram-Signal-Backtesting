"""Experiment 3.1: stop comparison with identical market entries and +3 TP."""
from dataclasses import asdict
import json
from pathlib import Path

import pandas as pd

from backtesting.midpoint import MidpointVariant, simulate_midpoint
from market_data.downloader import load_signals
from market_data.indicator_history import read_verified_page
from run_market_targets import summarize
from run_midpoint_experiment import hashes

OUTPUT = Path("reports/experiment_3_1")
VARIANTS = [
    MidpointVariant("A_original_sl_tp3", "market", target_from_entry=True, target_points=3.),
    MidpointVariant("B_fixed5_sl_tp3", "market", target_from_entry=True, target_points=3., fixed_stop_points=5.),
    MidpointVariant("C_fixed6_sl_tp3", "market", target_from_entry=True, target_points=3., fixed_stop_points=6.),
]
STOP_LABELS = dict(zip([v.name for v in VARIANTS], ["Original Telegram", "Fixed 5 points", "Fixed 6 points"]))


def preserve():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    prior = ["experiment_1", "experiment_1_1", "experiment_1_2", "experiment_2", "experiment_2_1", "experiment_3"]
    snapshot = hashes(sorted(p for name in prior for p in (Path("reports")/name).rglob("*") if p.is_file()))
    path = OUTPUT / "prior_reports_sha256.json"
    if path.exists() and json.loads(path.read_text()) != snapshot:
        raise ValueError("Earlier report files changed since preservation")
    path.write_text(json.dumps(snapshot, indent=2) + "\n")


def verify_original(trades):
    previous = pd.read_csv("reports/experiment_3/trades.csv")
    previous = previous[previous.strategy.eq("market_tp_3")]
    current = trades[trades.strategy.eq("A_original_sl_tp3")]
    columns = ["message_id", "entry_time", "entry_price", "stop_loss", "target_price", "status", "points", "r_multiple"]
    a = current[columns].sort_values("message_id").reset_index(drop=True)
    b = previous[columns].sort_values("message_id").reset_index(drop=True)
    for frame in [a, b]:
        frame["entry_time"] = pd.to_datetime(frame.entry_time, utc=True).astype("datetime64[ns, UTC]")
    pd.testing.assert_frame_equal(a, b, check_dtype=False)


def write_report(summary):
    def fmt(value):
        return "—" if pd.isna(value) else f"{value:.3f}".rstrip("0").rstrip(".")
    lines = ["# Experiment 3.1: original, five-point and six-point stops", "",
        "Identical immediate executable BID/ASK entries and +3 points TP from actual fill. Only the stop rule changes.",
        "A: original Telegram SL; B: fixed five-point SL; C: fixed six-point SL, measured from actual fill.",
        "The original-stop variant reproduces Experiment 3's market_tp_3 per-trade entries, stops, targets, statuses and P/L.",
        "Rules: EXPERIMENT_3_1.md. Five-second processing minimum, up to 15 seconds additional quote wait; five-hour monitoring.",
        "No filters, partial exits, breakeven or trailing. Results are normalized position-size price points; incomplete outcomes are excluded from profit totals.", ""]
    for scope in ["full", "validation_last_30pct"]:
        rows = summary[summary.scope.eq(scope) & summary.direction_group.eq("ALL")].sort_values("strategy")
        lines += [f"## {scope}", "",
            "| Stop | Received | Entered | Completed | Wins | Losses | Win % | Points | PF | Expectancy | Mean R | Max DD | Points/signal |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for _, r in rows.iterrows():
            lines.append(f"| {r.stop_rule} | {r.total_signals} | {r.entered_trades} | {r.completed_trades} | {r.wins} | {r.losses} | {fmt(r.win_rate_pct)} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.expectancy_points)} | {fmt(r.average_r)} | {fmt(r.max_drawdown_points)} | {fmt(r.points_per_received_signal)} |")
        lines += ["", "| Stop | Mean initial risk | Nominal reward/risk | Invalid | Entry data unknown | Ambiguous | Exit gaps | No exit data | Unresolved | Common completed | Common-set points |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for _, r in rows.iterrows():
            lines.append(f"| {r.stop_rule} | {fmt(r.mean_initial_risk)} | {fmt(r.mean_nominal_reward_risk)} | {r.invalid_entry} | {r.entry_data_unavailable} | {r.ambiguous} | {r.data_gaps} | {r.no_exit_data} | {r.unresolved} | {r.common_completed_trades} | {fmt(r.common_completed_points)} |")
        lines.append("")
    lines += ["## Directional validation", "",
              "| Direction | Stop | Completed | Win % | Points | PF | Max DD |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for _, r in summary[summary.scope.eq("validation_last_30pct") & summary.direction_group.ne("ALL")].iterrows():
        lines.append(f"| {r.direction_group} | {r.stop_rule} | {r.completed_trades} | {fmt(r.win_rate_pct)} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.max_drawdown_points)} |")
    lines += ["", "## Interpretation limits", "",
        "The common-completed table compares the same signals under all stop rules; it still excludes any signal unresolved under one rule. A tighter stop can terminate before a later data gap, changing completed-trade totals.",
        "Fixed stops are nominal distances: gap exits can exceed five or six points. BID/ASK quotes inform spread, but slippage, commission and financing beyond observed quotes are not modeled.",
        "Point profits assume the same normalized position size, not equal risk sizing. Mean R uses each actual entry-to-stop distance. Drawdown is completed trades ordered by signal time, not portfolio mark-to-market.",
        "Validation dates were examined in earlier experiments; this is a chronological diagnostic, not untouched out-of-sample proof. All three stop rules were preset and no thresholds optimized.", ""]
    (OUTPUT / "REPORT.md").write_text("\n".join(lines))


def main():
    preserve()
    signals = load_signals()
    manifest = pd.read_csv("reports/experiment_2/execution_manifest.csv")
    requests = manifest.set_index(["message_id", "granularity"])
    trades = []
    for count, (_, signal) in enumerate(signals.iterrows(), 1):
        frames = {}
        for tf in ["S5", "M1"]:
            row = requests.loc[(int(signal.message_id), tf)]
            request = {key: row[key] for key in ["instrument", "environment", "price", "start", "end"]}
            request["granularity"] = tf
            frames[tf], _ = read_verified_page(Path(row["file"]), request)
        for variant in VARIANTS:
            result = simulate_midpoint(signal, variant, frames["S5"], frames["M1"])
            result["target_points"] = variant.target_points
            trades.append(result)
        if count % 50 == 0 or count == len(signals):
            print(f"Stop comparison: {count}/{len(signals)}", flush=True)
    trades = pd.DataFrame(trades)
    verify_original(trades)
    for field in ["entry_time", "entry_price", "target_price"]:
        if not trades.groupby("message_id")[field].nunique().eq(1).all():
            raise AssertionError(f"Entries/targets differ across stop variants: {field}")
    summary = summarize(trades, signals)
    summary["stop_rule"] = summary.strategy.map(STOP_LABELS)
    trades.to_csv(OUTPUT / "trades.csv", index=False)
    summary.to_csv(OUTPUT / "comparison.csv", index=False)
    trades.groupby(["strategy", "status"]).size().rename("count").reset_index().to_csv(OUTPUT / "status_counts.csv", index=False)
    write_report(summary)
    inputs = [Path(__file__), Path("EXPERIMENT_3_1.md"), Path("backtesting/midpoint.py"), Path("backtesting/engine.py"),
              Path("backtesting/metrics.py"), Path("run_market_targets.py"), Path("market_data/indicator_history.py"),
              Path("market_data/history_validation.py"), Path("data/signals/gold_signals_2026.csv"),
              Path("reports/experiment_2/execution_manifest.csv"), Path("reports/experiment_3/trades.csv")]
    inputs += [Path(p) for p in manifest.file]
    inputs += [Path(p).with_suffix(".json") for p in manifest.file]
    metadata = dict(created_utc=pd.Timestamp.now(tz="UTC").isoformat(), variants=[asdict(v) for v in VARIANTS],
                    target_points=3, original_baseline_reproduced=True, identical_entries_and_targets=True,
                    processing_delay_seconds=5, horizon_hours=5,
                    validation_start_utc=signals.iloc[int(len(signals)*.7)].timestamp_utc.isoformat(), input_sha256=hashes(inputs))
    (OUTPUT / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    preserve()
    print(summary[summary.scope.eq("full") & summary.direction_group.eq("ALL")][
        ["stop_rule", "completed_trades", "win_rate_pct", "net_points", "profit_factor", "max_drawdown_points"]].to_string(index=False))
    print(f"Saved {OUTPUT}; original baseline reproduced and earlier reports preserved.")


if __name__ == "__main__":
    main()
