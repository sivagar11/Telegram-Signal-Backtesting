# Experiment 2: midpoint limit entry (registered before running)

Run `.venv/bin/python -u run_midpoint_experiment.py`. Results are isolated in
`reports/experiment_2/`; all Experiment 1/1.1/1.2 reports are hash-preserved.
No indicator filters or reversal confirmation apply in this experiment.

## Planned variants and original stops

| Name | Entry | Target |
|---|---|---|
| A_boundary_reference | Hypothetical immediate BUY upper / SELL lower boundary | Five points beyond that boundary |
| A_market_reference | Immediate executable ASK/BID quote | Same boundary-based target |
| B_midpoint_3m_boundary_target | Midpoint limit, expires signal +3 minutes | Same boundary-based target |
| C_midpoint_5m_boundary_target | Midpoint limit, expires signal +5 minutes | Same boundary-based target |
| D_midpoint_3m_entry_target | Midpoint limit, expires signal +3 minutes | Five points from actual filled entry |

All use the **original Telegram stop**, not a ten-point fixed stop. For BUY,
boundary = upper range, limit = midpoint, boundary target = upper +5; for SELL,
boundary = lower range and boundary target = lower -5. Original TP1 is not an exit
in these fixed-target experiments and cannot invalidate an otherwise valid entry.
The supplied example BUY 4120–4125, original SL4115 gives midpoint4122.5,
boundary target4130 and nominal reward/risk7.5/7.5. D instead targets4127.5.

The historical boundary reference is explicitly hypothetical; it is never reported
as verified executable. A_market_reference isolates the impact of actual quotes
and is the executable immediate-entry comparator for midpoint variants.

## Order activation and expiry

Retain the five-second processing minimum. Use the first observed S5 open at or
after signal +5 seconds; additional quote wait must be <=15 seconds. Activate a
pending midpoint order there. The three/five-minute clock starts at the original
message timestamp, **including** processing and quote delay.

A BUY limit is marketable if ASK open <= midpoint, SELL if BID open >= midpoint;
fill immediately at that executable open, including favorable price improvement.
Otherwise use ASK low <= midpoint for BUY / BID high >= midpoint for SELL to
detect an intrabar touch, filling at the midpoint with no price improvement.
Validate original stop < fill < target for BUY, reversed for SELL. A quote already
beyond its stop/target is INVALID_SL_TP, not a fabricated valid fill. No additional
entry-range, maximum-chase or prior-TP cancellation rule is introduced.

An opening quote at expiry is eligible. Intrabar fills require the full S5 bar to
end by expiry. If a bar straddling expiry touches the limit only in its unknown
intrabar path, report AMBIGUOUS_ENTRY_DEADLINE rather than assume a timely fill
or a definite miss. Missing S5 data while an order is pending gives ENTRY_DATA_GAP
or ENTRY_WINDOW_INCOMPLETE; it must never become a NO_FILL. Missing/late activation
quotes are separate statuses. NO_FILL requires complete observed entry-window
coverage and no eligible touch.

## Fill-bar uncertainty and exits

For intrabar fills, record the containing five-second interval as lower/upper
fill-time bounds. If the liquidation-side candle can also hit the stop or target
in that same bar, classify AMBIGUOUS_ENTRY_EXIT, without invented realized P/L.
Otherwise begin exit simulation at the next bar. Opening fills can use that bar's
OHLC because entry preceded its high/low movements.

Exit BUYs against BID and SELLs against ASK. Stop gaps fill at the observed open;
favorable target gaps fill at the target (conservative). A later bar touching both
stop and target is AMBIGUOUS. Reuse the existing nonoverlapping S5-to-M1 timeline.
Monitor for five hours from the fill-time lower bound; for intrabar fills this is
conservatively up to five seconds short of five hours from the unknown exact fill.
Only full bars ending within that horizon are used. Data gaps terminate as DATA_GAP;
end-of-window open trades are UNRESOLVED, with no forced exit or realized P/L.

## Data and comparisons

Extend S5 through signal +eight minutes (rounded up to a minute), covering all
five-minute entry windows plus early exits. Extend M1 through signal +five minutes
+five hours +one minute, rounded up. Reuse checksummed Experiment 1.2 prefixes,
download only missing tails and save new validated caches under
`data/prices/experiment_2/`. Earlier data and reports remain unchanged.

Report full history, first70% and last30% using the original global chronological
boundary, separately for ALL/BUY/SELL. Include signal counts, activation availability,
known fills, fill rate over all signals, definite no-fills, deadline-ambiguous
entries, unavailable entry windows, completed/ambiguous/gap/unresolved exits, win
rate, net normalized points, expectancy, PF, average R and drawdown. Points per
received signal includes unfilled and excluded signals in its denominator, while
profit totals use completed outcomes only. Potential fills with ambiguous expiry
are not counted as definite fills. Compare outcomes on signals completed by both
an immediate reference and midpoint variant, and count immediate winners missed.

Report initial risk and nominal reward-to-risk using **actual fill-to-original-stop
distance**. There is no ATR filter here; any later combined ATR rule must use this
actual risk rather than the ten-point constant from Experiment 1.

## Limits

Five-second extrema are not ticks. Touch fills assume execution at the limit and
do not model order queues, slippage, commissions or financing. Both sides of the
quote inform entry/exit, but a low/high touch does not prove real liquidity for
an order. Conservative ambiguous classifications can exclude profitable or losing
trades unevenly. Drawdown is completed trades ordered by signal time, not portfolio
mark-to-market. The already-examined chronological validation set is diagnostic,
not untouched out-of-sample evidence. Report all preset variants without tuning.
