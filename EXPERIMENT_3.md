# Experiment 3: immediate market entry, +3/+4/+5 targets

Run `.venv/bin/python -u run_market_targets.py`. Results go to
`reports/experiment_3/`. All earlier experiment reports are preserved by hash.
Reuse verified Experiment 2 S5/M1 windows; no further download is required.

Three preset scenarios: market_tp_3, market_tp_4 and market_tp_5. Entry rules and
original Telegram stop are identical; only target distance changes.

- Immediate means the existing processing assumption: first S5 opening quote
  at/after message +five seconds, no more than 15 seconds additionally late.
- BUY enters at ASK open, SELL at BID open. No range, midpoint, candle, indicator
  or trend filter; no waiting for retracement or candle close.
- SL is the message's original absolute stop price. If it is on the wrong side
  of the executable entry, reject as INVALID_SL_TP and report the count.
- BUY target = actual fill +3/+4/+5; SELL target = actual fill -3/-4/-5.
  Targets are not measured from the Telegram range or boundary. Telegram TP1 is
  replaced by the chosen fixed target and does not invalidate these experiments.
- Close the whole position at stop or target; no partials, trailing or breakeven.
- BUY exits use BID, SELL exits ASK. Stops gapped through fill at observed open;
  favorable target gaps fill at target conservatively. Bars hitting both levels
  are AMBIGUOUS with no assigned P/L.
- Monitor up to five hours from actual fill using the existing nonoverlapping
  S5-to-M1 timeline, only full candles ending within that deadline. Retain
  DATA_GAP/NO_EXIT_DATA/UNRESOLVED statuses and do not force close open trades.

All 394 signals remain in per-received-signal denominators. Report entered versus
completed counts, wins/losses, win rate, net normalized price points, profit factor,
expectancy, average R, original-stop risk, nominal reward/risk and drawdown. Use the
same global first-70%/last-30% split for ALL/BUY/SELL. Also compare profits on the
common set of signals with completed exits under all three targets, because target
distance can change how many trades remain open at a data gap or horizon.

Entry times/prices and stops must match across the three scenarios. Preserve all
trade details, status counts and hashed data/source provenance. Keep all preset
targets without selecting thresholds from results.

Prices are historical BID/ASK OHLC, not guaranteed tick fills. Quote rounding,
unknown intrabar sequencing, missing data, unmodeled slippage/commissions/financing,
overlapping positions and already-examined validation dates limit conclusions.
P/L excludes incomplete outcomes and is not account-currency return. Drawdown
uses completed trades ordered by signal time, not marked-to-market portfolio equity.
