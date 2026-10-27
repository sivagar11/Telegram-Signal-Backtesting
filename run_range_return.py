"""Experiment 4: immediate in-range or pending boundary entry, three targets."""
from dataclasses import asdict
import json
from pathlib import Path

import pandas as pd

from backtesting.range_return import VARIANTS, simulate_range_return
from market_data.downloader import load_signals
from market_data.indicator_history import read_verified_page
from run_market_targets import summarize as summarize_targets
from run_midpoint_experiment import hashes

OUTPUT = Path("reports/experiment_4")


def preserve():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    prior = ["experiment_1", "experiment_1_1", "experiment_1_2", "experiment_2", "experiment_2_1", "experiment_3", "experiment_3_1"]
    snapshot = hashes(sorted(p for name in prior for p in (Path("reports")/name).rglob("*") if p.is_file()))
    path = OUTPUT / "prior_reports_sha256.json"
    if path.exists() and json.loads(path.read_text()) != snapshot:
        raise ValueError("Earlier report files changed since preservation")
    path.write_text(json.dumps(snapshot, indent=2) + "\n")


def summarize(trades, signals):
    summaries = []
    split = int(len(signals)*.7)
    scopes = {"full": set(signals.message_id),
              "development_first_70pct": set(signals.iloc[:split].message_id),
              "validation_last_30pct": set(signals.iloc[split:].message_id)}
    for wait in sorted(trades.wait_minutes.unique()):
        selected = trades[trades.wait_minutes.eq(wait)]
        summary = summarize_targets(selected, signals)
        summary["wait_minutes"] = wait
        for index, metric in summary.iterrows():
            group = selected[selected.strategy.eq(metric.strategy)]
            group = group[group.message_id.isin(scopes[metric.scope])]
            if metric.direction_group != "ALL":
                group = group[group.direction.eq(metric.direction_group)]
            counts = {
                "immediate_fills": group.entered & group.entry_source.eq("IMMEDIATE_IN_RANGE"),
                "waited_boundary_fills": group.entered & group.entry_source.eq("WAIT_BOUNDARY_TOUCH"),
                "waited_open_fills": group.entered & group.entry_source.eq("WAIT_OPEN_IN_RANGE"),
                "no_fill": group.status.eq("NO_FILL"),
                "ambiguous_entry_gap": group.status.eq("AMBIGUOUS_ENTRY_GAP"),
                "ambiguous_entry_deadline": group.status.eq("AMBIGUOUS_ENTRY_DEADLINE"),
                "ambiguous_entry_exit": group.status.eq("AMBIGUOUS_ENTRY_EXIT"),
                "entry_data_unavailable": group.status.isin(["NO_ENTRY_DATA", "NO_ENTRY_QUOTE", "ENTRY_QUOTE_TOO_LATE", "ENTRY_DATA_GAP", "ENTRY_WINDOW_INCOMPLETE"]),
            }
            for key, mask in counts.items():
                summary.loc[index, key] = int(mask.sum())
            summary.loc[index, "fill_rate_pct"] = round(group.entered.mean()*100, 2)
        summaries.append(summary)
    return pd.concat(summaries, ignore_index=True)


def write_report(summary):
    def fmt(value):
        return "—" if pd.isna(value) else f"{value:.3f}".rstrip("0").rstrip(".")
    lines = ["# Experiment 4: enter inside range or wait for boundary", "",
        "Immediate BUY ASK / SELL BID if within Telegram range; otherwise wait for the first range-boundary touch.",
        "Three- and five-minute waits, each with +3/+4/+5 targets from BUY upper / SELL lower boundary. Original Telegram SL.",
        "Five-second processing minimum; five-hour monitoring after fill. Full rules: EXPERIMENT_4.md.",
        "Later opening quotes inside the range fill at the actual quote. Intrabar boundary touches assume a boundary fill; uncertain entry/exit order is excluded.",
        "P/L is normalized price points for completed outcomes; all received signals remain in per-signal denominators.", ""]
    for scope in ["full", "validation_last_30pct"]:
        rows = summary[summary.scope.eq(scope) & summary.direction_group.eq("ALL")].sort_values(["wait_minutes", "target_points"])
        lines += [f"## {scope}", "",
            "| Wait min | Target | Received | Entered | Fill % | Immediate | Waited touch | Waited open | Completed | Win % | Points | PF | Max DD | Points/signal |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for _, r in rows.iterrows():
            lines.append(f"| {r.wait_minutes} | +{fmt(r.target_points)} | {r.total_signals} | {r.entered_trades} | {fmt(r.fill_rate_pct)} | {fmt(r.immediate_fills)} | {fmt(r.waited_boundary_fills)} | {fmt(r.waited_open_fills)} | {r.completed_trades} | {fmt(r.win_rate_pct)} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.max_drawdown_points)} | {fmt(r.points_per_received_signal)} |")
        lines += ["", "| Wait min | Target | No fill | Entry gap unknown | Expiry unknown | Entry data missing | Fill-bar exit unknown | Later ambiguous | Exit gaps | Unresolved | Common completed | Common-set points |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for _, r in rows.iterrows():
            lines.append(f"| {r.wait_minutes} | +{fmt(r.target_points)} | {fmt(r.no_fill)} | {fmt(r.ambiguous_entry_gap)} | {fmt(r.ambiguous_entry_deadline)} | {fmt(r.entry_data_unavailable)} | {fmt(r.ambiguous_entry_exit)} | {r.ambiguous} | {r.data_gaps} | {r.unresolved} | {r.common_completed_trades} | {fmt(r.common_completed_points)} |")
        lines.append("")
    lines += ["## Directional validation", "", "| Side | Wait min | Target | Completed | Points | PF | Max DD |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for _, r in summary[summary.scope.eq("validation_last_30pct") & summary.direction_group.ne("ALL")].iterrows():
        lines.append(f"| {r.direction_group} | {r.wait_minutes} | +{fmt(r.target_points)} | {r.completed_trades} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.max_drawdown_points)} |")
    lines += ["", "## Limits", "",
        "Boundary targets are always anchored to upper for BUY / lower for SELL, including entries that approach from the opposite side. Profit per winning trade can therefore exceed the target offset.",
        "Cross-range quote gaps are not invented boundary fills. Unknown waiting-window coverage is not a definite miss. Common-set profits compare the same completed signals across targets within each wait period.",
        "OHLC touch fills, uncertain sequencing, incomplete-outcome exclusions, extra slippage/fees/financing, overlapping positions and already-inspected validation dates limit inference. Drawdown uses completed trades in signal order; no live edge is established.", ""]
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
        trades.extend(simulate_range_return(signal, variant, frames["S5"], frames["M1"]) for variant in VARIANTS)
        if count % 50 == 0 or count == len(signals):
            print(f"Range-return tests: {count}/{len(signals)}", flush=True)
    trades = pd.DataFrame(trades)
    summary = summarize(trades, signals)
    trades.to_csv(OUTPUT / "trades.csv", index=False)
    summary.to_csv(OUTPUT / "comparison.csv", index=False)
    trades.groupby(["strategy", "status"]).size().rename("count").reset_index().to_csv(OUTPUT / "status_counts.csv", index=False)
    write_report(summary)
    inputs = [Path(__file__), Path("EXPERIMENT_4.md"), Path("backtesting/range_return.py"), Path("backtesting/midpoint.py"),
              Path("backtesting/engine.py"), Path("backtesting/metrics.py"), Path("run_market_targets.py"),
              Path("market_data/indicator_history.py"), Path("market_data/history_validation.py"),
              Path("data/signals/gold_signals_2026.csv"), Path("reports/experiment_2/execution_manifest.csv")]
    inputs += [Path(p) for p in manifest.file]
    inputs += [Path(p).with_suffix(".json") for p in manifest.file]
    metadata = dict(created_utc=pd.Timestamp.now(tz="UTC").isoformat(), variants=[asdict(v) for v in VARIANTS],
                    stop_loss_mode="original", target_anchor="upper_BUY_lower_SELL", input_sha256=hashes(inputs))
    (OUTPUT / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    preserve()
    print(summary[summary.scope.eq("full") & summary.direction_group.eq("ALL")][
        ["wait_minutes", "target_points", "entered_trades", "completed_trades", "net_points", "profit_factor"]].to_string(index=False))
    print(f"Saved {OUTPUT}; earlier reports preserved.")


if __name__ == "__main__":
    main()
