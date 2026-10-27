
import pandas as pd


COMPLETED = {
    "STOP",
    "STOP_GAP",
    "TP1",
    "BREAKEVEN_STOP",
    "FIXED_TARGET",
}


def calculate_metrics(results):
    summaries = []

    for name, group in results.groupby("strategy"):
        completed = group[
            group["status"].isin(COMPLETED)
        ].copy()

        wins = completed[
            completed["points"] > 0
        ]

        losses = completed[
            completed["points"] < 0
        ]

        gross_profit = wins["points"].sum()
        gross_loss = abs(
            losses["points"].sum()
        )

        # This is the sum of normalized position
        # price-point P/L, not account currency.
        net_points = completed["points"].sum()

        completed_count = len(completed)

        if completed_count:
            win_rate = (
                len(wins) / completed_count * 100
            )
            expectancy = (
                net_points / completed_count
            )
            avg_r = completed["r_multiple"].mean()
        else:
            win_rate = None
            expectancy = None
            avg_r = None

        if gross_loss > 0:
            profit_factor = (
                gross_profit / gross_loss
            )
        else:
            profit_factor = None

        # Equity curve based on completed trades,
        # ordered by signal time.
        ordered = completed.sort_values(
            "timestamp_utc"
        )

        equity = ordered["points"].cumsum()
        equity = pd.concat(
            [pd.Series([0.0]), equity],
            ignore_index=True,
        )

        drawdown = equity - equity.cummax()

        max_drawdown = (
            abs(drawdown.min())
            if not drawdown.empty else 0
        )

        summaries.append({
            "strategy": name,
            "total_signals": len(group),
            "completed_trades": completed_count,
            "wins": len(wins),
            "losses": len(losses),
            "breakeven_trades": int(
                (completed["points"] == 0).sum()
            ),
            "ambiguous": int(
                (group["status"] == "AMBIGUOUS").sum()
            ),
            "unresolved": int(
                (group["status"] == "UNRESOLVED").sum()
            ),
            "net_points": round(net_points, 3),
            "win_rate_pct": (
                round(win_rate, 2)
                if win_rate is not None else None
            ),
            "profit_factor": (
                round(profit_factor, 3)
                if profit_factor is not None
                else None
            ),
            "expectancy_points": (
                round(expectancy, 3)
                if expectancy is not None
                else None
            ),
            "average_r": (
                round(avg_r, 3)
                if avg_r is not None
                else None
            ),
            "max_drawdown_points": round(
                max_drawdown, 3
            ),
        })

    return pd.DataFrame(summaries)
