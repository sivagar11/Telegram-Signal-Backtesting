"""Enter inside the range immediately, otherwise wait for its first boundary touch."""
from dataclasses import dataclass

import pandas as pd

from backtesting.midpoint import MidpointVariant, simulate_midpoint, _simulate_filled


@dataclass(frozen=True)
class RangeReturnVariant:
    wait_minutes: int
    target_points: float

    @property
    def name(self):
        return f"range_{self.wait_minutes}m_tp{self.target_points:g}"


VARIANTS = [RangeReturnVariant(minutes, float(points)) for minutes in [3, 5] for points in [3, 4, 5]]


def simulate_range_return(signal, variant, s5, m1):
    if variant.wait_minutes <= 0:
        raise ValueError("Wait must be positive")
    config = MidpointVariant(variant.name, "market", target_points=variant.target_points)
    # Initialize the shared result and validate signal/config without selecting an entry.
    result = simulate_midpoint(signal, config, s5.iloc[:0], m1)
    result.update(entry_mode="range_return", wait_minutes=variant.wait_minutes,
                  target_points=variant.target_points, initial_quote_price=None,
                  initial_quote_inside_range=None, pending_boundary=None, approach=None, entry_source=None)
    if result["status"] != "NO_ENTRY_DATA":
        return result
    timestamp = pd.Timestamp(signal["timestamp_utc"])
    expiry = timestamp + pd.Timedelta(minutes=variant.wait_minutes)
    result["order_expiry_time"] = expiry
    sign = 1 if result["direction"] == "BUY" else -1
    result["boundary_target"] = result["boundary_price"] + sign * variant.target_points
    low, high = result["entry_low"], result["entry_high"]

    def no_entry(status):
        result.update(status=status, entry_status=status)
        return result

    def fill(bar, price, intrabar, source):
        result["entry_source"] = source
        return _simulate_filled(result, config, bar, price, intrabar, s5, m1)

    if s5.empty:
        return result
    candidates = s5[s5.time >= result["entry_ready_time"]]
    if candidates.empty:
        return no_entry("NO_ENTRY_QUOTE")
    first = candidates.iloc[0]
    if (first.time - result["entry_ready_time"]).total_seconds() > 15:
        return no_entry("ENTRY_QUOTE_TOO_LATE")
    side = "ask" if sign == 1 else "bid"
    initial = float(first[f"{side}_o"])
    result.update(activation_time=first.time, checked_quote_time=first.time, checked_quote_price=initial,
                  quote_inside_range=low <= initial <= high,
                  initial_quote_price=initial, initial_quote_inside_range=low <= initial <= high)
    if low <= initial <= high:
        return fill(first, initial, False, "IMMEDIATE_IN_RANGE")
    above = initial > high
    boundary = high if above else low
    result.update(pending_boundary=boundary, approach="ABOVE" if above else "BELOW")
    expected = first.time
    for _, bar in candidates[candidates.time <= expiry].iterrows():
        if bar.time != expected:
            return no_entry("ENTRY_DATA_GAP")
        opening = float(bar[f"{side}_o"])
        if low <= opening <= high:
            return fill(bar, opening, False, "WAIT_OPEN_IN_RANGE")
        if (above and opening < low) or (not above and opening > high):
            # A discontinuous jump across the whole range does not prove a boundary fill.
            return no_entry("AMBIGUOUS_ENTRY_GAP")
        end = bar.time + pd.Timedelta(seconds=5)
        touched = (float(bar[f"{side}_l"]) <= boundary if above
                   else float(bar[f"{side}_h"]) >= boundary)
        if touched and end <= expiry:
            return fill(bar, boundary, True, "WAIT_BOUNDARY_TOUCH")
        if touched and bar.time < expiry < end:
            return no_entry("AMBIGUOUS_ENTRY_DEADLINE")
        if end > expiry:
            return no_entry("NO_FILL")
        expected = end
    return no_entry("ENTRY_WINDOW_INCOMPLETE")
