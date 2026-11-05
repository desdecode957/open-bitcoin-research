#!/usr/bin/env python3
"""Weekly BTC / 200-day SMA PAPER bot. Public price GETs only; no order capability."""
import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE = BASE / 'paper_state.json'
FEE, SLIP = .012, .0005
PROTOCOL = dict(mode='PAPER_ONLY', strategy='WEEKLY_SMA200', product='BTC-USD',
                average_completed_days=200, include_signal_day=True,
                signal_day='Sunday UTC', daily_clock='UTC', account_usd=1000,
                entry_budget_usd=150, fee_each_side=FEE, adverse_slippage=SLIP,
                minimum_daily_candles=1, require_day_first_and_last=False, require_day_last=True,
                maximum_signal_age_seconds=900, intraday_stop=False)

FINGERPRINT = hashlib.sha256(json.dumps(PROTOCOL, sort_keys=True).encode()).hexdigest()
LEGACY_PROTOCOL = dict(PROTOCOL, minimum_daily_candles=286, require_day_first_and_last=True)
LEGACY_PROTOCOL.pop('require_day_last')
LEGACY_FINGERPRINT = hashlib.sha256(json.dumps(LEGACY_PROTOCOL, sort_keys=True).encode()).hexdigest()


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def get_json(url):
    # The only network capability in this program: public price-data GETs.
    request = urllib.request.Request(url, headers={'User-Agent': 'btc-weekly-trend-PAPER/1.0',
                                                 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def spot_price():
    price = float(get_json('https://api.coinbase.com/v2/prices/BTC-USD/spot')['data']['amount'])
    if not math.isfinite(price) or price <= 0:
        raise ValueError('Invalid spot price')
    return price


def validate_candles(payload, day):
    if not isinstance(payload, list):
        raise ValueError('Unexpected candle response')
    candles = {}
    for row in payload:
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError('Malformed candle')
        stamp = float(row[0])
        if not math.isfinite(stamp) or not stamp.is_integer() or int(stamp) % 300:
            raise ValueError('Invalid candle timestamp')
        stamp = int(stamp)
        values = list(map(float, row[1:6]))
        low, high, opening, close, volume = values
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Nonfinite candle')
        if low <= 0 or not low <= min(opening, close) <= max(opening, close) <= high or volume < 0:
            raise ValueError('Invalid candle values')
        if day <= stamp < day + 86400:
            parsed = [stamp, *values]
            if stamp in candles and candles[stamp] != parsed:
                raise ValueError('Conflicting candles')
            candles[stamp] = parsed
    return [candles[t] for t in sorted(candles)]


def record_from_candles(rows, day):
    # SMA uses the exact closing observation, not the day's high/low or opening.
    # Missing earlier bars do not change the close; a missing closing bar is rejected.
    valid = bool(rows and rows[-1][0] == day + 86100)
    return dict(day=day, valid=bool(valid), candles=len(rows),
                high=max((r[2] for r in rows), default=None),
                low=min((r[1] for r in rows), default=None),
                close=rows[-1][4] if rows else None)


def day_record(day, now):
    if day % 86400 or now < day + 86400 + 20:
        raise ValueError('Daily candle is not settled yet')
    cache = BASE / 'candle-cache'
    cache.mkdir(exist_ok=True)
    path = cache / f'BTC-USD-5m-{day}.json'
    rows = validate_candles(json.loads(path.read_text()), day) if path.exists() else None
    shared = BASE.parent / 'btc-breakout-paper' / 'candle-cache' / path.name
    if rows is None and shared.exists():
        candidate = validate_candles(json.loads(shared.read_text()), day)
        if record_from_candles(candidate, day)['valid']:
            rows = candidate
            atomic_json(path, rows)
    needs_fetch = rows is None or (not record_from_candles(rows, day)['valid']
                                  and now <= day + 86400 + 900)
    if needs_fetch:
        query = urllib.parse.urlencode(dict(granularity=300, start=iso(day), end=iso(day + 86400)))
        rows = validate_candles(get_json('https://api.exchange.coinbase.com/products/BTC-USD/candles?' + query), day)
        atomic_json(path, rows)
        time.sleep(.25)
    return record_from_candles(rows, day)


def signal_week(now):
    """Most recent fully completed Sunday UTC; never uses today's unfinished day."""
    day = int(now // 86400) * 86400 - 86400
    weekday = datetime.fromtimestamp(day, timezone.utc).weekday()
    return day - ((weekday-6) % 7) * 86400


def average(records, week):
    previous = [records.get(week-offset*86400) for offset in range(200)]
    if not all(r and r['valid'] for r in previous):
        return None
    return math.fsum(r['close'] for r in previous) / 200


def signal(close, moving_average, has_position):
    if moving_average is None:
        return None
    if close > moving_average and not has_position:
        return 'BUY'
    if close < moving_average and has_position:
        return 'SELL'
    return None


def new_state(price, now):
    return dict(version=1, mode='PAPER_ONLY', protocol_hash=FINGERPRINT,
                created_at=iso(now), started_ts=now, cash=1000.0, btc=0.0,
                cost=0.0, fees=0.0, realized_profit=0.0, trades=[],
                benchmark_btc=150 * (1-FEE) / (price * (1+SLIP)),
                last_price=price, last_check=iso(now), last_check_ts=now,
                last_processed_week=None, last_signal=None, last_signal_week=None,
                average=None, last_weekly_close=None,
                note='New paper account; waiting for the next Sunday UTC close.')


def validate_state(state, *, _legacy=False):
    expected_hash = LEGACY_FINGERPRINT if _legacy else FINGERPRINT
    if state.get('version') != 1 or state.get('mode') != 'PAPER_ONLY' or state.get('protocol_hash') != expected_hash:
        raise ValueError('State mode/rules differ; refusing to reset or adopt it')
    for key in ('cash', 'btc', 'cost', 'fees', 'realized_profit', 'benchmark_btc',
                'last_price', 'started_ts', 'last_check_ts'):
        if not isinstance(state.get(key), (int, float)) or not math.isfinite(state[key]):
            raise ValueError('Invalid state field: ' + key)
    if min(state['cash'], state['btc'], state['cost'], state['fees'], state['benchmark_btc']) < -1e-8:
        raise ValueError('Negative account balance')
    if state['cost'] > 150 + 1e-8 or state['last_price'] <= 0:
        raise ValueError('Invalid entry cost or price')
    if (state['btc'] == 0) != (state['cost'] == 0):
        raise ValueError('Position and purchase cost disagree')
    if not isinstance(state.get('trades'), list):
        raise ValueError('Missing paper trade ledger')
    # Reconcile recorded trades independently; malformed state never starts over.
    cash, btc, cost, fees, realized = 1000.0, 0.0, 0.0, 0.0, 0.0
    seen = set()
    for trade in state['trades']:
        if trade['id'] in seen:
            raise ValueError('Duplicate paper trade')
        seen.add(trade['id'])
        price, amount, quantity, fee = (trade[k] for k in ('fill', 'cash_amount', 'btc', 'fee'))
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0
                   for v in (price, amount, quantity, fee)):
            raise ValueError('Invalid recorded trade')
        if trade['side'] == 'BUY':
            if btc != 0 or amount > cash + 1e-8 or amount > 150 + 1e-8:
                raise ValueError('Invalid recorded purchase')
            if not math.isclose(fee, amount * FEE, abs_tol=1e-8) or not math.isclose(quantity * price, amount - fee, abs_tol=1e-8):
                raise ValueError('Purchase accounting mismatch')
            cash -= amount
            btc = quantity
            cost = amount
        elif trade['side'] == 'SELL':
            if not math.isclose(quantity, btc, abs_tol=1e-12) or not math.isclose(fee, quantity * price * FEE, abs_tol=1e-8):
                raise ValueError('Sale accounting mismatch')
            if not math.isclose(amount, quantity * price - fee, abs_tol=1e-8):
                raise ValueError('Sale proceeds mismatch')
            cash += amount
            realized += amount - cost
            btc = cost = 0.0
        else:
            raise ValueError('Unknown paper side')
        fees += fee
    for key, expected in dict(cash=cash, btc=btc, cost=cost, fees=fees, realized_profit=realized).items():
        if not math.isclose(state[key], expected, abs_tol=1e-8):
            raise ValueError('State ledger mismatch: ' + key)



def migrate_close_policy(state, now):
    """Explicit data-validity correction; no account reset or trade replay."""
    if state.get('protocol_hash') == FINGERPRINT:
        validate_state(state)
        return copy.deepcopy(state)
    validate_state(state, _legacy=True)
    updated = copy.deepcopy(state)
    updated['protocol_hash'] = FINGERPRINT
    updated['close_policy_migration'] = dict(from_protocol_hash=LEGACY_FINGERPRINT,
                                              changed_at=iso(now))
    # Refresh displayed indicators without replaying the already processed week.
    updated['refresh_average'] = True
    updated['note'] = 'Daily close validation corrected; account and processed signals preserved.'
    validate_state(updated)
    return updated


def migrate_close_file(path):
    if not path.exists():
        raise ValueError('Existing paper state required; refusing to initialize a new account')
    raw = path.read_bytes()
    state = json.loads(raw)
    updated = migrate_close_policy(state, time.time())
    if updated == state:
        print('Closing-price policy already current; no state changes.', flush=True)
        return
    backup = path.with_name(path.name+'.before-close-policy-'+str(time.time_ns())+'.bak')
    with backup.open('xb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    atomic_json(path, updated)
    print('Original state backup:', backup, flush=True)
    print('Closing-price policy corrected; balances, history and weekly decisions preserved.', flush=True)


def process_week(state, records, week, price, now):
    """One fresh decision after a completed Sunday; no replayed historical fills."""
    validate_state(state)
    if not math.isfinite(price) or price <= 0 or now < state['last_check_ts']:
        raise ValueError('Invalid price or clock moved backwards')
    if week % 86400 or datetime.fromtimestamp(week, timezone.utc).weekday() != 6:
        raise ValueError('Signal day must be Sunday UTC')
    if state['last_processed_week'] is not None and week < state['last_processed_week']:
        raise ValueError('Weekly clock moved backwards')
    if state['last_processed_week'] == week:
        return None
    record = records[week]
    ma = average(records, week)
    state.update(average=ma, last_weekly_close=record['close'])
    close_time = week + 86400
    age = now-close_time
    if age < 0:
        raise ValueError('Attempt to process an unfinished week')
    if (not record['valid'] or ma is None) and age <= 900:
        state['note'] = 'Weekly signal data incomplete; retrying within freshness window.'
        return None
    state['last_processed_week'] = week
    if not record['valid'] or ma is None:
        state['note'] = 'Need 200 consecutive valid daily closes; weekly signal skipped.'
        return None
    if close_time < state['started_ts'] or age > 900:
        state['note'] = 'Historical or stale weekly signal skipped; waiting for next Sunday UTC close.'
        return None
    side = signal(record['close'], ma, state['btc'] > 0)
    state['last_signal_week'] = week
    state['last_signal'] = side or 'WAIT'
    if side is None:
        state['note'] = 'No weekly position change.'
        return None
    fill = price * ((1+SLIP) if side == 'BUY' else (1-SLIP))
    if side == 'BUY':
        amount = min(150., state['cash'])
        if amount <= 1e-8:
            state['note'] = 'No simulated cash available.'
            return None
        fee = amount * FEE
        quantity = (amount-fee) / fill
        state['cash'] -= amount
        state['btc'], state['cost'] = quantity, amount
        profit = None
    else:
        quantity = state['btc']
        gross = quantity * fill
        fee = gross * FEE
        amount = gross-fee
        profit = amount-state['cost']
        state['cash'] += amount
        state['btc'] = state['cost'] = 0.
        state['realized_profit'] += profit
    state['fees'] += fee
    trade = dict(id=f'{week}:{side}', side=side, signal_week=week, time=iso(now),
                 observed_spot=price, fill=fill, cash_amount=amount,
                 btc=quantity, fee=fee, profit=profit)
    state['trades'].append(trade)
    state['note'] = 'PAPER ' + side + ' recorded; no actual order sent.'
    validate_state(state)
    return trade


def status(state):
    validate_state(state)
    price = state['last_price']
    net_btc = state['btc'] * price * (1-SLIP) * (1-FEE)
    equity = state['cash'] + net_btc
    hold = 850 + state['benchmark_btc'] * price * (1-SLIP) * (1-FEE)
    buys = sum(t['side'] == 'BUY' for t in state['trades'])
    sells = sum(t['side'] == 'SELL' for t in state['trades'])
    print('PAPER ONLY | BTC weekly trend / 200-day average | simulated USD', flush=True)
    print(f'Fees {FEE:.1%} each way | assumed slippage {SLIP:.2%} each way', flush=True)
    print('Last observation:', state['last_check'], flush=True)
    print(f"Account ${equity:.2f} | since start ${equity-1000:+.2f} | same-budget hold ${hold:.2f}", flush=True)
    print(f"Realized ${state['realized_profit']:+.2f} | open ${net_btc-state['cost']:+.2f} | fees ${state['fees']:.2f}", flush=True)
    print(f"{buys} buys / {sells} sells | entry cost ${state['cost']:.2f}/$150.00", flush=True)
    ma = f"${state['average']:,.2f}" if state['average'] is not None else 'not ready'
    close = f"${state['last_weekly_close']:,.2f}" if state['last_weekly_close'] is not None else 'not ready'
    print(f'Last Sunday UTC close {close} | 200-day average {ma}', flush=True)
    print('Sunday close above average: hold BTC; below: hold cash. No intraday stop.', flush=True)
    print(state['note'], flush=True)


def poll(state):
    now = time.time()
    price = spot_price()
    if state is None:
        state = new_state(price, now)
    if now < state['last_check_ts']:
        raise ValueError('Clock moved backwards')
    week = signal_week(now)
    if (state['last_processed_week'] != week or state.get('refresh_average', False)) and now >= week + 86400 + 20:
        print('Loading 200 completed daily closes for the weekly trend rule...', flush=True)
        records = {}
        for index, offset in enumerate(range(199, -1, -1), 1):
            day = week - offset*86400
            records[day] = day_record(day, now)
            if index == 1 or index % 25 == 0:
                print(f'Daily history: {index}/200', flush=True)
        price = spot_price()
        now = time.time()
        if state.get('refresh_average', False):
            state.update(average=average(records, week), last_weekly_close=records[week]['close'],
                         refresh_average=False)
            state['note'] = ('Closing-price average refreshed; waiting for next Sunday UTC close.'
                             if state['average'] is not None else
                             'A required daily closing candle is missing; weekly average unavailable.')
        process_week(state, records, week, price, now)
    state.update(last_price=price, last_check=iso(now), last_check_ts=now)
    atomic_json(STATE, state)
    status(state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--migrate-close-policy', action='store_true', help='Offline state migration with backup')
    action.add_argument('--status', action='store_true', help='Read saved paper status; no network')
    action.add_argument('--once', action='store_true', help='One paper-only update, then exit')
    args = parser.parse_args()
    if args.status:
        if not STATE.exists():
            print('No paper state yet; run --once or start the paper service.')
        else:
            status(json.loads(STATE.read_text()))
        return
    with (BASE / 'paper.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.migrate_close_policy:
            migrate_close_file(STATE)
            return
        state = json.loads(STATE.read_text()) if STATE.exists() else None
        if state is not None:
            validate_state(state)
        while True:
            try:
                state = poll(state)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                # Integrity problems stop rather than resetting a paper account.
                raise
            except Exception as error:
                if args.once:
                    raise
                print(f'Price/data update failed: {type(error).__name__}: {error}', flush=True)
                # Reload the last committed state after a failed update.
                state = json.loads(STATE.read_text()) if STATE.exists() else None
                if state is not None:
                    validate_state(state)
            if args.once:
                return
            time.sleep(60)


if __name__ == '__main__':
    main()
