
from pathlib import Path

import pandas as pd

from backtesting.engine import run_all_strategies
from backtesting.metrics import calculate_metrics

SIGNALS_FILE = Path(
    "data/signals/gold_signals_2026.csv"
)

REPORT_DIR = Path("reports")


def main():
    REPORT_DIR.mkdir(
        parents=True, exist_ok=True
    )

    signals = pd.read_csv(SIGNALS_FILE)

    signals["timestamp_utc"] = pd.to_datetime(
        signals["timestamp_utc"],
        utc=True,
    )

    print("=" * 55)
    print("GOLD STRATEGY BACKTESTER - PHASE 3")
    print("=" * 55)

    print(f"Loaded {len(signals)} signals")

    results = run_all_strategies(signals)

    results_path = (
        REPORT_DIR / "backtest_trades.csv"
    )

    results.to_csv(
        results_path, index=False
    )

    summary = calculate_metrics(results)

    summary_path = (
        REPORT_DIR / "strategy_comparison.csv"
    )

    summary.to_csv(
        summary_path, index=False
    )

    print("\nSTRATEGY COMPARISON")
    print(
        summary.to_string(index=False)
    )

    print("\nRESULT STATUS COUNTS")
    print(
        pd.crosstab(
            results["strategy"],
            results["status"]
        ).to_string()
    )

    print("\nReports saved:")
    print(results_path)
    print(summary_path)


if __name__ == "__main__":
    main()
