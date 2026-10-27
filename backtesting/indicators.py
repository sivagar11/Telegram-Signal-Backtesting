"""Causal indicator features and fixed, untuned signal-confirmation rules.

Indicators use BID OHLC. Execution remains the existing engine's BID/ASK logic.
Each feature is joined by candle CLOSE time, never by candle opening time.
"""
import numpy as np
import pandas as pd

SECONDS = {"H1": 3600, "M15": 900, "M5": 300}
MAX_AGE = {"H1": 7200, "M15": 2700, "M5": 900}
FILTERS = {
    "baseline": (),
    "A_trend": ("trend",),
    "B_trend_sr": ("trend", "sr"),
    "C_trend_rsi": ("trend", "rsi"),
    "D_trend_candle": ("trend", "candle"),
    "E_all_confirmation": ("trend", "sr", "rsi", "candle"),
    "F_confirmation_atr": ("trend", "sr", "rsi", "candle", "atr"),
    "standalone_sr": ("sr",),
    "standalone_rsi": ("rsi",),
    "standalone_candle": ("candle",),
    "standalone_atr": ("atr",),
}
RULE_TIMEFRAMES = {"trend": "H1", "sr": "M15", "rsi": "M5", "candle": "M5", "atr": "M5"}


def wilder_average(series, period=14):
    """Wilder smoothing seeded with the first period's arithmetic average."""
    series = series.astype(float)
    seed = series.rolling(period, min_periods=period).mean().first_valid_index()
    result = pd.Series(np.nan, index=series.index, dtype=float)
    if seed is None:
        return result
    position = series.index.get_loc(seed)
    result.iloc[position] = series.iloc[position - period + 1:position + 1].mean()
    for i in range(position + 1, len(series)):
        result.iloc[i] = (result.iloc[i - 1] * (period - 1) + series.iloc[i]) / period
    return result


def atr(frame, period=14):
    previous = frame.bid_c.shift()
    tr = pd.concat([frame.bid_h - frame.bid_l,
                    (frame.bid_h - previous).abs(),
                    (frame.bid_l - previous).abs()], axis=1).max(axis=1)
    return wilder_average(tr, period)


def rsi(close, period=14):
    changes = close.diff()
    gains = wilder_average(changes.clip(lower=0), period)
    losses = wilder_average((-changes).clip(lower=0), period)
    value = 100 - 100 / (1 + gains / losses)
    value = value.mask((losses == 0) & (gains > 0), 100)
    return value.mask((losses == 0) & (gains == 0), 50)


def candle_features(history, timeframe):
    frame = history.sort_values("time").drop_duplicates("time").reset_index(drop=True).copy()
    frame["time"] = pd.to_datetime(frame.time, utc=True).astype("datetime64[ns, UTC]")
    if not frame.time.is_monotonic_increasing or frame.time.isna().any():
        raise ValueError(f"Invalid {timeframe} timestamps")
    prices = frame.filter(regex=r"^(bid|ask)_[ohlc]$")
    if not np.isfinite(prices.to_numpy(dtype=float)).all():
        raise ValueError(f"Nonfinite {timeframe} prices")
    out = pd.DataFrame({f"{timeframe}_close_time": frame.time + pd.Timedelta(seconds=SECONDS[timeframe]),
                        f"{timeframe}_close": frame.bid_c})
    if timeframe == "H1":
        out["ema50"] = frame.bid_c.ewm(span=50, adjust=False, min_periods=1000).mean()
        out["ema200"] = frame.bid_c.ewm(span=200, adjust=False, min_periods=1000).mean()
    elif timeframe == "M15":
        # Levels precede the latest completed candle: it cannot define its own level.
        out["support20"] = frame.bid_l.shift().rolling(20, min_periods=20).min()
        out["resistance20"] = frame.bid_h.shift().rolling(20, min_periods=20).max()
        out["atr_m15"] = atr(frame)
    else:
        out["rsi14"] = rsi(frame.bid_c)
        out["atr_m5"] = atr(frame)
        out["m5_open"] = frame.bid_o
        out["m5_high"] = frame.bid_h
        out["m5_low"] = frame.bid_l
        out["m5_spread"] = frame.ask_c - frame.bid_c
    return out


def build_signal_features(signals, histories, publication_latency_seconds=0, feature_builder=None):
    if publication_latency_seconds < 0:
        raise ValueError("Publication latency must be nonnegative")
    feature_builder = feature_builder or candle_features
    features = signals.sort_values("timestamp_utc").reset_index(drop=True).copy()
    features["timestamp_utc"] = pd.to_datetime(features.timestamp_utc, utc=True).astype("datetime64[ns, UTC]")
    for timeframe in SECONDS:
        right = feature_builder(histories[timeframe], timeframe)
        close_time = f"{timeframe}_close_time"
        available_time = f"{timeframe}_available_time"
        right[available_time] = right[close_time] + pd.Timedelta(seconds=publication_latency_seconds)
        features = pd.merge_asof(features, right, left_on="timestamp_utc", right_on=available_time,
                                 direction="backward", allow_exact_matches=True)
        age = (features.timestamp_utc - features[close_time]).dt.total_seconds()
        features[f"{timeframe}_age_seconds"] = age
        features[f"{timeframe}_fresh"] = age.between(0, MAX_AGE[timeframe])
    if not features.direction.isin(["BUY", "SELL"]).all():
        raise ValueError("Signal directions must be BUY or SELL")
    buy = features.direction.eq("BUY")
    features["trend"] = np.where(buy,
        (features.H1_close > features.ema50) & (features.ema50 > features.ema200),
        (features.H1_close < features.ema50) & (features.ema50 < features.ema200))
    features["sr_distance"] = np.where(buy,
        (features.M15_close - features.support20).abs(),
        (features.M15_close - features.resistance20).abs())
    features["sr"] = features.sr_distance <= 0.5 * features.atr_m15
    features["rsi"] = np.where(buy, features.rsi14.between(50, 70), features.rsi14.between(30, 50))
    body = (features.M5_close - features.m5_open).abs()
    span = features.m5_high - features.m5_low
    directional = np.where(buy, features.M5_close > features.m5_open, features.M5_close < features.m5_open)
    features["candle"] = directional & (span > 0) & (body >= 0.5 * span)
    # The baseline fixes initial risk to 10 points for every entry strategy.
    features["stop_atr_ratio"] = 10.0 / features.atr_m5
    features["atr"] = (features.stop_atr_ratio.between(1, 4)
                       & (features.m5_spread >= 0)
                       & (features.atr_m5 >= 3 * features.m5_spread))
    required = {
        "trend": ["ema50", "ema200", "H1_close"],
        "sr": ["support20", "resistance20", "atr_m15", "M15_close"],
        "rsi": ["rsi14"],
        "candle": ["m5_open", "M5_close", "m5_high", "m5_low"],
        "atr": ["atr_m5", "m5_spread"],
    }
    for rule, columns in required.items():
        features[f"{rule}_ready"] = (features[columns].notna().all(axis=1)
            & features[f"{RULE_TIMEFRAMES[rule]}_fresh"])
    return features


def filter_decisions(features, filters=None):
    rows = []
    for name, rules in (FILTERS if filters is None else filters).items():
        for _, signal in features.iterrows():
            unavailable = [rule for rule in rules if not signal[f"{rule}_ready"]]
            failed = [rule for rule in rules if signal[f"{rule}_ready"] and not signal[rule]]
            status = "INDICATOR_UNAVAILABLE" if unavailable else "FILTER_REJECTED" if failed else "ACCEPTED"
            rows.append(dict(message_id=int(signal.message_id), filter=name, decision=status,
                             unavailable_rules=",".join(unavailable), failed_rules=",".join(failed)))
    return pd.DataFrame(rows)
