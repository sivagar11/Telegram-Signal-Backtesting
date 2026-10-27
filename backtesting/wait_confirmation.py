"""Current-candle confirmation decisions, separate from unchanged exit logic."""
import pandas as pd

from backtesting import engine

VARIANTS = {
    "immediate_market": (None, False),
    "latest_M5_candle": ("latest_M5", False),
    "latest_M5_trend_candle": ("latest_M5", True),
    "current_M1_candle": ("M1", False),
    "current_M1_trend_candle": ("M1", True),
    "current_M5_candle": ("M5", False),
    "current_M5_trend_candle": ("M5", True),
}


def directional_candle(candle, direction):
    opening, close = float(candle["bid_o"]), float(candle["bid_c"])
    high, low = float(candle["bid_h"]), float(candle["bid_l"])
    return (high > low and abs(close - opening) >= .5 * (high - low)
            and (close > opening if direction == "BUY" else close < opening))


def current_candle_bounds(timestamp, timeframe):
    frequency = {"M1": "min", "M5": "5min"}[timeframe]
    opening = pd.Timestamp(timestamp).floor(frequency)
    return opening, opening + pd.Timedelta(minutes=1 if timeframe == "M1" else 5)


def confirmation_decision(signal, name, features, m1, m5, latency_seconds=2):
    timeframe, require_trend = VARIANTS[name]
    timestamp = pd.Timestamp(signal["timestamp_utc"])
    row = dict(message_id=int(signal["message_id"]), variant=name, direction=signal["direction"],
               timestamp_utc=timestamp, decision="ACCEPTED", candle_open_time=pd.NaT,
               candle_close_time=pd.NaT, candle_available_time=pd.NaT,
               entry_ready_time=timestamp + pd.Timedelta(seconds=5),
               candle_passed=None, trend_required=require_trend,
               trend_at_signal=bool(features["trend"]), trend_ready_at_signal=bool(features["trend_ready"]))
    if timeframe == "latest_M5":
        row.update(candle_close_time=features["M5_close_time"],
                   candle_open_time=features["M5_close_time"] - pd.Timedelta(minutes=5),
                   candle_available_time=features["M5_available_time"],
                   candle_passed=bool(features["candle"]))
        if not features["candle_ready"]:
            row["decision"] = "CONFIRMATION_UNAVAILABLE"
        elif not row["candle_passed"]:
            row["decision"] = "CANDLE_REJECTED"
    elif timeframe is not None:
        opening, close = current_candle_bounds(timestamp, timeframe)
        available = close + pd.Timedelta(seconds=latency_seconds)
        row.update(candle_open_time=opening, candle_close_time=close,
                   candle_available_time=available,
                   entry_ready_time=max(row["entry_ready_time"], available))
        history = m1 if timeframe == "M1" else m5
        candidates = history.loc[history.time.eq(opening)]
        if len(candidates) != 1:
            row["decision"] = "CONFIRMATION_UNAVAILABLE"
        else:
            candle = candidates.iloc[0]
            row.update({f"confirmation_{field}": float(candle[f"bid_{field}"]) for field in "ohlc"})
            row["candle_passed"] = directional_candle(candle, signal["direction"])
            if not row["candle_passed"]:
                row["decision"] = "CANDLE_REJECTED"
    if require_trend:
        if not features["trend_ready"]:
            row["decision"] = "TREND_UNAVAILABLE"
        elif not features["trend"] and row["decision"] == "ACCEPTED":
            row["decision"] = "TREND_REJECTED"
    return row


def simulate_confirmed(signal, strategy, s5, m1, decision):
    if decision["decision"] != "ACCEPTED":
        result = engine.simulate(signal, strategy, s5.iloc[:0], m1.iloc[:0])
        result["status"] = decision["decision"]
    else:
        ready = decision["entry_ready_time"]
        entry, _ = engine.get_entry(signal, s5, ready_time=ready)
        if entry is not None:
            deadline = entry["time"] + pd.Timedelta(hours=5)
            fine = s5.loc[s5.time + pd.Timedelta(seconds=5) <= deadline]
            coarse = m1.loc[m1.time + pd.Timedelta(minutes=1) <= deadline]
        else:
            fine, coarse = s5, m1
        result = engine.simulate(signal, strategy, fine, coarse, ready_time=ready)
    result.update(variant=decision["variant"], confirmation_decision=decision["decision"],
                  candle_open_time=decision["candle_open_time"], candle_close_time=decision["candle_close_time"],
                  candle_available_time=decision["candle_available_time"], entry_ready_time=decision["entry_ready_time"])
    result["entered"] = (result["entry_price"] is not None
                          and result["status"] not in {"INVALID_RISK", "TP1_BEFORE_PARTIAL_TARGET"})
    if result["entered"]:
        result["wait_seconds"] = (result["entry_time"] - pd.Timestamp(signal["timestamp_utc"])).total_seconds()
    else:
        result["wait_seconds"] = None
    return result
