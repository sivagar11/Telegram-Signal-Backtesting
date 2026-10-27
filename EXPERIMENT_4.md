# Experiment 4: immediate in-range entry or wait for the range

Run `.venv/bin/python -u run_range_return.py`. Output: `reports/experiment_4/`.
All prior experiment reports are hash-preserved. Reuse verified Experiment 2
execution windows; they cover both wait periods and five-hour trade monitoring.

## Six preset combinations

Test 3-minute and 5-minute pending-entry expiries, each with +3/+4/+5 targets
anchored to the **favorable Telegram range boundary**: BUY upper +distance,
SELL lower -distance. Targets are not from actual fill or midpoint. Original
Telegram SL applies in every case. No candle, indicator, breakeven or partial exits.

Example BUY range4120–4125, SL4115: targets4128/4129/4130. If first ASK is4123,
enter immediately at4123. If first ASK is4127, wait for4125; if first ASK is4118,
wait for4120. The target anchor remains4125 in both cases. SELL mirrors the quote
side and target direction, using BID entry and lower-boundary targets.

Immediate means the existing five-second processing minimum: first S5 open at
or after message+5s, no more than 15s additionally late. A quote is in range when
entry_low <= executable quote <= entry_high, including boundaries. BUY uses ASK,
SELL BID; the user's word "bid" is treated as placing the trade, preserving correct
execution sides. Wait clocks start at original signal time, including processing.

## Pending entry

If initially above the range, the first boundary sought is the upper boundary.
If initially below, it is the lower boundary. This applies to either direction;
depending on side it represents a retracement limit or continuation stop entry.
Use ASK/BID high/low to detect the relevant intrabar touch. Fill that boundary
with a five-second fill-time interval; reuse conservative fill-bar exit uncertainty.

If a later observed open lies inside the range, use that actual executable quote,
not a boundary quote that was never observed at that open. If an open jumps across
the entire range to the opposite side, classify AMBIGUOUS_ENTRY_GAP without assuming
a boundary fill. Both are explicit execution approximations; stop-order slippage
or exact queue execution at intrabar touches is unknown in OHLC data.

Opening quotes exactly at expiry are eligible. A touch in a bar straddling expiry
is AMBIGUOUS_ENTRY_DEADLINE, not a definite fill/miss. Gaps while pending produce
ENTRY_DATA_GAP/ENTRY_WINDOW_INCOMPLETE. NO_FILL requires complete observed coverage.
If a filled intrabar candle also touches an exit, classify AMBIGUOUS_ENTRY_EXIT.

Use the existing BID liquidation for BUY / ASK for SELL, stop-gap fills at observed
open, conservative target fills at target and AMBIGUOUS for later bars touching
both exits. Monitor five hours from fill-time lower bound using S5 then M1,
excluding incomplete horizon bars; no forced close or invented P/L for unknowns.

## Evaluation

Report all six combinations for ALL/BUY/SELL and the original global full/first70%
/last30% chronology. Distinguish immediate-in-range fills, waited boundary fills,
waited opening fills, no-fill, uncertain entry, entry-data gaps and incomplete exits.
Include win rate, price-point profits, PF, expectancy, mean R, drawdown, fill rate
and points per all signals received. Within each wait period compare the common
set completed under all three targets. Entries should match across targets; only
target price and subsequent outcome differ.

Keep all preset results without tuning. Hypothetical touch execution, unknown
intrabar order, spread, unmodeled additional costs, missing outcomes, overlapping
positions and already-examined validation dates prevent claims of a live edge.
Drawdown is a completed-trade curve in signal order, not portfolio equity.
