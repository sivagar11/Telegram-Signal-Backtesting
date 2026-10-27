"""Experiment 1.2: enter after the message's current M1/M5 candle confirms."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from backtesting import engine
from backtesting.indicator_refinement import build_refined_features
from backtesting.metrics import COMPLETED, calculate_metrics
from backtesting.strategies import STRATEGIES, Strategy
from backtesting.wait_confirmation import VARIANTS, confirmation_decision, simulate_confirmed
from market_data.downloader import load_signals
from market_data.indicator_history import download_validated_history
from market_data.wait_windows import download_wait_windows

OUTPUT = Path("reports/experiment_1_2")


def preserve_prior_reports():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = OUTPUT / "prior_reports_sha256.json"
    files = sorted(p for folder in ["experiment_1", "experiment_1_1"]
                   for p in (Path("reports") / folder).rglob("*") if p.is_file())
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    if manifest.exists() and json.loads(manifest.read_text()) != hashes:
        raise ValueError("Prior experiment reports changed since preservation")
    manifest.write_text(json.dumps(hashes, indent=2) + "\n")


def summarize(trades, signals):
    split = int(len(signals) * .7)
    scopes = {"full": set(signals.message_id), "development_first_70pct": set(signals.iloc[:split].message_id),
              "validation_last_30pct": set(signals.iloc[split:].message_id)}
    immediate = trades[trades.variant.eq("immediate_market")][["message_id", "strategy", "status", "points", "entry_price", "entered"]].rename(
        columns={"status": "immediate_status", "points": "immediate_points", "entry_price": "immediate_entry_price", "entered": "immediate_entered"})
    trades = trades.merge(immediate, on=["message_id", "strategy"], validate="many_to_one")
    sign = trades.direction.map({"BUY": 1, "SELL": -1})
    trades["entry_price_change_adverse"] = (sign * (trades.entry_price - trades.immediate_entry_price)).where(trades.entered & trades.immediate_entered)
    summaries = []
    for scope, ids in scopes.items():
        for direction in ["ALL", "BUY", "SELL"]:
            mask = trades.message_id.isin(ids)
            if direction != "ALL":
                mask &= trades.direction.eq(direction)
            for variant, group in trades[mask].groupby("variant"):
                for _, metric in calculate_metrics(group).iterrows():
                    rows = group[group.strategy.eq(metric.strategy)]
                    accepted = rows.confirmation_decision.eq("ACCEPTED")
                    entered = rows.entered
                    paired = rows.status.isin(COMPLETED) & rows.immediate_status.isin(COMPLETED)
                    summaries.append({"scope": scope, "direction_group": direction, "variant": variant,
                        **metric.to_dict(), "accepted_signals": int(accepted.sum()), "entered_trades": int(entered.sum()),
                        "candle_rejected": int(rows.status.eq("CANDLE_REJECTED").sum()),
                        "confirmation_unavailable": int(rows.status.eq("CONFIRMATION_UNAVAILABLE").sum()),
                        "trend_rejected": int(rows.status.eq("TREND_REJECTED").sum()),
                        "trend_unavailable": int(rows.status.eq("TREND_UNAVAILABLE").sum()),
                        "accepted_no_entry": int((accepted & ~entered).sum()),
                        "data_gaps": int(rows.status.eq("DATA_GAP").sum()),
                        "other_entered_incomplete": int((entered & ~rows.status.isin(COMPLETED | {"AMBIGUOUS", "UNRESOLVED", "DATA_GAP"})).sum()),
                        "mean_wait_seconds": rows.loc[entered, "wait_seconds"].mean(),
                        "mean_adverse_entry_change": rows.entry_price_change_adverse.mean(),
                        "immediate_winners_rejected": int((~accepted & rows.immediate_status.isin(COMPLETED) & rows.immediate_points.gt(0)).sum()),
                        "paired_completed": int(paired.sum()),
                        "paired_points_difference": round((rows.loc[paired, "points"] - rows.loc[paired, "immediate_points"]).sum(), 3),
                        "points_per_received_signal": round(metric.net_points / len(rows), 4),
                    })
    return trades, pd.DataFrame(summaries)


def write_report(summary, decisions):
    lines = ["# Experiment 1.2: current-candle confirmation", "",
        "Rules: EXPERIMENT_1_2.md. Confirmation uses the M1/M5 interval containing the message, never an earlier completed substitute.",
        "BUY requires a bullish candle, SELL bearish; body >=50% of range. Publication delay: two seconds.",
        "Entry uses the next S5 ASK open for BUY or BID open for SELL, after confirmation and at least five seconds after the signal.",
        "Fixed ten-point stop; all six existing exits tested. Five-hour monitoring starts at the actual fill.",
        "Tables show fixed_5; all strategies and detailed statuses are in comparison.csv and status_counts.csv.",
        "P/L is normalized price points on completed outcomes only. Accepted signals may fail to enter or lack a complete exit.", ""]

    def fmt(value):
        return "—" if pd.isna(value) else f"{value:.3f}".rstrip("0").rstrip(".")

    for scope in ["full", "validation_last_30pct"]:
        lines += [f"## {scope}", "",
            "| Variant | Received | Accepted | Entered | Completed | Win % | Points | PF | Expectancy | Max DD | Mean wait s | Points/signal |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        selected = summary[summary.scope.eq(scope) & summary.direction_group.eq("ALL") & summary.strategy.eq("fixed_5")].set_index("variant")
        for variant in VARIANTS:
            r = selected.loc[variant]
            lines.append(f"| {variant} | {r.total_signals} | {r.accepted_signals} | {r.entered_trades} | {r.completed_trades} | {fmt(r.win_rate_pct)} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.expectancy_points)} | {fmt(r.max_drawdown_points)} | {fmt(r.mean_wait_seconds)} | {fmt(r.points_per_received_signal)} |")
        lines.append("")
    lines += ["## Directional validation", "",
              "| Direction | Variant | Accepted | Entered | Completed | Points | PF | Max DD |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    selected = summary[summary.scope.eq("validation_last_30pct") & summary.direction_group.ne("ALL") & summary.strategy.eq("fixed_5")]
    for _, r in selected.iterrows():
        lines.append(f"| {r.direction_group} | {r.variant} | {r.accepted_signals} | {r.entered_trades} | {r.completed_trades} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.max_drawdown_points)} |")
    lines += ["", "## Interpretation limits", "",
        "Compare delayed entries with immediate_market in this report. Earlier Experiment 1 boundary-fill results use a different fill assumption and are preserved as historical references.",
        "Telegram TP1 remains absolute: if a new executable entry has already passed TP1, the existing engine rejects it as INVALID_SL_TP, including fixed-target strategies. Partial strategies also retain TP1_BEFORE_PARTIAL_TARGET. Inspect these exclusions rather than reading accepted as completed.",
        "Confirmation candles contain movement before and after signal arrival; that information is used only after the candle closes and execution is delayed accordingly. Trend is frozen at signal arrival.",
        "The chronological validation period has already been examined. No threshold tuning or claim of untouched out-of-sample performance is made. Tiny samples, excluded ambiguous/data-gap/unresolved outcomes, OHLC sequencing and omitted slippage/financing/commissions limit conclusions.",
        "Drawdown is a completed-trade curve in signal order, not a marked-to-market overlapping portfolio. Results do not establish a live trading edge.", ""]
    (OUTPUT / "REPORT.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()
    preserve_prior_reports()
    engine.ENTRY_MODE, engine.STOP_LOSS_MODE = "market", "fixed"
    engine.FIXED_STOP_POINTS, engine.ENTRY_DELAY_SECONDS = 10., 5
    signals = load_signals()
    histories, gaps, quality = download_validated_history(signals, OUTPUT)
    features = build_refined_features(signals, histories, gaps, latency_seconds=2).set_index("message_id")
    manifest = download_wait_windows(signals, OUTPUT, force=args.force_download)
    files = manifest.set_index(["message_id", "granularity"])["file"]
    strategies = list(STRATEGIES) + [Strategy("two_trades_50", partial_fraction=.5, partial_points=5.)]
    trades, decisions = [], []
    for count, (_, signal) in enumerate(signals.iterrows(), 1):
        message_id = int(signal.message_id)
        s5 = engine.load_prices(files.loc[(message_id, "S5")])
        m1 = engine.load_prices(files.loc[(message_id, "M1")])
        for variant in VARIANTS:
            decision = confirmation_decision(signal, variant, features.loc[message_id], m1, histories["M5"])
            decisions.append(decision)
            for strategy in strategies:
                trades.append(simulate_confirmed(signal, strategy, s5, m1, decision))
        if count % 25 == 0 or count == len(signals):
            print(f"Simulated current-candle entries: {count}/{len(signals)}", flush=True)
    decisions = pd.DataFrame(decisions)
    trades, summary = summarize(pd.DataFrame(trades), signals)
    decisions.to_csv(OUTPUT / "confirmation_decisions.csv", index=False)
    features.to_csv(OUTPUT / "signal_time_features.csv")
    trades.to_csv(OUTPUT / "trades.csv", index=False)
    summary.to_csv(OUTPUT / "comparison.csv", index=False)
    trades.groupby(["variant", "strategy", "status"]).size().rename("count").reset_index().to_csv(OUTPUT / "status_counts.csv", index=False)
    write_report(summary, decisions)
    inputs = [Path(__file__), Path("EXPERIMENT_1_2.md"), Path("data/signals/gold_signals_2026.csv"),
              Path("backtesting/engine.py"), Path("backtesting/strategies.py"), Path("backtesting/metrics.py"),
              Path("backtesting/indicators.py"), Path("backtesting/indicator_refinement.py"),
              Path("backtesting/wait_confirmation.py"), Path("market_data/wait_windows.py"),
              Path("market_data/indicator_history.py"), Path("market_data/history_validation.py"), Path("market_data/oanda_client.py")]
    inputs += [Path(p) for p in manifest.file]
    inputs += [Path(p) for p in pd.read_csv(OUTPUT / "history_manifest.csv").file]
    metadata = dict(created_utc=pd.Timestamp.now(tz="UTC").isoformat(), entry_mode="market",
                    stop_points=10, processing_minimum_seconds=5, publication_latency_seconds=2,
                    variants=VARIANTS, validation_start_utc=signals.iloc[int(len(signals)*.7)].timestamp_utc.isoformat(),
                    input_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})
    (OUTPUT / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    preserve_prior_reports()
    print(summary[summary.scope.eq("full") & summary.direction_group.eq("ALL") & summary.strategy.eq("fixed_5")][
        ["variant", "accepted_signals", "entered_trades", "completed_trades", "net_points", "profit_factor", "max_drawdown_points"]].to_string(index=False))
    print(f"\nSaved {OUTPUT}; prior reports unchanged.")


if __name__ == "__main__":
    main()
