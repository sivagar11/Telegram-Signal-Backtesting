# Experiment 1: frozen first-pass indicator rules

Run `.venv/bin/python -u run_indicator_experiment.py` from the repository root.
Downloads resume from exact-request page caches. Use `--force-download` to refresh.
Reports go to `reports/experiment_1/`; existing reports are preserved.

## Baseline

Use all historical 2026 signals and the existing engine unchanged: hypothetical
BUY upper-boundary / SELL lower-boundary fills, five-second delay, fixed ten-point
initial stop, and the existing six exit strategies. This isolates filtering from
entry/exit changes. `original_tp1` names an exit strategy; its stop remains ten
points in this experiment. `two_trades_50` duplicates partial_50 economics.

## Data and causality

Download OANDA H1, M15 and M5 complete BID/ASK candles through the last signal.
H1 starts 90 calendar days before the first signal, M15 14 days, M5 seven days.
Indicators use BID OHLC consistently (not reconstructed midpoint highs/lows).
Both H1 EMAs require 1,000 observations before use. RSI and ATR use Wilder's
14-period smoothing seeded with an arithmetic average. Flat RSI is 50.

Join features by candle opening time plus duration <= signal timestamp. A candle
that closes exactly at signal time is available under this historical convention;
live publication/processing latency is not modeled. Maximum ages since last close
are H1 two hours, M15 45 minutes and M5 15 minutes. Missing, insufficiently warmed,
or stale required indicators produce INDICATOR_UNAVAILABLE, never an approval.
The engine's later five-second entry delay cannot admit additional indicator data.

## Rules chosen before observing filter outcomes

These are explicit research starting points, not optimized or proven thresholds.

| Rule | BUY | SELL |
|---|---|---|
| Trend | Latest H1 close > EMA50 > EMA200 | Close < EMA50 < EMA200 |
| Support/resistance | Latest M15 close within 0.5 M15 ATR of support | Within 0.5 M15 ATR of resistance |
| RSI | Latest closed M5 RSI14 in [50,70] | RSI14 in [30,50] |
| Candle | Latest M5 close > open; body >=50% of range | Close < open; body >=50% of range |
| ATR/risk | 10 / M5 ATR14 in [1,4], ATR >=3 times latest M5 closing spread | Same |

Support is the minimum low of the 20 completed M15 candles preceding the latest
completed candle; resistance is their maximum high. Distance is absolute, so a
small break through a level can qualify. This is a rolling-level proxy for
support/resistance, not a discretionary swing/market-structure classifier.
Zero-range candles fail candle confirmation; negative spreads fail the ATR rule.

A: trend. B: trend + support/resistance. C: trend + RSI. D: trend + candle.
E: trend + support/resistance + RSI + candle. F: E + ATR/risk.
Also report standalone support/resistance, RSI, candle and ATR rules for diagnosis.

## Evaluation

Compare each filter against the same unfiltered trades for every exit strategy.
Record accepted, rejected, unavailable, completed, ambiguous, unresolved and data-gap
counts; winning and losing baseline trades rejected; win rate, points, profit
factor, expectancy, average R and drawdown. Rejected includes unavailable signals
when counting missed baseline winners/losers, but availability has its own count.
Points per signal uses all signals received, including rejected and unavailable.

Report full history, first 70% of signals and final 30% chronologically (275/119 for
394 signals). Freeze thresholds across both periods. The final segment is a
chronological validation diagnostic, not a claim of untouched out-of-sample data;
the repository already contains full-period baseline results. Do not select a
winner from tiny samples, or tune on validation and still label it unseen.

## Interpretation limits

Retain existing engine handling of intrabar uncertainty and missing data.
Completed-trade metrics exclude ambiguous, unresolved and data-gap outcomes;
filter-dependent exclusions can bias comparisons. Hypothetical boundary fills
are not verified executable fills. Existing bid/ask prices inform exits, but
boundary entry does not model a real spread-paid fill, slippage, financing,
commissions, position overlap, or account sizing. P/L is normalized price points.
Drawdown uses completed trades ordered by signal time, not a marked-to-market
account curve. No trades are force-closed at the end of available monitoring data.

Files include per-signal feature timestamps, pass/fail reasons, filtered trade
outcomes, comparison tables, and hashed input provenance. Downloads and generated
reports remain gitignored under the repository's existing policy.

OANDA candle definitions: https://developer.oanda.com/rest-live-v20/instrument-df/
