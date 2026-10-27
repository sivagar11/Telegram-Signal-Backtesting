# Experiment 1.2: confirm the candle forming at signal arrival

Run `.venv/bin/python -u run_wait_confirmation.py` at the repository root.
Outputs go to `reports/experiment_1_2/`; all prior experiment outputs are preserved
and verified by hash. Execution windows are cached separately under
`data/prices/experiment_1_2/`, with validated, checksummed request metadata.

## Frozen question and entry timing

Wait for the **current** M1 or M5 candle to close and confirm the signal. The user
explicitly selected directional candle confirmation with body >=50% of range.
The candle is the interval containing signal timestamp: open <= signal < close.
A message exactly on a boundary belongs to the newly opening candle, so waits
one full interval. A message at 10:02:20 waits until 10:03 M1 / 10:05 M5.

Use the entire current candle OHLC, including its pre-message portion, but only
after it closes. BUY: BID close > open and body >=50% of high-low; SELL reverses
direction. Zero-range candles fail. No substitution of an earlier completed
candle if the exact current candle is missing. Primary publication delay is two
seconds. Entry-ready time is max(signal + five seconds, candle close + two seconds).
The five-second processing minimum is not added again after confirmation.
Use the first S5 open at or after ready time, with maximum additional quote wait
15 seconds. BUY fills ASK open; SELL fills BID open. No fill at the earlier
Telegram boundary or at the just-closed candle's BID close.

## Comparisons and exits

Seven entry/filter variants, each using all six existing exit strategies:

1. Immediate unfiltered market entry, signal + five seconds.
2. Immediate entry with latest available closed M5 candle confirmation.
3. Same as 2 plus H1 trend.
4. Wait for current M1 confirmation.
5. Same as 4 plus H1 trend frozen at signal arrival.
6. Wait for current M5 confirmation.
7. Same as 6 plus H1 trend frozen at signal arrival.

Trend remains H1 close > EMA50 > EMA200 for BUY, reversed for SELL, using only
data available at original signal arrival including two-second publication delay,
the existing warm-up, freshness and gap checks. It is not recalculated after waiting.
The immediate closed-M5 comparators are the same candle hypotheses as Experiment
1.1, with executable entries for fair comparisons. Historical hypothetical boundary
results remain a separate reference, not the delayed-entry benchmark.

Keep the fixed ten-point initial stop and existing exit logic: original TP1,
partial 50%/75% at +5 with remaining stop to breakeven, fixed +5, fixed +5 with
breakeven at +3, and the two-leg 50% equivalent. Fixed/partial targets are based
on the actual new fill. Telegram TP1 remains the signal's absolute TP1 price;
entries already beyond it retain the engine's INVALID_SL_TP status. Partial exits
with TP1 before their +5 target retain TP1_BEFORE_PARTIAL_TARGET. No new range
or maximum-chase filter is added. The midpoint/original-stop experiment is separate.

## Data and monitoring

Download each signal's complete M1 candles from the signal minute through current
M5 close + five hours + one minute, and S5 from signal's five-second bucket through
current M5 close + three minutes. Existing validated M5/H1 history supplies the
exact current M5 candle and signal-time trend. No previous files are overwritten.
Reuse schema/price/timestamp validation, request hashes and interrupted-write
protection. Four download workers are rate limited to ten initial requests/sec.

Monitor each entered trade for up to five hours **from its actual fill**, using
S5 first and then nonoverlapping M1 via the existing engine. Discard coarse bars
ending beyond that deadline. No pre-entry high/low is used for trade exits;
the confirmation candle itself is excluded from exit monitoring. Missing quote
windows, confirmation bars, data gaps, ambiguous and unresolved exits are explicit.
No forced close or invented P/L at the horizon.

## Reporting and limits

Report ALL/BUY/SELL and the same global first-70%/last-30% chronological split.
Include rejected/missing confirmations, accepted versus entered versus completed,
wait and entry-price change diagnostics, status counts, missed winners, win rate,
net price points, PF, expectancy, drawdown and points per signal received.
Entry simulations use original signal timestamps for ordering and traceability;
completed-trade drawdown remains signal-ordered, not portfolio mark-to-market.

Waiting observes future information relative to signal arrival **legitimately by
delaying execution**; entry is never backdated. Historical validation is already
examined, and small samples are exploratory. BID/ASK OHLC approximates executable
quotes but does not model tick sequencing, slippage, commissions, financing,
network latency beyond the assumption or overlapping portfolio positions.
