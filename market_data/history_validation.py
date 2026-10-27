"""Strict candle checks and a bounded, sourced XAU/USD session calendar."""
import numpy as np
import pandas as pd

from market_data.oanda_client import GRANULARITY_SECONDS, PRICE_COLUMNS, as_utc

CALENDAR_START = pd.Timestamp("2025-12-18", tz="UTC")
CALENDAR_END = pd.Timestamp("2026-10-28", tz="UTC")
SCHEDULE_SOURCES = [
    "https://www.oanda.com/uk-en/trading/hours-of-operation/",
    "https://www.oanda.com/uk-en/trading/holiday-trading-hours/",
]
# Exchange-local New York times, interpreted with DST; end is exclusive.
# Calendar intentionally bounded to this experiment, not a universal calendar.
HOLIDAY_CLOSURES = [
    ("2025-12-24 13:44", "2025-12-25 18:05", "Christmas"),
    ("2025-12-31 16:59", "2026-01-01 18:05", "New Year"),
    ("2026-01-19 14:29", "2026-01-19 18:05", "MLK Day"),
    ("2026-02-16 14:29", "2026-02-16 18:05", "Presidents Day"),
    ("2026-04-02 16:59", "2026-04-05 18:05", "Good Friday"),
    ("2026-05-25 14:29", "2026-05-25 18:05", "Memorial Day"),
    ("2026-06-19 12:59", "2026-06-21 18:05", "Juneteenth"),
    ("2026-07-03 12:59", "2026-07-05 18:05", "Independence Day"),
    ("2026-09-07 14:29", "2026-09-07 18:05", "Labor Day"),
]


def validate_candles(frame, start, end, timeframe):
    """Reject duplicates, malformed prices, partial bars, and truncated schemas."""
    missing = set(PRICE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"{timeframe}: missing columns {sorted(missing)}")
    frame = frame.copy()
    times = (pd.Series([], dtype="datetime64[ns, UTC]", index=frame.index)
             if frame.empty else pd.to_datetime(frame.time, errors="raise"))
    if times.dt.tz is None:
        raise ValueError(f"{timeframe}: timezone missing")
    frame["time"] = times.dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
    if frame.time.isna().any() or frame.time.duplicated().any():
        raise ValueError(f"{timeframe}: missing or duplicate timestamps")
    if not frame.time.is_monotonic_increasing:
        raise ValueError(f"{timeframe}: timestamps out of order")
    duration = pd.Timedelta(seconds=GRANULARITY_SECONDS[timeframe])
    if not frame.time.eq(frame.time.dt.floor(duration)).all():
        raise ValueError(f"{timeframe}: misaligned candle opening")
    if not (frame.time.ge(as_utc(start)) & frame.time.lt(as_utc(end))).all():
        raise ValueError(f"{timeframe}: candles outside requested bounds")
    if (frame.time + duration > pd.Timestamp.now(tz="UTC")).any():
        raise ValueError(f"{timeframe}: candle not yet complete")
    if "complete" in frame and not frame.complete.eq(True).all():
        raise ValueError(f"{timeframe}: incomplete candle")
    for column in PRICE_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    values = frame[PRICE_COLUMNS[1:]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError(f"{timeframe}: nonfinite or nonpositive prices")
    for side in ("bid", "ask"):
        lo, hi = frame[f"{side}_l"], frame[f"{side}_h"]
        if not ((lo <= hi) & frame[f"{side}_o"].between(lo, hi)
                & frame[f"{side}_c"].between(lo, hi)).all():
            raise ValueError(f"{timeframe}: invalid {side} OHLC bounds")
    for field in "ohlc":
        if (frame[f"ask_{field}"] < frame[f"bid_{field}"] - 1e-8).any():
            raise ValueError(f"{timeframe}: crossed BID/ASK {field}")
    return frame


def expected_slots(start, end, timeframe):
    """A candle is expected if its interval overlaps any scheduled open minute."""
    start, end = as_utc(start), as_utc(end)
    if start < CALENDAR_START or end > CALENDAR_END:
        raise ValueError("Session calendar does not cover requested dates; extend it from broker sources")
    minutes = pd.date_range(start, end, freq="min", inclusive="left")
    local = minutes.tz_convert("America/New_York")
    clock = local.hour * 60 + local.minute
    day = local.day_of_week
    opened = (((day <= 4) & (clock < 16 * 60 + 59))
              | (((day <= 3) | (day == 6)) & (clock >= 18 * 60 + 5)))
    labels = np.where(opened, "OPEN", np.where((day == 5) | ((day == 4) & (clock >= 1019))
                                              | ((day == 6) & (clock < 1085)), "WEEKEND", "DAILY_BREAK")).astype(object)
    for first, last, name in HOLIDAY_CLOSURES:
        a = pd.Timestamp(first, tz="America/New_York")
        b = pd.Timestamp(last, tz="America/New_York")
        labels[(local >= a) & (local < b)] = "HOLIDAY:" + name
    duration = pd.Timedelta(seconds=GRANULARITY_SECONDS[timeframe])
    minute_labels = pd.Series(labels, index=minutes)
    # Choose OPEN if any minute is open, else a holiday, else weekend/break.
    grouped = minute_labels.groupby(minutes.floor(duration))
    is_open = minute_labels.eq("OPEN").groupby(minutes.floor(duration)).any()
    slot_labels = grouped.first()
    slot_labels.loc[is_open] = "OPEN"
    return slot_labels


def audit_history(frame, start, end, timeframe):
    frame = validate_candles(frame, start, end, timeframe)
    slots = expected_slots(start, end, timeframe)
    absent = slots.loc[~slots.index.isin(frame.time)]
    gaps = []
    duration = pd.Timedelta(seconds=GRANULARITY_SECONDS[timeframe])
    for timestamp, label in absent.items():
        kind = "UNEXPECTED_GAP" if label == "OPEN" else label
        if gaps and gaps[-1]["end_utc"] == timestamp and gaps[-1]["kind"] == kind:
            gaps[-1]["end_utc"] = timestamp + duration
            gaps[-1]["missing_candles"] += 1
        else:
            gaps.append(dict(timeframe=timeframe, start_utc=timestamp, end_utc=timestamp + duration,
                             kind=kind, missing_candles=1))
    missing = absent.index[absent.eq("OPEN")]
    # Reset only after unexplained missing trading-session candles, never weekends.
    breaks = np.zeros(len(frame), dtype=bool)
    for timestamp in missing:
        position = frame.time.searchsorted(timestamp, side="right")
        if position < len(frame):
            breaks[position] = True
    frame["reset_before"] = breaks
    stats = dict(timeframe=timeframe, candles=len(frame), expected_open_candles=int(slots.eq("OPEN").sum()),
                 unexpected_missing_candles=len(missing),
                 scheduled_absent_candles=int(absent.ne("OPEN").sum()),
                 observed_outside_schedule=int((~frame.time.isin(slots.index[slots.eq("OPEN")])).sum()),
                 start_utc=as_utc(start).isoformat(), end_utc=as_utc(end).isoformat())
    return frame, pd.DataFrame(gaps, columns=["timeframe", "start_utc", "end_utc", "kind", "missing_candles"]), stats
