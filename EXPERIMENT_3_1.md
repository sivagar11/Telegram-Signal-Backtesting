# Experiment 3.1: original versus fixed stops, immediate market entry and +3 TP

Run `.venv/bin/python -u run_stop_comparison.py`. Output is isolated in
`reports/experiment_3_1/`; earlier reports are preserved by hash. Existing verified
Experiment 2 S5/M1 execution data is sufficient, with no new download needed.

| Variant | Entry | Stop | Target |
|---|---|---|---|
| A_original_sl_tp3 | Immediate executable market | Original Telegram SL | Fill +/-3 |
| B_fixed5_sl_tp3 | Same fill | Five points from actual fill | Fill +/-3 |
| C_fixed6_sl_tp3 | Same fill | Six points from actual fill | Fill +/-3 |

BUY fills ASK, SELL BID. Retain the original five-second processing minimum,
first available S5 open at/after that time, and maximum additional quote wait of
15 seconds. No range, indicator, candle or midpoint filter. No partial exits,
breakeven moves or trailing stops. Entry times/prices and target prices must match
across all three scenarios. Fixed stops are fill-5/fill-6 for BUY and fill+5/fill+6
for SELL. Preserve the message stop separately as original_stop_loss in every row.

Reuse Experiment 3 exit handling: BUY liquidates on BID, SELL on ASK; stop gaps
fill at observed open and can lose more than the nominal stop distance. Target
gaps fill conservatively at target. Both levels touched within a bar is AMBIGUOUS.
Monitor five hours from actual fill with nonoverlapping S5-to-M1 candles; retain
missing-data/unresolved statuses without invented or forced exits.

A must reproduce all Experiment 3 market_tp_3 per-trade statuses and outcomes.
Report ALL/BUY/SELL with the same global chronological first70%/last30% split.
Include completion counts, win rate, profit factor, expectancy, price-point net
profit, mean R, drawdown, initial risk, nominal reward/risk and incomplete outcomes.
Also compare the common set completed under all three stops, since tighter stops
can close before a later data gap and change the completed-outcome sample.
All received signals remain in points-per-signal denominators.

Fixed-five nominal reward/risk is 3/5 =0.6, fixed-six is 3/6 =0.5. These variants
use the same position size in normalized points, not equal account risk; compare
R separately and do not interpret point profits as currency or portfolio returns.
Intrabar uncertainty, missing data, unmodeled slippage/commission/financing,
overlapping positions, signal-ordered drawdown and previously examined validation
dates retain earlier limitations. All stops were preset; no threshold tuning.
