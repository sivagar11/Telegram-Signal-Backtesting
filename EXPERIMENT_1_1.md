# Experiment 1.1: registered refinements

Run `.venv/bin/python -u run_indicator_refinement.py` from the project root.
All output goes to `reports/experiment_1_1/`. Experiment 1 outputs and legacy
history pages are preserved; a SHA-256 manifest verifies report preservation.

## Scope and frozen rules

Keep the original engine, six exit strategies, boundary entry, five-second entry
delay and fixed ten-point initial stop. Keep the original proximity SR rule and
all original indicator thresholds unchanged. Add the following hypothesis without
selecting thresholds based on its backtest results.

For each candidate completed M15 candle, calculate support/resistance from its
preceding 20 completed candles, and ATR14 from the previous completed candle.
BUY rejection: candidate low is within 0.25 ATR of support on either side, close
is at least 0.25 ATR above support, candle closes above its open and in the upper
half of its range. SELL mirrors this at resistance: high near resistance, close
at least 0.25 ATR below, bearish close in lower half. Range must be positive.
Accept a qualifying rejection in the last three available completed M15 candles,
whose close was at most 45 minutes before signal arrival. Require all three
candidates to have the full preceding level/ATR context.

Report standalone rejection, rejection + trend, rejection replacing proximity
in E, and rejection replacing proximity in F. Retain the original versions beside
them; no threshold relaxation or retrospective winner selection.

## Publication latency

Primary: a candle is available only at close + **two seconds** <= signal arrival.
Sensitivity: repeat at zero seconds. Two seconds is a research assumption, not
a measured OANDA latency. Both runs freeze the decision at signal arrival; neither
can use candles published during the later five-second execution delay.
Existing freshness limits measure age from candle close and remain unchanged.

## Historical validation and cache integrity

Require complete schema, explicit UTC-convertible timezone, ordered unique aligned
timestamps inside requested bounds, positive finite BID/ASK values, consistent
OHLC ranges and noncrossed corresponding BID/ASK prices. Reject explicit incomplete
or future-ending candles. OANDA already discards API candles marked incomplete.
Only identical duplicated page-boundary candles may be deduplicated by the API
client; conflicting duplicated candle prices fail.

Use New York timezone rules (including DST), published gold hours Sun–Fri
18:05–16:59, and the dated holiday overrides in history_validation.py. A coarse
candle is expected if any minute in its interval was scheduled open. Calendar
coverage is explicitly bounded to 2025-12-18 through 2026-10-27; requests outside
that range fail until the calendar is extended using broker sources.

Daily breaks, weekends and documented holiday closures are labeled separately;
they never reset indicators. Observed valid bars outside published session times
are retained and counted, since historical quote buckets can differ from published
trading availability. The calendar is a broker schedule model, not proof of every
historical quote outage or regional account schedule.

Unexplained missing open-session candles (including leading/trailing truncation)
are listed explicitly. Signals during such gaps fail the affected rules closed.
At the first observed candle after each gap, reset indicators and repeat full
warm-up: H1 1,000 candles, RSI/ATR 14 changes/observations, SR full preceding context.
No gap is interpolated or silently declared a scheduled closure. A missing single
bar is treated conservatively too. Persisting gaps require investigation; the
report must not describe a dataset with gaps as complete.

Version-2 pages go to `data/prices/indicators/validated_v2/`. Legacy CSVs are
structurally validated before adoption and retain their original hash as provenance.
Legacy integrity prior to adoption cannot be proven. Subsequent cache loads verify
request metadata, SHA-256, row count and candle validity. CSV and metadata each use
same-directory atomic replacement; metadata commits the CSV hash. An interrupted
pair update or a truncated/edited file cannot pass cache validation. Invalid v2
pages can be recovered from validated legacy data or redownloaded; coverage is
audited over the full combined request on every run. `--force-download` refreshes
from OANDA. Integrity hashes do not assert completeness of broker quote coverage.

## Comparisons

All six existing exit strategies, all filters, and latency 0/2 seconds are reported
for combined signals, BUY and SELL. Use the original global chronological boundary
(first 275 / last 119 for 394 signals), never a separate per-direction split.
Compare candle-only and trend + candle with same-direction unfiltered baselines,
for full/development/validation periods. Include sample sizes, incomplete statuses,
missed baseline winners/losers, expectancy, PF, drawdown and points per received
signal. The zero-delay original-filter decision comparison isolates data-audit
effects from the new latency assumption. Changed feature timestamps and filter
decisions are retained in auditable CSVs.

Any direct broker gap rechecks are timestamped separately in gap_rechecks.csv
alongside their returned candle files; rerunning the offline analysis retains
that evidence rather than implying that another network check occurred.

Drawdown and profits remain completed-trade metrics in signal order. Tiny direction
samples, already-inspected validation dates, hypothetical fills, intrabar ambiguity,
missing exit data, costs and portfolio overlap retain all Experiment 1 limitations.

The midpoint experiment is outside this run. Its ATR/risk ratio must use actual
entry-to-original-Telegram-stop distance, with direction validation. The ten-point
ratio here applies only to this explicitly frozen ten-point-stop baseline.

Calendar sources (checked 2026-10-27):

- https://www.oanda.com/uk-en/trading/hours-of-operation/
- https://www.oanda.com/uk-en/trading/holiday-trading-hours/
