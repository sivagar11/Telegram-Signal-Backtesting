"""Experiment 3: three fixed take-profit distances from immediate market fills."""
from dataclasses import asdict
import json
from pathlib import Path

import pandas as pd

from backtesting.metrics import COMPLETED, calculate_metrics
from backtesting.midpoint import MidpointVariant, simulate_midpoint
from market_data.downloader import load_signals
from market_data.indicator_history import read_verified_page
from run_midpoint_experiment import hashes

OUTPUT = Path("reports/experiment_3")
VARIANTS = [MidpointVariant(f"market_tp_{distance}", "market", target_from_entry=True,
                            target_points=float(distance)) for distance in [3, 4, 5]]


def preserve():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    snapshot = hashes(sorted(p for name in ["experiment_1", "experiment_1_1", "experiment_1_2", "experiment_2", "experiment_2_1"]
                             for p in (Path("reports")/name).rglob("*") if p.is_file()))
    path = OUTPUT / "prior_reports_sha256.json"
    if path.exists() and json.loads(path.read_text()) != snapshot:
        raise ValueError("Earlier experiment reports changed since preservation")
    path.write_text(json.dumps(snapshot, indent=2) + "\n")


def summarize(trades, signals):
    completed = trades[trades.status.isin(COMPLETED)]
    common = set(completed.groupby("message_id").variant.nunique().loc[lambda n: n.eq(3)].index)
    split = int(len(signals) * .7)
    scopes = {"full": set(signals.message_id), "development_first_70pct": set(signals.iloc[:split].message_id),
              "validation_last_30pct": set(signals.iloc[split:].message_id)}
    summaries = []
    for scope, ids in scopes.items():
        for direction in ["ALL", "BUY", "SELL"]:
            selected = trades[trades.message_id.isin(ids)]
            if direction != "ALL":
                selected = selected[selected.direction.eq(direction)]
            for _, metric in calculate_metrics(selected).iterrows():
                group = selected[selected.strategy.eq(metric.strategy)]
                filled = group.entered
                subset = group[group.message_id.isin(common)]
                summaries.append({"scope": scope, "direction_group": direction, **metric.to_dict(),
                    "target_points": float(group.target_points.iloc[0]),
                    "entered_trades": int(filled.sum()),
                    "invalid_entry": int(group.status.isin(["INVALID_SIGNAL", "INVALID_DIRECTION", "INVALID_SL_TP"]).sum()),
                    "entry_data_unavailable": int(group.status.isin(["NO_ENTRY_DATA", "NO_ENTRY_QUOTE", "ENTRY_QUOTE_TOO_LATE"]).sum()),
                    "data_gaps": int(group.status.eq("DATA_GAP").sum()),
                    "no_exit_data": int(group.status.eq("NO_EXIT_DATA").sum()),
                    "mean_initial_risk": group.loc[filled, "initial_risk"].mean(),
                    "mean_nominal_reward_risk": group.loc[filled, "nominal_reward_risk"].mean(),
                    "points_per_received_signal": round(metric.net_points/len(group), 4),
                    "common_completed_trades": len(subset),
                    "common_completed_points": round(subset.points.sum(), 3),
                })
    return pd.DataFrame(summaries)


def write_report(summary):
    def fmt(value):
        return "—" if pd.isna(value) else f"{value:.3f}".rstrip("0").rstrip(".")
    lines = ["# Experiment 3: immediate market entry with +3, +4 and +5 targets", "",
        "All scenarios use the same first S5 opening quote at/after message +five seconds, with at most 15 seconds additional quote wait.",
        "BUY enters ASK, SELL BID. Original Telegram SL retained. Targets are measured from actual fill, not range boundaries or Telegram TP1.",
        "Whole-position exits only; no range/indicator filters, partial exits or breakeven moves. Five-hour monitoring starts at actual fill.",
        "Exact rules: EXPERIMENT_3.md. Verified cached data reused; all previous reports preserved.",
        "P/L uses completed outcomes in normalized price points. Missing/ambiguous/unresolved outcomes are excluded from profit totals.", ""]
    for scope in ["full", "validation_last_30pct"]:
        lines += [f"## {scope}", "",
            "| Target | Received | Entered | Completed | Wins | Losses | Win % | Points | PF | Expectancy | Mean R | Max DD | Points/signal |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        selected = summary[summary.scope.eq(scope) & summary.direction_group.eq("ALL")].sort_values("target_points")
        for _, r in selected.iterrows():
            lines.append(f"| +{fmt(r.target_points)} | {r.total_signals} | {r.entered_trades} | {r.completed_trades} | {r.wins} | {r.losses} | {fmt(r.win_rate_pct)} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.expectancy_points)} | {fmt(r.average_r)} | {fmt(r.max_drawdown_points)} | {fmt(r.points_per_received_signal)} |")
        lines += ["", "| Target | Invalid entry | Entry data unknown | Ambiguous | Exit gaps | No exit data | Unresolved | Mean initial risk | Mean nominal reward/risk | Common completed | Common-set points |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for _, r in selected.iterrows():
            lines.append(f"| +{fmt(r.target_points)} | {r.invalid_entry} | {r.entry_data_unavailable} | {r.ambiguous} | {r.data_gaps} | {r.no_exit_data} | {r.unresolved} | {fmt(r.mean_initial_risk)} | {fmt(r.mean_nominal_reward_risk)} | {r.common_completed_trades} | {fmt(r.common_completed_points)} |")
        lines.append("")
    lines += ["## Directional validation", "",
              "| Direction | Target | Entered | Completed | Win % | Points | PF | Max DD |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for _, r in summary[summary.scope.eq("validation_last_30pct") & summary.direction_group.ne("ALL")].iterrows():
        lines.append(f"| {r.direction_group} | +{fmt(r.target_points)} | {r.entered_trades} | {r.completed_trades} | {fmt(r.win_rate_pct)} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.max_drawdown_points)} |")
    lines += ["", "## Interpretation limits", "",
        "Compare target distances within this report: all share market fills and original stops. Earlier experiments with fixed ten-point stops or boundary-based targets are different configurations.",
        "A smaller target can exit before a later data gap, so completion counts differ. The common-completed comparison uses the same signal subset under all three targets; it still excludes outcomes that any scenario could not resolve.",
        "Historical quotes approximate execution. Tick sequencing, additional slippage, commissions, financing and overlapping portfolio positions are not modeled. Drawdown is a completed-trade signal-ordered curve, not account equity.",
        "The already-examined chronological validation period is diagnostic; results do not establish a live edge. All three targets were preset and no thresholds were optimized.", ""]
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
            print(f"Market target tests: {count}/{len(signals)}", flush=True)
    trades = pd.DataFrame(trades)
    summary = summarize(trades, signals)
    trades.to_csv(OUTPUT / "trades.csv", index=False)
    summary.to_csv(OUTPUT / "comparison.csv", index=False)
    trades.groupby(["strategy", "status"]).size().rename("count").reset_index().to_csv(OUTPUT / "status_counts.csv", index=False)
    write_report(summary)
    inputs = [Path(__file__), Path("EXPERIMENT_3.md"), Path("backtesting/midpoint.py"), Path("backtesting/engine.py"),
              Path("backtesting/metrics.py"), Path("market_data/indicator_history.py"), Path("market_data/history_validation.py"),
              Path("data/signals/gold_signals_2026.csv"), Path("reports/experiment_2/execution_manifest.csv")]
    inputs += [Path(p) for p in manifest.file]
    inputs += [Path(p).with_suffix(".json") for p in manifest.file]
    metadata = dict(created_utc=pd.Timestamp.now(tz="UTC").isoformat(), variants=[asdict(v) for v in VARIANTS],
                    stop_loss_mode="original", processing_delay_seconds=5, target_origin="actual_entry", horizon_hours=5,
                    validation_start_utc=signals.iloc[int(len(signals)*.7)].timestamp_utc.isoformat(), input_sha256=hashes(inputs))
    (OUTPUT / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    preserve()
    print(summary[summary.scope.eq("full") & summary.direction_group.eq("ALL")][
        ["strategy", "entered_trades", "completed_trades", "win_rate_pct", "net_points", "profit_factor", "max_drawdown_points"]].to_string(index=False))
    print(f"Saved {OUTPUT}; previous reports preserved.")


if __name__ == "__main__":
    main()
