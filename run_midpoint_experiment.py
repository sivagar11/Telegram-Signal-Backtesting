"""Run the four planned midpoint/target tests and an executable immediate reference."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from backtesting.engine import load_prices
from backtesting.metrics import COMPLETED, calculate_metrics
from backtesting.midpoint import VARIANTS, requests_for_midpoint, simulate_midpoint
from market_data.downloader import load_signals
from market_data.wait_windows import download_wait_windows

OUTPUT = Path("reports/experiment_2")


def hashes(paths):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def preserve_prior_reports():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = OUTPUT / "prior_reports_sha256.json"
    snapshot = hashes(sorted(p for name in ["experiment_1", "experiment_1_1", "experiment_1_2"]
                             for p in (Path("reports") / name).rglob("*") if p.is_file()))
    if manifest.exists() and json.loads(manifest.read_text()) != snapshot:
        raise ValueError("Earlier experiment reports changed since preservation")
    manifest.write_text(json.dumps(snapshot, indent=2) + "\n")


def summarize(trades, signals):
    reference = trades[trades.variant.eq("A_market_reference")][["message_id", "status", "points"]].rename(
        columns={"status": "market_reference_status", "points": "market_reference_points"})
    trades = trades.merge(reference, on="message_id", validate="many_to_one")
    boundary = int(len(signals) * .7)
    scopes = {"full": set(signals.message_id), "development_first_70pct": set(signals.iloc[:boundary].message_id),
              "validation_last_30pct": set(signals.iloc[boundary:].message_id)}
    summaries = []
    for scope, ids in scopes.items():
        for direction in ["ALL", "BUY", "SELL"]:
            selected = trades[trades.message_id.isin(ids)]
            if direction != "ALL":
                selected = selected[selected.direction.eq(direction)]
            for _, metric in calculate_metrics(selected).iterrows():
                group = selected[selected.variant.eq(metric.strategy)]
                filled = group.entered
                completed = group.status.isin(COMPLETED)
                paired = completed & group.market_reference_status.isin(COMPLETED)
                summaries.append({"scope": scope, "direction_group": direction, "variant": metric.strategy,
                    **metric.to_dict(), "activation_available": int(group.activation_time.notna().sum()),
                    "known_fills": int(filled.sum()), "fill_rate_pct": round(filled.mean()*100, 2),
                    "no_fill": int(group.status.eq("NO_FILL").sum()),
                    "outside_entry_range": int(group.status.eq("OUTSIDE_ENTRY_RANGE").sum()),
                    "ambiguous_entry_deadline": int(group.status.eq("AMBIGUOUS_ENTRY_DEADLINE").sum()),
                    "ambiguous_entry_exit": int(group.status.eq("AMBIGUOUS_ENTRY_EXIT").sum()),
                    "entry_data_unavailable": int(group.status.isin(["NO_ENTRY_DATA", "NO_ENTRY_QUOTE", "ENTRY_QUOTE_TOO_LATE", "ENTRY_DATA_GAP", "ENTRY_WINDOW_INCOMPLETE"]).sum()),
                    "invalid_entry": int(group.status.isin(["INVALID_SIGNAL", "INVALID_DIRECTION", "INVALID_SL_TP"]).sum()),
                    "data_gaps": int(group.status.eq("DATA_GAP").sum()),
                    "no_exit_data": int(group.status.eq("NO_EXIT_DATA").sum()),
                    "mean_wait_seconds_lower": group.loc[filled, "wait_seconds"].mean(),
                    "mean_initial_risk": group.loc[filled, "initial_risk"].mean(),
                    "mean_nominal_reward_risk": group.loc[filled, "nominal_reward_risk"].mean(),
                    "improved_open_fills": int((filled & group.fill_kind.eq("OPEN") & group.midpoint_price_improvement.gt(0)).sum()),
                    "market_winners_not_filled": int((~filled & group.market_reference_status.isin(COMPLETED) & group.market_reference_points.gt(0)).sum()),
                    "market_winners_definitely_missed": int((group.status.eq("NO_FILL") & group.market_reference_status.isin(COMPLETED) & group.market_reference_points.gt(0)).sum()),
                    "paired_completed": int(paired.sum()),
                    "paired_points_difference": round((group.loc[paired, "points"] - group.loc[paired, "market_reference_points"]).sum(), 3),
                    "points_per_received_signal": round(metric.net_points / len(group), 4),
                })
    return trades, pd.DataFrame(summaries)


def write_report(summary):
    def fmt(value):
        return "—" if pd.isna(value) else f"{value:.3f}".rstrip("0").rstrip(".")
    lines = ["# Experiment 2: midpoint entry", "",
        "Preset rules: EXPERIMENT_2.md. Original Telegram SL throughout; no indicators or reversal confirmation.",
        "A_boundary_reference is hypothetical. A_market_reference uses executable ASK/BID open with the same boundary-based target.",
        "B/C target BUY upper +5 or SELL lower -5; D targets five points from its actual filled entry.",
        "Entry clocks expire three/five minutes after the message, including the five-second processing minimum.",
        "P/L is completed-trade normalized price points; unknown entry/exit ordering is excluded, not assigned a profit.", ""]
    for scope in ["full", "validation_last_30pct"]:
        lines += [f"## {scope}", "",
            "| Variant | Received | Known fills | Fill % | Definite no-fill | Entry timing unknown | Completed | Win % | Points | PF | Expectancy | Max DD | Points/signal |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        rows = summary[summary.scope.eq(scope) & summary.direction_group.eq("ALL")].set_index("variant")
        for variant in VARIANTS:
            r = rows.loc[variant.name]
            lines.append(f"| {variant.name} | {r.total_signals} | {r.known_fills} | {fmt(r.fill_rate_pct)} | {r.no_fill} | {r.ambiguous_entry_deadline} | {r.completed_trades} | {fmt(r.win_rate_pct)} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.expectancy_points)} | {fmt(r.max_drawdown_points)} | {fmt(r.points_per_received_signal)} |")
        lines += ["", "| Variant | Mean risk | Mean nominal reward/risk | Mean wait lower (s) | Entry-bar exit unknown | Later-bar ambiguous | Exit gaps | Unresolved | Entry data unavailable | Invalid entry | Market winners definitely missed |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for variant in VARIANTS:
            r = rows.loc[variant.name]
            lines.append(f"| {variant.name} | {fmt(r.mean_initial_risk)} | {fmt(r.mean_nominal_reward_risk)} | {fmt(r.mean_wait_seconds_lower)} | {r.ambiguous_entry_exit} | {r.ambiguous} | {r.data_gaps} | {r.unresolved} | {r.entry_data_unavailable} | {r.invalid_entry} | {r.market_winners_definitely_missed} |")
        lines.append("")
    lines += ["## Directional validation", "",
              "| Direction | Variant | Fills | Completed | Points | PF | Max DD |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for _, r in summary[summary.scope.eq("validation_last_30pct") & summary.direction_group.ne("ALL")].iterrows():
        lines.append(f"| {r.direction_group} | {r.variant} | {r.known_fills} | {r.completed_trades} | {fmt(r.net_points)} | {fmt(r.profit_factor)} | {fmt(r.max_drawdown_points)} |")
    lines += ["", "## Interpretation", "",
        "Compare executable midpoint variants against A_market_reference. The hypothetical boundary reference is retained to match the original plan but has unverified fills.",
        "A better nominal reward/risk does not establish higher profit: inspect fill rate, missed immediate winners, points per signal and exclusions. The three/five-minute comparison changes only order expiry; B and D share entry rules and differ only in target.",
        "Marketable midpoint limits may fill immediately at a better open, so some midpoint orders do not wait for a later retracement. INTRABAR_LIMIT times are five-second intervals, not exact tick timestamps.",
        "AMBIGUOUS_ENTRY_DEADLINE is not counted as a known fill. AMBIGUOUS_ENTRY_EXIT is a known fill with unknowable fill-bar exit order; later AMBIGUOUS, DATA_GAP, NO_EXIT_DATA and UNRESOLVED also contribute no completed-trade P/L. This can bias comparisons; all status counts remain visible.",
        "Entry-data gaps never become definite misses. Definite no-fills count as received signals in per-signal metrics. Five-hour horizons start at fill-time lower bounds and do not force close open trades.",
        "The previously examined chronological validation period is diagnostic. Small samples, touch-fill assumptions, omitted execution costs beyond quoted spread, and signal-ordered rather than portfolio drawdown prevent claims of a verified live edge.", ""]
    full = summary[summary.scope.eq("full") & summary.direction_group.eq("ALL")].set_index("variant")
    validation = summary[summary.scope.eq("validation_last_30pct") & summary.direction_group.eq("ALL")].set_index("variant")
    b, c, d = (full.loc[name] for name in ["B_midpoint_3m_boundary_target", "C_midpoint_5m_boundary_target", "D_midpoint_3m_entry_target"])
    lines += ["## First-pass findings", "",
        f"The three-minute boundary-target variant produced {fmt(b.net_points)} points from {b.completed_trades} completed outcomes and {b.known_fills} known fills ({fmt(b.fill_rate_pct)}% of all signals). "
        f"Its executable immediate reference produced {fmt(full.loc['A_market_reference'].net_points)} points. "
        f"There were {b.entry_data_unavailable} signals with unavailable entry-window data and {b.no_fill} definite misses; {b.market_winners_definitely_missed} immediate-reference winners were definitely missed.", "",
        f"The five-minute window increased known fills to {c.known_fills} but produced {fmt(c.net_points)} points, versus {fmt(b.net_points)} for three minutes. "
        "This preset comparison does not support extending the wait on this sample.", "",
        f"Targeting five points from actual entry produced {fmt(d.net_points)} full-period points. "
        f"On the final chronological segment, entry-based and boundary-based three-minute targets produced "
        f"{fmt(validation.loc['D_midpoint_3m_entry_target'].net_points)} and {fmt(validation.loc['B_midpoint_3m_boundary_target'].net_points)} points respectively. "
        "Their relative ranking is not consistent across periods; neither is established as a robust winner.", "",
        f"Mean nominal reward/risk on known fills was {fmt(b.mean_nominal_reward_risk)} for the three-minute boundary target, "
        f"versus {fmt(full.loc['A_market_reference'].mean_nominal_reward_risk)} for immediate executable entry. "
        "This is a ratio of target distance to original-stop distance, not an observed investment return.", ""]
    (OUTPUT / "REPORT.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()
    preserve_prior_reports()
    signals = load_signals()
    source = Path("reports/experiment_1_2/execution_manifest.csv")
    manifest = download_wait_windows(signals, OUTPUT, force=args.force_download,
        request_builder=requests_for_midpoint, window_dir="data/prices/experiment_2",
        source_manifest=pd.read_csv(source) if source.exists() else None)
    files = manifest.set_index(["message_id", "granularity"])["file"]
    trades = []
    for count, (_, signal) in enumerate(signals.iterrows(), 1):
        key = int(signal.message_id)
        s5 = load_prices(files.loc[(key, "S5")])
        m1 = load_prices(files.loc[(key, "M1")])
        trades.extend(simulate_midpoint(signal, variant, s5, m1) for variant in VARIANTS)
        if count % 25 == 0 or count == len(signals):
            print(f"Midpoint simulations: {count}/{len(signals)}", flush=True)
    trades, summary = summarize(pd.DataFrame(trades), signals)
    trades.to_csv(OUTPUT / "trades.csv", index=False)
    summary.to_csv(OUTPUT / "comparison.csv", index=False)
    trades.groupby(["variant", "status"]).size().rename("count").reset_index().to_csv(OUTPUT / "status_counts.csv", index=False)
    write_report(summary)
    inputs = [Path(__file__), Path("EXPERIMENT_2.md"), Path("data/signals/gold_signals_2026.csv"),
              Path("backtesting/midpoint.py"), Path("backtesting/engine.py"), Path("backtesting/metrics.py"),
              Path("market_data/wait_windows.py"), Path("market_data/oanda_client.py"),
              Path("market_data/indicator_history.py"), Path("market_data/history_validation.py")]
    inputs += [Path(p) for p in manifest.file]
    inputs += [Path(p).with_suffix(".json") for p in manifest.file]
    metadata = dict(created_utc=pd.Timestamp.now(tz="UTC").isoformat(), variants=[asdict(v) for v in VARIANTS],
                    stop_loss_mode="original", processing_minimum_seconds=5, expiry_origin="signal_timestamp",
                    horizon_hours=5, validation_start_utc=signals.iloc[int(len(signals)*.7)].timestamp_utc.isoformat(),
                    input_sha256=hashes(inputs))
    (OUTPUT / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    preserve_prior_reports()
    print(summary[summary.scope.eq("full") & summary.direction_group.eq("ALL")][
        ["variant", "known_fills", "no_fill", "completed_trades", "net_points", "profit_factor", "max_drawdown_points"]].to_string(index=False))
    print(f"\nSaved {OUTPUT}; earlier reports unchanged.")


if __name__ == "__main__":
    main()
