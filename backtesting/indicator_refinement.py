"""Experiment 1.1 features: support rejection and gap-aware warm-up."""
import numpy as np
import pandas as pd

from backtesting.indicators import (FILTERS, SECONDS, atr, build_signal_features,
                                    candle_features)

REFINED_FILTERS = {**FILTERS,
    "standalone_sr_touch": ("sr_touch",),
    "B_touch_trend": ("trend", "sr_touch"),
    "E_touch_confirmation": ("trend", "sr_touch", "rsi", "candle"),
    "F_touch_confirmation_atr": ("trend", "sr_touch", "rsi", "candle", "atr"),
}


def support_rejection(frame):
    """A directional rejection within the last three fully closed M15 candles.

    Each candidate uses levels/ATR frozen BEFORE that candidate candle, so a
    later lower low cannot retrospectively redefine its support.
    """
    support = frame.bid_l.shift().rolling(20, min_periods=20).min()
    resistance = frame.bid_h.shift().rolling(20, min_periods=20).max()
    volatility = atr(frame).shift()
    span = frame.bid_h - frame.bid_l
    midpoint = (frame.bid_h + frame.bid_l) / 2
    buy = ((frame.bid_l - support).abs().le(.25 * volatility)
           & (frame.bid_c >= support + .25 * volatility)
           & (frame.bid_c > frame.bid_o) & (frame.bid_c >= midpoint) & (span > 0))
    sell = ((frame.bid_h - resistance).abs().le(.25 * volatility)
            & (frame.bid_c <= resistance - .25 * volatility)
            & (frame.bid_c < frame.bid_o) & (frame.bid_c <= midpoint) & (span > 0))
    ready = support.notna() & resistance.notna() & volatility.gt(0)
    return pd.DataFrame({"sr_touch_buy": buy.rolling(3, min_periods=3).max().eq(1),
                         "sr_touch_sell": sell.rolling(3, min_periods=3).max().eq(1),
                         "sr_touch_buy_time": (frame.time + pd.Timedelta(minutes=15)).where(buy).ffill(limit=2),
                         "sr_touch_sell_time": (frame.time + pd.Timedelta(minutes=15)).where(sell).ffill(limit=2),
                         "sr_touch_context_ready": ready.rolling(3, min_periods=3).min().eq(1)})


def refined_candle_features(history, timeframe):
    frames = []
    reset = history.get("reset_before", pd.Series(False, index=history.index)).fillna(False)
    for _, segment in history.groupby(reset.astype(int).cumsum(), sort=False):
        segment = segment.reset_index(drop=True)
        features = candle_features(segment, timeframe)
        if timeframe == "M15":
            features = pd.concat([features, support_rejection(segment)], axis=1)
        frames.append(features)
    return pd.concat(frames, ignore_index=True)


def build_refined_features(signals, histories, gaps, latency_seconds=2):
    features = build_signal_features(signals, histories, publication_latency_seconds=latency_seconds,
                                     feature_builder=refined_candle_features)
    features["sr_touch"] = np.where(features.direction.eq("BUY"), features.sr_touch_buy, features.sr_touch_sell)
    event_time = features.sr_touch_buy_time.where(features.direction.eq("BUY"), features.sr_touch_sell_time)
    features["sr_touch_age_seconds"] = (features.timestamp_utc - event_time).dt.total_seconds()
    features["sr_touch"] &= features.sr_touch_age_seconds.between(0, 45 * 60)
    features["sr_touch_ready"] = features.sr_touch_context_ready.fillna(False) & features.M15_fresh
    rule_timeframes = {"trend": "H1", "sr": "M15", "sr_touch": "M15", "rsi": "M5", "candle": "M5", "atr": "M5"}
    for tf, seconds in SECONDS.items():
        blocked = pd.Series(False, index=features.index)
        for _, gap in gaps[gaps.kind.eq("UNEXPECTED_GAP") & gaps.timeframe.eq(tf)].iterrows():
            start = pd.Timestamp(gap.start_utc) + pd.Timedelta(seconds=seconds + latency_seconds)
            recovery = histories[tf].loc[histories[tf].time >= pd.Timestamp(gap.end_utc), "time"]
            stop = (recovery.iloc[0] + pd.Timedelta(seconds=seconds + latency_seconds)
                    if len(recovery) else pd.Timestamp.max.tz_localize("UTC"))
            blocked |= features.timestamp_utc.ge(start) & features.timestamp_utc.lt(stop)
        features[f"{tf}_gap_active"] = blocked
        for rule, required_tf in rule_timeframes.items():
            if required_tf == tf:
                features[f"{rule}_ready"] &= ~blocked
    return features
