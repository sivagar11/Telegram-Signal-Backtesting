"""Download history, evaluate frozen filters and compare unchanged exit strategies."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from backtesting import engine
from backtesting.indicators import FILTERS, build_signal_features, filter_decisions
from backtesting.metrics import COMPLETED, calculate_metrics
from market_data.downloader import load_signals
from market_data.indicator_history import download_history

OUTPUT = Path("reports/experiment_1")


def compare(baseline, decisions, signals, filters=None, scope_ids=None):
    """Keep rejected and unavailable signals in the per-received-signal denominator."""
    boundary = int(len(signals) * 0.7)
    scopes = scope_ids if scope_ids is not None else {"full": set(signals.message_id),
              "development_first_70pct": set(signals.iloc[:boundary].message_id),
              "validation_last_30pct": set(signals.iloc[boundary:].message_id)}
    detail = baseline.merge(decisions, on="message_id", validate="many_to_many")
    detail["baseline_status"] = detail.status
    detail["baseline_points"] = detail.points
    rejected = detail.decision.ne("ACCEPTED")
    detail.loc[rejected, "status"] = detail.loc[rejected, "decision"]
    detail.loc[rejected, ["points", "r_multiple"]] = float("nan")
    summaries = []
    for scope, ids in scopes.items():
        for name in (FILTERS if filters is None else filters):
            rows = detail[detail.message_id.isin(ids) & detail["filter"].eq(name)]
            if rows.empty:
                continue
            metrics = calculate_metrics(rows)
            for _, metric in metrics.iterrows():
                group = rows[rows.strategy.eq(metric.strategy)]
                accepted = group.decision.eq("ACCEPTED")
                completed_baseline = group.baseline_status.isin(COMPLETED)
                summaries.append({"scope": scope, "filter": name, **metric.to_dict(),
                    "accepted_signals": int(accepted.sum()),
                    "rejected_signals": int(group.decision.eq("FILTER_REJECTED").sum()),
                    "unavailable_signals": int(group.decision.eq("INDICATOR_UNAVAILABLE").sum()),
                    "acceptance_pct": round(100 * accepted.mean(), 2),
                    "winning_signals_rejected": int((~accepted & completed_baseline & group.baseline_points.gt(0)).sum()),
                    "losing_signals_rejected": int((~accepted & completed_baseline & group.baseline_points.lt(0)).sum()),
                    "accepted_data_gaps": int((accepted & group.baseline_status.eq("DATA_GAP")).sum()),
                    "accepted_other_incomplete": int((accepted & ~completed_baseline & ~group.baseline_status.isin(["DATA_GAP", "AMBIGUOUS", "UNRESOLVED"])).sum()),
                    "points_per_received_signal": round(metric.net_points / len(group), 4),
                })
    return detail, pd.DataFrame(summaries)


def write_report(summary, signals):
    lines = ["# Experiment 1: indicator confirmation", "",
             "Frozen rules and limitations: see EXPERIMENT_1.md in the repository root.", "",
             f"Signals: {len(signals)}; {signals.timestamp_utc.min()} through {signals.timestamp_utc.max()}.",
             "Entry: hypothetical boundary; stop: fixed 10 points; delay: 5 seconds.",
             "All six existing exit strategies are evaluated; tables below show fixed_5.",
             "P/L and drawdown include completed trades only and are normalized price points.", ""]
    for scope in summary.scope.unique():
        lines += [f"## {scope}", "",
                  "| Filter | Accepted | Completed | Wins rejected | Win % | Net points | PF | Expectancy | Max DD | Points/signal |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for _, r in summary[summary.scope.eq(scope) & summary.strategy.eq("fixed_5")].iterrows():
            lines.append(f"| {r['filter']} | {r.accepted_signals} | {r.completed_trades} | {r.winning_signals_rejected} | {r.win_rate_pct} | {r.net_points} | {r.profit_factor} | {r.expectancy_points} | {r.max_drawdown_points} | {r.points_per_received_signal} |")
        lines.append("")
    lines += ["The chronological validation segment is a diagnostic split, not proof of unseen/live performance.",
              "Zero or very small accepted samples cannot establish an advantage.", "",
              "## First-pass interpretation", ""]
    full = summary[summary.scope.eq("full") & summary.strategy.eq("fixed_5")].set_index("filter")
    validation = summary[summary.scope.eq("validation_last_30pct") & summary.strategy.eq("fixed_5")].set_index("filter")
    base, candle = full.loc["baseline"], full.loc["D_trend_candle"]
    lines += [f"Trend + candle accepted {int(candle.accepted_signals)} signals and completed {int(candle.completed_trades)} trades. "
              f"Profit factor was {candle.profit_factor}, versus {base.profit_factor} for the baseline; "
              f"net points were {candle.net_points}, versus {base.net_points}. "
              f"It rejected {int(candle.winning_signals_rejected)} baseline winners and "
              f"{int(candle.losing_signals_rejected)} baseline losers (including unavailable indicators).", ""]
    empty = [name for name in FILTERS if full.loc[name].accepted_signals == 0]
    if empty:
        lines += ["No signals passed: " + ", ".join(empty) + ". These configurations have no measured trading edge; "
                  "their zero drawdown reflects no trades. Thresholds were not relaxed after observing this result.", ""]
    standalone = validation.loc["standalone_candle"]
    lines += [f"The standalone candle diagnostic completed {int(standalone.completed_trades)} trades in the last 30% "
              f"and produced {standalone.net_points} points, versus {validation.loc['baseline'].net_points} baseline points. "
              "This is a small exploratory sample across multiple tested configurations, not a selected live strategy.", "",
              "All results retain hypothetical boundary fills and completed-trade-only exclusions; "
              "check accepted_data_gaps, ambiguous, unresolved and unavailable counts in comparison.csv.", ""]
    (OUTPUT / "REPORT.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()
    # Freeze an explicit baseline, independent of shell or .env entry-delay settings.
    engine.ENTRY_MODE = "boundary"
    engine.STOP_LOSS_MODE = "fixed"
    engine.FIXED_STOP_POINTS = 10.0
    engine.ENTRY_DELAY_SECONDS = 5
    signals = load_signals()
    if len(signals) < 2:
        raise ValueError("At least two signals required for chronological comparison")
    histories = download_history(signals, force=args.force_download)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    features = build_signal_features(signals, histories)
    decisions = filter_decisions(features)
    baseline = engine.run_all_strategies(signals)
    detail, summary = compare(baseline, decisions, signals)
    features.to_csv(OUTPUT / "signal_features.csv", index=False)
    decisions.to_csv(OUTPUT / "filter_decisions.csv", index=False)
    baseline.to_csv(OUTPUT / "baseline_trades.csv", index=False)
    detail.to_csv(OUTPUT / "filtered_trades.csv", index=False)
    summary.to_csv(OUTPUT / "comparison.csv", index=False)
    write_report(summary, signals)
    files = [Path("data/signals/gold_signals_2026.csv"), Path("EXPERIMENT_1.md"),
             Path("backtesting/engine.py"), Path("backtesting/strategies.py"),
             Path("backtesting/metrics.py"), Path("backtesting/indicators.py"),
             Path(__file__), Path("market_data/indicator_history.py"),
             Path("market_data/oanda_client.py")]
    files += sorted(Path("data/prices/indicators").glob("*.csv"))
    files += sorted(engine.PRICE_DIR.glob("*.csv"))
    metadata = {"created_utc": pd.Timestamp.now(tz="UTC").isoformat(),
                "entry_mode": "boundary", "stop_loss_mode": "fixed", "stop_points": 10,
                "entry_delay_seconds": 5, "indicator_price": "bid", "filters": FILTERS,
                "validation_start_utc": signals.iloc[int(len(signals) * .7)].timestamp_utc.isoformat(),
                "input_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    (OUTPUT / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(summary[summary.scope.eq("full") & summary.strategy.eq("fixed_5")].to_string(index=False))
    print(f"\nReports saved to {OUTPUT}")


if __name__ == "__main__":
    main()
