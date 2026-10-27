# Experiment 2.1: one-minute range check

Run `.venv/bin/python -u run_range_wait.py`. Outputs are isolated in
`reports/experiment_2_1/`. All previous experiment reports are hash-preserved.
Existing Experiment 2 checksummed price windows are sufficient; no extra history
or downloads are needed.

## Primary rule

Wait **60 elapsed seconds from the Telegram message**, not until the current M1
candle closes. Select the first available S5 opening quote at/after that instant;
it must be no more than 15 seconds late. This is the same quote approximation as
earlier experiments. There is no additional five-second delay after the minute.

Check once: BUY uses ASK open; SELL uses BID open. Enter at that price only if
entry_low <= quote <= entry_high, including exact boundaries. If outside, skip
the signal as OUTSIDE_ENTRY_RANGE, even if it subsequently returns to the range.
Do not substitute candle highs/lows, a boundary price, a midpoint, or a later
qualifying quote. Missing/late quotes are unknown entry data, not range failures.
No directional candle, indicator or reversal confirmation is required.

Retain Experiment 2 exits: original Telegram stop; BUY target upper boundary +5,
SELL target lower boundary -5. Monitor up to five hours from actual fill. Use
nonoverlapping S5 then M1, BID liquidation for BUY and ASK for SELL, conservative
gap/ambiguous handling, and no forced exit. Fixed targets do not use Telegram TP1.

## Preset controls

- A_market_reference: immediate bid/ask entry after the original five-second delay.
- E_immediate_in_range: same immediate quote, require it inside the range.
- F_wait_60s_unfiltered: enter after 60 seconds regardless of range, subject to
  valid entry/stop/target geometry.
- G_wait_60s_in_range: the primary one-minute, one-check range strategy.
- B_midpoint_3m_boundary_target: repeat the earlier three-minute midpoint variant
  as a reference with identical data and exits.

These controls separate the effect of waiting from range filtering. All use the
same original stops and boundary-based targets; no settings are chosen from the
new results. Reproduce the prior immediate and midpoint reference outcomes before
interpreting new differences.

Report ALL/BUY/SELL and the same global full/development/last-30% chronological
split. Count entered, outside-range, unavailable, invalid and incomplete outcomes
separately. Include win rate, net price points, PF, expectancy, nominal reward/risk,
drawdown and points per received signal (including skipped signals in denominator).
Export checked quote times/prices so the single check can be audited.

Historical quote OHLC is not tick execution. Slippage, commissions, financing,
portfolio overlap, incomplete-outcome selection and previously examined validation
dates retain the limitations of earlier experiments. Profit/drawdown use completed
outcomes only, ordered by signal time, and are not account-currency returns.
