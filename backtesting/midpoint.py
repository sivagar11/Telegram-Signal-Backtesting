"""Preset midpoint limit experiments with explicit OHLC entry uncertainty."""
from dataclasses import dataclass
import math

import pandas as pd

from backtesting.engine import prepare_timeline


@dataclass(frozen=True)
class MidpointVariant:
    name: str
    entry_mode: str
    wait_minutes: int = 0
    target_from_entry: bool = False
    entry_delay_seconds: int = 5
    require_in_range: bool = False
    target_points: float = 5.0
    fixed_stop_points: float | None = None


VARIANTS = [
    MidpointVariant("A_boundary_reference", "boundary"),
    MidpointVariant("A_market_reference", "market"),
    MidpointVariant("B_midpoint_3m_boundary_target", "limit", 3),
    MidpointVariant("C_midpoint_5m_boundary_target", "limit", 5),
    MidpointVariant("D_midpoint_3m_entry_target", "limit", 3, True),
]


def requests_for_midpoint(signal):
    timestamp = pd.Timestamp(signal.timestamp_utc)
    return {"S5": (timestamp.floor("5s"), (timestamp + pd.Timedelta(minutes=8)).ceil("min")),
            "M1": (timestamp.floor("min"), (timestamp + pd.Timedelta(hours=5, minutes=6)).ceil("min"))}


def simulate_midpoint(signal, variant, s5, m1):
    timestamp = pd.Timestamp(signal["timestamp_utc"])
    direction = str(signal["direction"]).strip().upper()
    sign = 1 if direction == "BUY" else -1
    low, high, sl = (float(signal[key]) for key in ("entry_low", "entry_high", "stop_loss"))
    midpoint = (low + high) / 2
    boundary = high if sign == 1 else low
    if variant.entry_delay_seconds < 0 or (variant.require_in_range and variant.entry_mode != "market"):
        raise ValueError("Invalid entry delay or range-check mode")
    if not math.isfinite(variant.target_points) or variant.target_points <= 0:
        raise ValueError("Target points must be finite and positive")
    if variant.fixed_stop_points is not None and (not math.isfinite(variant.fixed_stop_points) or variant.fixed_stop_points <= 0):
        raise ValueError("Fixed stop points must be finite and positive")
    ready = timestamp + pd.Timedelta(seconds=variant.entry_delay_seconds)
    expiry = timestamp + pd.Timedelta(minutes=variant.wait_minutes) if variant.entry_mode == "limit" else pd.NaT
    result = dict(message_id=int(signal["message_id"]), timestamp_utc=timestamp, direction=direction,
        strategy=variant.name, variant=variant.name, entry_mode=variant.entry_mode,
        hypothetical=variant.entry_mode == "boundary",
        stop_loss_mode="original" if variant.fixed_stop_points is None else "fixed", stop_loss=sl,
        original_stop_loss=sl,
        entry_low=low, entry_high=high, midpoint=midpoint, boundary_price=boundary,
        boundary_target=boundary + sign * 5, entry_ready_time=ready, order_expiry_time=expiry,
        activation_time=pd.NaT, status="NO_ENTRY_DATA", entry_status="NO_ENTRY_DATA",
        checked_quote_time=pd.NaT, checked_quote_price=None, quote_inside_range=None,
        entered=False, entry_price=None, entry_time=pd.NaT, fill_time_upper=pd.NaT,
        fill_kind=None, initial_risk=None, target_price=None, nominal_reward=None,
        nominal_reward_risk=None, wait_seconds=None, wait_seconds_upper=None,
        midpoint_price_improvement=None, exit_time=pd.NaT, exit_price=None, points=None, r_multiple=None)
    if direction not in ("BUY", "SELL"):
        result.update(status="INVALID_DIRECTION", entry_status="INVALID_DIRECTION")
        return result
    if (not all(math.isfinite(v) for v in (low, high, sl)) or low > high
            or not (sl < low if sign == 1 else sl > high)):
        result.update(status="INVALID_SIGNAL", entry_status="INVALID_SIGNAL")
        return result

    def no_entry(status):
        result.update(status=status, entry_status=status)
        return result

    if s5.empty:
        return result
    candidates = s5.loc[s5.time >= ready]
    if candidates.empty:
        return no_entry("NO_ENTRY_QUOTE")
    first = candidates.iloc[0]
    if (first.time - ready).total_seconds() > 15:
        return no_entry("ENTRY_QUOTE_TOO_LATE")
    result["activation_time"] = first.time
    if variant.entry_mode != "limit":
        fill = boundary if variant.entry_mode == "boundary" else float(first.ask_o if sign == 1 else first.bid_o)
        result.update(checked_quote_time=first.time, checked_quote_price=fill,
                      quote_inside_range=low <= fill <= high)
        if variant.require_in_range and not result["quote_inside_range"]:
            return no_entry("OUTSIDE_ENTRY_RANGE")
        return _simulate_filled(result, variant, first, fill, False, s5, m1)

    expected = first.time
    side = "ask" if sign == 1 else "bid"
    for _, bar in candidates.loc[candidates.time <= expiry].iterrows():
        if bar.time != expected:
            return no_entry("ENTRY_DATA_GAP")
        opening = float(bar[f"{side}_o"])
        if (opening <= midpoint if sign == 1 else opening >= midpoint):
            return _simulate_filled(result, variant, bar, opening, False, s5, m1)
        end = bar.time + pd.Timedelta(seconds=5)
        touched = float(bar[f"{side}_l"]) <= midpoint if sign == 1 else float(bar[f"{side}_h"]) >= midpoint
        if touched and end <= expiry:
            return _simulate_filled(result, variant, bar, midpoint, True, s5, m1)
        if touched and bar.time < expiry < end:
            return no_entry("AMBIGUOUS_ENTRY_DEADLINE")
        if end > expiry:
            return no_entry("NO_FILL")
        expected = end
    return no_entry("ENTRY_WINDOW_INCOMPLETE")


def _simulate_filled(result, variant, fill_bar, fill, intrabar, s5, m1):
    sign = 1 if result["direction"] == "BUY" else -1
    sl = result["stop_loss"] if variant.fixed_stop_points is None else fill - sign * variant.fixed_stop_points
    result["stop_loss"] = sl
    target = fill + sign * variant.target_points if variant.target_from_entry else result["boundary_target"]
    if not (sl < fill < target if sign == 1 else target < fill < sl):
        result.update(status="INVALID_SL_TP", entry_status="INVALID_SL_TP")
        return result
    start = fill_bar.time
    upper = start + pd.Timedelta(seconds=5) if intrabar else start
    risk, reward = sign * (fill - sl), sign * (target - fill)
    result.update(entered=True, entry_status="FILLED", entry_price=fill, entry_time=start,
        fill_time_upper=upper, fill_kind="INTRABAR_LIMIT" if intrabar else "OPEN",
        initial_risk=risk, target_price=target, nominal_reward=reward, nominal_reward_risk=reward/risk,
        wait_seconds=(start - result["timestamp_utc"]).total_seconds(),
        wait_seconds_upper=(upper - result["timestamp_utc"]).total_seconds(),
        midpoint_price_improvement=sign * (result["midpoint"] - fill) if variant.entry_mode == "limit" else None)
    side = "bid" if sign == 1 else "ask"

    def hits(bar):
        lo, hi = float(bar[f"{side}_l"]), float(bar[f"{side}_h"])
        return (lo <= sl, hi >= target) if sign == 1 else (hi >= sl, lo <= target)

    if intrabar and any(hits(fill_bar)):
        result.update(status="AMBIGUOUS_ENTRY_EXIT", exit_time=start)
        return result
    horizon = start + pd.Timedelta(hours=5)
    fine = s5.loc[s5.time + pd.Timedelta(seconds=5) <= horizon]
    coarse = m1 if m1.empty else m1.loc[m1.time + pd.Timedelta(minutes=1) <= horizon]
    timeline = prepare_timeline(fine, coarse, start)
    expected = upper
    timeline = timeline.loc[timeline.time >= expected] if not timeline.empty else timeline
    if timeline.empty:
        result["status"] = "NO_EXIT_DATA"
        return result

    def finish(status, when, price=None):
        result.update(status=status, exit_time=when, exit_price=price)
        if price is not None:
            points = sign * (price - fill)
            result.update(points=points, r_multiple=points/risk)
        return result

    for _, bar in timeline.iterrows():
        when = bar.time
        if when != expected:
            return finish("DATA_GAP", when)
        expected = when + pd.Timedelta(seconds=int(bar.duration_seconds))
        opening = float(bar[f"{side}_o"])
        if (opening <= sl if sign == 1 else opening >= sl):
            return finish("STOP_GAP", when, opening)
        if (opening >= target if sign == 1 else opening <= target):
            return finish("FIXED_TARGET", when, target)
        stop, profit = hits(bar)
        if stop and profit:
            return finish("AMBIGUOUS", when)
        if stop:
            return finish("STOP", when, sl)
        if profit:
            return finish("FIXED_TARGET", when, target)
    result["status"] = "UNRESOLVED"
    return result
