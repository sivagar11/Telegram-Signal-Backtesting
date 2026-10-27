"""Experiment 2.1: wait sixty seconds, then check the original entry range once."""
from dataclasses import asdict
import json
from pathlib import Path

import pandas as pd

from backtesting.engine import load_prices
from backtesting.midpoint import MidpointVariant, VARIANTS as MIDPOINT_VARIANTS, simulate_midpoint
from market_data.downloader import load_signals
from market_data.indicator_history import read_verified_page
from run_midpoint_experiment import hashes, summarize

OUTPUT = Path("reports/experiment_2_1")
VARIANTS = [MIDPOINT_VARIANTS[1],
    MidpointVariant("E_immediate_in_range", "market", require_in_range=True),
    MidpointVariant("F_wait_60s_unfiltered", "market", entry_delay_seconds=60),
    MidpointVariant("G_wait_60s_in_range", "market", entry_delay_seconds=60, require_in_range=True),
    MIDPOINT_VARIANTS[2]]


def preserve():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    snapshot = hashes(sorted(p for name in ["experiment_1", "experiment_1_1", "experiment_1_2", "experiment_2"]
                             for p in (Path("reports")/name).rglob("*") if p.is_file()))
    path = OUTPUT / "prior_reports_sha256.json"
    if path.exists() and json.loads(path.read_text()) != snapshot:
        raise ValueError("Earlier report files changed since preservation")
    path.write_text(json.dumps(snapshot, indent=2) + "\n")


def write_report(summary):
    def fmt(value):
        return "—" if pd.isna(value) else f"{value:.3f}".rstrip("0").rstrip(".")
    lines = ["# Experiment 2.1: wait one minute and check the range", "",
        "Primary: G_wait_60s_in_range. At message +60 seconds, use the first S5 quote no more than 15 seconds late.",
        "BUY ASK / SELL BID must be inclusively within the original Telegram range. Check once, then skip if outside.",
        "Original Telegram stop; BUY upper +5 / SELL lower -5 target. Five-hour monitoring from actual fill.",
        "Existing Experiment 2 data reused; earlier reports preserved. Exact methodology: EXPERIMENT_2_1.md.",
        "P/L is normalized price points on completed outcomes. Skipped and unknown signals remain in points/signal denominators.", ""]
    for scope in ["full", "validation_last_30pct"]:
        lines += [f"## {scope}", "",
            "| Variant | Received | Entered | Outside range | Entry data unknown | Invalid | Completed | Win % | Points | PF | Expectancy | Max DD | Points/signal |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        rows = summary[summary.scope.eq(scope) & summary.direction_group.eq("ALL")].set_index("variant")
        for variant in VARIANTS:
            r = rows.loc[variant.name]
            lines.append(f"| {variant.name} | {r.total_signals} | {r.known_fills} | {r.outside_entry_range} | {r.entry_data_unavailable} | {r.invalid_entry} | {r.completed_trades} | {fmt(r.win_rate_pct)} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.expectancy_points)} | {fmt(r.max_drawdown_points)} | {fmt(r.points_per_received_signal)} |")
        lines += ["", "The midpoint control also has definite NO_FILL outcomes; see comparison.csv/status_counts.csv. Entered totals include incomplete exits, which are excluded from completed-trade profits.", ""]
    lines += ["## Primary strategy by direction", "",
              "| Scope | Direction | Received | Entered | Completed | Points | PF | Max DD |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for _, r in summary[summary.variant.eq("G_wait_60s_in_range") & summary.scope.isin(["full", "validation_last_30pct"])].iterrows():
        lines.append(f"| {r.scope} | {r.direction_group} | {r.total_signals} | {r.known_fills} | {r.completed_trades} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.max_drawdown_points)} |")
    lines += ["", "## Limits", "",
        "The 60-second wait is elapsed time from message arrival, not candle-close confirmation or a one-minute window searching for a fill.",
        "The S5 quote approximation rounds execution to the next available five-second open. Entry gaps/late quotes, ambiguous exits, exit gaps and unresolved trades are explicit in status_counts.csv.",
        "This retains earlier OHLC, omitted-cost, portfolio and already-examined-validation limitations. No live trading edge is established by this historical test.", ""]
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
        trades.extend(simulate_midpoint(signal, variant, frames["S5"], frames["M1"]) for variant in VARIANTS)
        if count % 50 == 0 or count == len(signals):
            print(f"One-minute range tests: {count}/{len(signals)}", flush=True)
    trades, summary = summarize(pd.DataFrame(trades), signals)
    old = pd.read_csv("reports/experiment_2/comparison.csv")
    for name in ["A_market_reference", "B_midpoint_3m_boundary_target"]:
        columns = ["scope", "direction_group", "total_signals", "known_fills", "completed_trades", "wins", "losses", "net_points", "max_drawdown_points"]
        first = summary[summary.variant.eq(name)][columns].reset_index(drop=True)
        second = old[old.variant.eq(name)][columns].reset_index(drop=True)
        pd.testing.assert_frame_equal(first, second, check_dtype=False)
    trades.to_csv(OUTPUT / "trades.csv", index=False)
    summary.to_csv(OUTPUT / "comparison.csv", index=False)
    trades.groupby(["variant", "status"]).size().rename("count").reset_index().to_csv(OUTPUT / "status_counts.csv", index=False)
    write_report(summary)
    inputs = [Path(__file__), Path("EXPERIMENT_2_1.md"), Path("backtesting/midpoint.py"),
              Path("backtesting/engine.py"), Path("backtesting/metrics.py"), Path("run_midpoint_experiment.py"),
              Path("market_data/indicator_history.py"), Path("market_data/history_validation.py"),
              Path("data/signals/gold_signals_2026.csv"), Path("reports/experiment_2/execution_manifest.csv")]
    inputs += [Path(p) for p in manifest.file]
    inputs += [Path(p).with_suffix(".json") for p in manifest.file]
    metadata = dict(created_utc=pd.Timestamp.now(tz="UTC").isoformat(), variants=[asdict(v) for v in VARIANTS],
                    check_once=True, range_inclusive=True, primary_delay_seconds=60, quote_wait_limit_seconds=15,
                    stop_loss_mode="original", target="favorable_boundary_plus_5", input_sha256=hashes(inputs))
    (OUTPUT / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    preserve()
    print(summary[summary.scope.eq("full") & summary.direction_group.eq("ALL")][
        ["variant", "known_fills", "outside_entry_range", "completed_trades", "net_points", "profit_factor"]].to_string(index=False))
    print(f"Saved {OUTPUT}; earlier reference results reproduced and reports preserved.")


if __name__ == "__main__":
    main()
