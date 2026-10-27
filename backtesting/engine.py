"""Gold Telegram-signal backtester.

Default experiment: hypothetical Telegram boundary entry, ten-point initial SL.
OHLC data cannot establish intrabar order. Such outcomes are AMBIGUOUS and
excluded from completed-trade metrics. Not a live-execution simulator.
"""
from pathlib import Path
from types import SimpleNamespace
import os
import pandas as pd
from backtesting.strategies import STRATEGIES

PRICE_DIR = Path('data/prices/signal_windows')
ENTRY_DELAY_SECONDS = int(os.getenv('ENTRY_DELAY_SECONDS', '5'))
MAX_QUOTE_WAIT_SECONDS = 15
ENTRY_MODE = 'boundary'  # 'boundary' or 'market'
STOP_LOSS_MODE = 'fixed'  # 'fixed' or 'original'
FIXED_STOP_POINTS = 10.0
PRICE_COLS = ['bid_o', 'bid_h', 'bid_l', 'bid_c', 'ask_o', 'ask_h', 'ask_l', 'ask_c']
COMPLETED_STATUSES = {'STOP', 'STOP_GAP', 'TP1', 'BREAKEVEN_STOP', 'FIXED_TARGET'}


def load_prices(path):
    path = Path(path)
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    if df.empty:
        return df
    missing = set(PRICE_COLS + ['time']) - set(df.columns)
    if missing:
        raise ValueError(f'{path}: missing columns {sorted(missing)}')
    df['time'] = pd.to_datetime(df['time'], utc=True)
    for col in PRICE_COLS:
        df[col] = pd.to_numeric(df[col], errors='raise')
    return df.drop_duplicates('time').sort_values('time').reset_index(drop=True)


def get_entry(signal, s5):
    if s5.empty:
        return None, 'NO_ENTRY_DATA'
    direction = str(signal['direction']).upper().strip()
    if direction not in ('BUY', 'SELL'):
        return None, 'INVALID_DIRECTION'
    signal_time = pd.Timestamp(signal['timestamp_utc'])
    signal_time = signal_time.tz_localize('UTC') if signal_time.tzinfo is None else signal_time.tz_convert('UTC')
    assumed_time = signal_time + pd.Timedelta(seconds=ENTRY_DELAY_SECONDS)
    candidates = s5.loc[s5['time'] >= assumed_time]
    if candidates.empty:
        return None, 'NO_ENTRY_QUOTE'
    candle = candidates.iloc[0]
    if (candle['time'] - assumed_time).total_seconds() > MAX_QUOTE_WAIT_SECONDS:
        return None, 'ENTRY_QUOTE_TOO_LATE'
    low, high = float(signal['entry_low']), float(signal['entry_high'])
    if low > high:
        return None, 'INVALID_ENTRY_RANGE'
    boundary = high if direction == 'BUY' else low
    market = float(candle['ask_o'] if direction == 'BUY' else candle['bid_o'])
    if ENTRY_MODE not in ('boundary', 'market'):
        raise ValueError(f'Unknown ENTRY_MODE: {ENTRY_MODE}')
    price = boundary if ENTRY_MODE == 'boundary' else market
    original_sl = float(signal['stop_loss'])
    if STOP_LOSS_MODE == 'fixed':
        if FIXED_STOP_POINTS <= 0:
            raise ValueError('FIXED_STOP_POINTS must be positive')
        sl = price - FIXED_STOP_POINTS if direction == 'BUY' else price + FIXED_STOP_POINTS
    elif STOP_LOSS_MODE == 'original':
        sl = original_sl
    else:
        raise ValueError(f'Unknown STOP_LOSS_MODE: {STOP_LOSS_MODE}')
    tp1 = float(signal['tp1'])
    if not (sl < price < tp1 if direction == 'BUY' else tp1 < price < sl):
        return None, 'INVALID_SL_TP'
    sign = 1 if direction == 'BUY' else -1
    return {
        'price': price, 'time': candle['time'], 'direction': direction,
        'sl': sl, 'original_sl': original_sl, 'tp1': tp1,
        'market_price': market, 'boundary_price': boundary,
        'entry_difference': sign * (market - boundary),
    }, 'ENTERED'


def prepare_timeline(s5, m1, entry_time):
    """Use S5 until the last complete minute, then nonoverlapping M1.

    If S5 ends mid-minute, discard that incomplete minute's S5 tail and
    use the corresponding complete M1 candle only when it begins after entry.
    Gaps are reported by simulate rather than silently interpolated.
    """
    if s5.empty:
        return pd.DataFrame()
    fine = s5.loc[s5['time'] >= entry_time].copy()
    if fine.empty:
        return fine
    fine['duration_seconds'] = 5
    if m1.empty:
        return fine.reset_index(drop=True)
    last_s5_end = fine['time'].max() + pd.Timedelta(seconds=5)
    boundary = last_s5_end.floor('min')
    # Keep S5 only through the last completed minute boundary.
    fine = fine.loc[fine['time'] < boundary].copy()
    if fine.empty:
        # No full minute of S5; use fine data and flag subsequent gaps.
        fine = s5.loc[s5['time'] >= entry_time].copy()
        fine['duration_seconds'] = 5
        boundary = last_s5_end.ceil('min')
    coarse = m1.loc[m1['time'] >= boundary].copy()
    if not coarse.empty:
        coarse['duration_seconds'] = 60
    return pd.concat([fine, coarse], ignore_index=True).sort_values('time').reset_index(drop=True)


def simulate(signal, strategy, s5, m1):
    entry, status = get_entry(signal, s5)
    result = {
        'message_id': int(signal['message_id']),
        'timestamp_utc': signal['timestamp_utc'],
        'direction': signal['direction'], 'strategy': strategy.name,
        'entry_mode': ENTRY_MODE, 'stop_loss_mode': STOP_LOSS_MODE,
        'status': status, 'entry_price': None, 'market_price': None,
        'boundary_price': None, 'entry_difference': None,
        'entry_time': None, 'stop_loss': None,
        'original_stop_loss': float(signal['stop_loss']),
        'initial_risk': None, 'points': None, 'r_multiple': None,
        'partial_hit': False, 'breakeven_activated': False, 'exit_time': None,
        'trade_1_points': None, 'trade_2_points': None,
        'trade_1_exit_price': None, 'trade_2_exit_price': None,
    }
    if entry is None:
        return result
    direction, price, sl, tp1 = entry['direction'], entry['price'], entry['sl'], entry['tp1']
    sign = 1 if direction == 'BUY' else -1
    risk = sign * (price - sl)
    result.update(entry_price=price, market_price=entry['market_price'],
                  boundary_price=entry['boundary_price'], entry_difference=entry['entry_difference'],
                  entry_time=entry['time'], stop_loss=sl, initial_risk=risk)
    if risk <= 0:
        result['status'] = 'INVALID_RISK'
        return result

    fraction = float(strategy.partial_fraction)
    target_distance = float(strategy.partial_points)
    be_trigger = getattr(strategy, 'breakeven_trigger', None)
    if not 0 <= fraction <= 1 or target_distance <= 0:
        raise ValueError(f'Invalid strategy: {strategy.name}')
    if be_trigger is not None and (fraction != 1 or not 0 < be_trigger < target_distance):
        raise ValueError('BE trigger requires a 100% fixed-target strategy and 0 < BE < target')
    is_fixed = fraction == 1.0
    is_partial = 0 < fraction < 1
    fixed_target = price + sign * target_distance
    partial_target = fixed_target
    # Partial strategies require TP1 beyond their first +5 target.
    # Explicitly exclude rather than silently change strategy rules.
    if is_partial and sign * (tp1 - price) <= target_distance:
        result['status'] = 'TP1_BEFORE_PARTIAL_TARGET'
        return result

    timeline = prepare_timeline(s5, m1, entry['time'])
    if timeline.empty:
        result['status'] = 'NO_EXIT_DATA'
        return result
    remaining = 1.0
    realized = 0.0
    active_sl = sl
    partial_done = False
    be_activated = False
    last_end = None

    def finish(exit_status, when, exit_price=None):
        result['status'] = exit_status
        result['exit_time'] = when
        result['partial_hit'] = partial_done
        result['breakeven_activated'] = be_activated
        if exit_status == 'AMBIGUOUS':
            # Do not report an invented profit for unknown intrabar order.
            return result
        total = realized + (remaining * sign * (exit_price - price) if exit_price is not None else 0)
        result['points'] = total
        result['r_multiple'] = total / risk
        if strategy.name == 'two_trades_50':
            # Both legs are 50% of the original total position.
            if partial_done:
                result['trade_1_points'] = 0.5 * target_distance
                result['trade_1_exit_price'] = partial_target
                if exit_price is not None:
                    result['trade_2_points'] = 0.5 * sign * (exit_price - price)
                    result['trade_2_exit_price'] = exit_price
            elif exit_price is not None:
                result['trade_1_points'] = 0.5 * sign * (exit_price - price)
                result['trade_2_points'] = 0.5 * sign * (exit_price - price)
                result['trade_1_exit_price'] = exit_price
                result['trade_2_exit_price'] = exit_price
        return result

    for _, c in timeline.iterrows():
        when = c['time']
        duration = int(c['duration_seconds'])
        if last_end is not None and when != last_end:
            return finish('DATA_GAP', when)
        last_end = when + pd.Timedelta(seconds=duration)
        side = 'bid' if direction == 'BUY' else 'ask'
        opening, high, low = (float(c[f'{side}_{x}']) for x in ('o', 'h', 'l'))
        if low > high or not low <= opening <= high:
            return finish('INVALID_CANDLE', when)

        def stop_open(level):
            return opening <= level if sign == 1 else opening >= level

        def target_open(level):
            return opening >= level if sign == 1 else opening <= level

        def stop_hit(level):
            return low <= level if sign == 1 else high >= level

        def target_hit(level):
            return high >= level if sign == 1 else low <= level

        # The first observed open precedes the candle's unknown high/low order.
        # A boundary fill can already be past its stop/target on the first bar:
        # these are hypothetical executions, not validated market fills.
        if stop_open(active_sl):
            return finish('BREAKEVEN_STOP' if active_sl == price else 'STOP_GAP', when, opening)

        # Always check the strategy's own target, never substitute Telegram TP1
        # for a fixed +5 target.
        target = fixed_target if is_fixed else tp1
        if target_open(target):
            return finish('FIXED_TARGET' if is_fixed else 'TP1', when, target)

        if is_fixed:
            hit_stop = stop_hit(active_sl)
            hit_target = target_hit(fixed_target)
            if be_trigger is None:
                if hit_stop and hit_target:
                    return finish('AMBIGUOUS', when)
                if hit_stop:
                    return finish('STOP', when, active_sl)
                if hit_target:
                    return finish('FIXED_TARGET', when, fixed_target)
                continue

            trigger = price + sign * float(be_trigger)
            hit_trigger = target_hit(trigger)
            if be_activated:
                if hit_stop and hit_target:
                    return finish('AMBIGUOUS', when)
                if hit_stop:
                    return finish('BREAKEVEN_STOP', when, active_sl)
                if hit_target:
                    return finish('FIXED_TARGET', when, fixed_target)
                continue

            if hit_stop and (hit_trigger or hit_target):
                return finish('AMBIGUOUS', when)
            if hit_stop:
                return finish('STOP', when, active_sl)
            if hit_target:
                # Even if +3 triggers first, +5 is reached in the same bar;
                # whether BE was hit between those events is unknown if
                # the candle also traded back through entry.
                if stop_hit(price):
                    return finish('AMBIGUOUS', when)
                return finish('FIXED_TARGET', when, fixed_target)
            if hit_trigger:
                if stop_hit(price):
                    return finish('AMBIGUOUS', when)
                be_activated = True
                active_sl = price
            continue

        if not is_partial:
            if stop_hit(active_sl) and target_hit(tp1):
                return finish('AMBIGUOUS', when)
            if stop_hit(active_sl):
                return finish('STOP', when, active_sl)
            if target_hit(tp1):
                return finish('TP1', when, tp1)
            continue

        if partial_done:
            if stop_hit(active_sl) and target_hit(tp1):
                return finish('AMBIGUOUS', when)
            if stop_hit(active_sl):
                return finish('BREAKEVEN_STOP', when, active_sl)
            if target_hit(tp1):
                return finish('TP1', when, tp1)
            continue

        hit_sl = stop_hit(active_sl)
        hit_partial = target_hit(partial_target)
        hit_tp1 = target_hit(tp1)
        if hit_sl and (hit_partial or hit_tp1):
            return finish('AMBIGUOUS', when)
        if hit_sl:
            return finish('STOP', when, active_sl)
        if hit_tp1:
            # TP1 cannot be reached without first crossing +5; however
            # the remaining portion may have hit BE between those events.
            if stop_hit(price):
                return finish('AMBIGUOUS', when)
            realized += fraction * target_distance
            remaining = 1 - fraction
            partial_done = True
            be_activated = True
            return finish('TP1', when, tp1)
        if hit_partial:
            if stop_hit(price):
                return finish('AMBIGUOUS', when)
            realized += fraction * target_distance
            remaining = 1 - fraction
            partial_done = True
            be_activated = True
            active_sl = price

    result['status'] = 'UNRESOLVED'
    result['partial_hit'] = partial_done
    result['breakeven_activated'] = be_activated
    # Realized partial profit only; not total P/L of an open trade.
    result['points'] = realized
    result['r_multiple'] = realized / risk
    return result


def run_all_strategies(signals):
    results = []
    total = len(signals)
    # Dedicated two-position experiment; equivalent to partial_50, but
    # with separately recorded legs and a fixed 10-point initial stop.
    two_trades = SimpleNamespace(
        name='two_trades_50', partial_fraction=0.5,
        partial_points=5.0, breakeven_trigger=None,
    )
    strategies = [s for s in STRATEGIES if s.name != 'two_trades_50'] + [two_trades]
    for index, (_, signal) in enumerate(signals.iterrows(), start=1):
        message_id = int(signal['message_id'])
        s5 = load_prices(PRICE_DIR / f'signal_{message_id}_S5.csv')
        m1 = load_prices(PRICE_DIR / f'signal_{message_id}_M1.csv')
        for strategy in strategies:
            results.append(simulate(signal, strategy, s5, m1))
        if index % 25 == 0:
            print(f'Processed {index}/{total} signals')
    return pd.DataFrame(results)
