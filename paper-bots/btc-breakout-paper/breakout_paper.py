#!/usr/bin/env python3
"""Standalone BTC 60/30 PAPER bot. Public GET data only; cannot submit orders."""
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
FEE = .012
SLIP = .0005
LEGACY_FEE = .005
PROTOCOL = dict(mode='PAPER_ONLY', product='BTC-USD', entry_prior_days=60,
                exit_prior_days=30, daily_clock='UTC', account_usd=1000,
                entry_budget_usd=150, fee_each_side=FEE, adverse_slippage=SLIP,
                minimum_daily_candles=286, require_day_first_and_last=True,
                maximum_signal_age_seconds=900)
FINGERPRINT = hashlib.sha256(json.dumps(PROTOCOL, sort_keys=True).encode()).hexdigest()
LEGACY_PROTOCOL = dict(PROTOCOL, fee_each_side=LEGACY_FEE)
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
    request = urllib.request.Request(url, headers={'User-Agent': 'btc-breakout-PAPER/1.0',
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
    valid = (len(rows) >= 286 and rows[0][0] == day and rows[-1][0] == day + 86100)
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
    needs_fetch = rows is None or (not record_from_candles(rows, day)['valid']
                                  and now <= day + 86400 + 900)
    if needs_fetch:
        query = urllib.parse.urlencode(dict(granularity=300, start=iso(day), end=iso(day + 86400)))
        rows = validate_candles(get_json('https://api.exchange.coinbase.com/products/BTC-USD/candles?' + query), day)
        atomic_json(path, rows)
        time.sleep(.25)
    return record_from_candles(rows, day)


def levels(records, day):
    previous = [records.get(day - offset * 86400) for offset in range(1, 61)]
    high = max(r['high'] for r in previous) if all(r and r['valid'] for r in previous) else None
    low = min(r['low'] for r in previous[:30]) if all(r and r['valid'] for r in previous[:30]) else None
    return high, low


def new_state(price, now):
    return dict(version=1, mode='PAPER_ONLY', protocol_hash=FINGERPRINT,
                created_at=iso(now), started_ts=now, cash=1000.0, btc=0.0,
                cost=0.0, fees=0.0, realized_profit=0.0, trades=[],
                benchmark_btc=150 * (1-FEE) / (price * (1+SLIP)),
                last_price=price, last_check=iso(now), last_check_ts=now,
                last_processed_day=None, last_signal=None, last_signal_day=None,
                upper=None, lower=None, last_daily_close=None,
                note='New paper account; waiting for a new UTC daily close.')


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
    migration = state.get('fee_migration')
    legacy_count = len(state['trades']) if _legacy else 0
    if _legacy and migration is not None:
        raise ValueError('Legacy state already contains fee migration metadata')
    if migration is not None:
        if not isinstance(migration, dict):
            raise ValueError('Invalid fee migration metadata')
        count = migration.get('legacy_trade_count')
        if (type(count) is not int or not 0 <= count <= len(state['trades'])
                or migration.get('from_protocol_hash') != LEGACY_FINGERPRINT
                or migration.get('legacy_fee_each_side') != LEGACY_FEE
                or migration.get('new_fee_each_side') != FEE
                or migration.get('benchmark_repriced') is not True):
            raise ValueError('Invalid fee migration metadata')
        legacy_count = count
    # Reconcile recorded trades independently; malformed state never starts over.
    cash, btc, cost, fees, realized = 1000.0, 0.0, 0.0, 0.0, 0.0
    seen = set()
    for index, trade in enumerate(state['trades']):
        trade_rate = LEGACY_FEE if index < legacy_count else FEE
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
            if not math.isclose(fee, amount * trade_rate, abs_tol=1e-8) or not math.isclose(quantity * price, amount - fee, abs_tol=1e-8):
                raise ValueError('Purchase accounting mismatch')
            cash -= amount
            btc = quantity
            cost = amount
        elif trade['side'] == 'SELL':
            if not math.isclose(quantity, btc, abs_tol=1e-12) or not math.isclose(fee, quantity * price * trade_rate, abs_tol=1e-8):
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
def migrate_fee_state(state, now):
    """Validate old ledger; retain balances/history and update future cost policy."""
    if state.get('protocol_hash') == FINGERPRINT:
        validate_state(state)
        return copy.deepcopy(state)
    validate_state(state, _legacy=True)
    updated = copy.deepcopy(state)
    updated['protocol_hash'] = FINGERPRINT
    # Correct the hypothetical hold entry at the original price, without resetting it.
    updated['benchmark_btc'] *= (1-FEE)/(1-LEGACY_FEE)
    updated['fee_migration'] = dict(from_protocol_hash=LEGACY_FINGERPRINT,
                                    legacy_trade_count=len(state['trades']),
                                    legacy_fee_each_side=LEGACY_FEE, new_fee_each_side=FEE,
                                    changed_at=iso(now), benchmark_repriced=True)
    updated['note'] = 'Fee model corrected to 1.2% each way; original account and signals preserved.'
    validate_state(updated)
    return updated


def migrate_fee_file(path):
    if not path.exists():
        raise ValueError('No existing paper state; refusing to initialize or reset an account')
    original = path.read_bytes()
    state = json.loads(original)
    updated = migrate_fee_state(state, time.time())
    if updated == state:
        print('Fee state already at 1.2%; no files changed.', flush=True)
        status(state)
        return
    backup = path.with_name(path.name + '.before-fee-1p2-' + str(time.time_ns()) + '.bak')
    with backup.open('xb') as handle:
        handle.write(original)
        handle.flush()
        os.fsync(handle.fileno())
    atomic_json(path, updated)
    print('Original paper state backed up:', backup, flush=True)
    print('Fee model migrated without resetting the account or changing recorded trades.', flush=True)
    status(updated)


def process_day(state, records, day, price, now):
    """At most one paper decision per completed day; no historical fills."""
    validate_state(state)
    if not math.isfinite(price) or price <= 0 or now < state['last_check_ts']:
        raise ValueError('Invalid price or clock moved backwards')
    if state['last_processed_day'] is not None and day < state['last_processed_day']:
        raise ValueError('Daily clock moved backwards')
    if state['last_processed_day'] == day:
        return None
    record = records[day]
    high, low = levels(records, day)
    state.update(upper=high, lower=low, last_daily_close=record['close'])
    close_time = day + 86400
    age = now - close_time
    if age < 0:
        raise ValueError('Attempt to process an unfinished day')
    if not record['valid'] and age <= 900:
        state['note'] = 'Latest daily data incomplete; retrying within freshness window.'
        return None
    state['last_processed_day'] = day
    if not record['valid']:
        state['note'] = 'Daily data incomplete; signal skipped.'
        return None
    if close_time < state['started_ts'] or age > 900:
        state['note'] = 'Historical or stale daily signal skipped; waiting for the next UTC close.'
        return None
    side = None
    if state['btc'] == 0 and high is not None and record['close'] > high:
        side = 'BUY'
    elif state['btc'] > 0 and low is not None and record['close'] < low:
        side = 'SELL'
    state['last_signal_day'] = day
    state['last_signal'] = side or 'WAIT'
    if side is None:
        state['note'] = 'No daily entry/exit signal.' if (high is not None or state['btc'] > 0) else 'Waiting for 60 valid previous daily candles.'
        return None
    fill = price * ((1+SLIP) if side == 'BUY' else (1-SLIP))
    if side == 'BUY':
        amount = min(150.0, state['cash'])
        if amount <= 1e-8:
            state['note'] = 'No simulated cash available.'
            return None
        fee = amount * FEE
        quantity = (amount - fee) / fill
        state['cash'] -= amount
        state['btc'] = quantity
        state['cost'] = amount
        profit = None
    else:
        quantity = state['btc']
        gross = quantity * fill
        fee = gross * FEE
        amount = gross - fee
        profit = amount - state['cost']
        state['cash'] += amount
        state['btc'] = state['cost'] = 0.0
        state['realized_profit'] += profit
    state['fees'] += fee
    trade = dict(id=f'{day}:{side}', side=side, signal_day=day,
                 time=iso(now), observed_spot=price, fill=fill, cash_amount=amount,
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
    print('PAPER ONLY | BTC daily breakout 60/30 | simulated USD', flush=True)
    print(f'Fees {FEE:.1%} each way | assumed slippage {SLIP:.2%} each way', flush=True)
    if state.get('fee_migration', {}).get('legacy_trade_count', 0):
        print('Earlier recorded trades retain their original 0.5% fees; hold benchmark repriced at 1.2%.', flush=True)
    print('Last observation:', state['last_check'], flush=True)
    print(f"Account ${equity:.2f} | since start ${equity-1000:+.2f} | same-budget hold ${hold:.2f}", flush=True)
    print(f"Realized ${state['realized_profit']:+.2f} | open ${net_btc-state['cost']:+.2f} | fees ${state['fees']:.2f}", flush=True)
    print(f"{buys} buys / {sells} sells | entry cost ${state['cost']:.2f}/$150.00", flush=True)
    upper = f"${state['upper']:,.2f}" if state['upper'] is not None else 'not ready'
    lower = f"${state['lower']:,.2f}" if state['lower'] is not None else 'not ready'
    print(f"Daily close must exceed prior 60-day high {upper} to buy; fall below prior 30-day low {lower} to sell.", flush=True)
    print(state['note'], flush=True)


def poll(state):
    now = time.time()
    price = spot_price()
    if state is None:
        state = new_state(price, now)
    if now < state['last_check_ts']:
        raise ValueError('Clock moved backwards')
    day = int(now // 86400) * 86400 - 86400
    if state['last_processed_day'] != day and now >= day + 86400 + 20:
        print('Loading completed daily history for the fixed 60/30 rules...', flush=True)
        records = {}
        for offset in range(60, -1, -1):
            stamp = day - offset * 86400
            records[stamp] = day_record(stamp, now)
        # Refresh both clock and price after a possibly slow bootstrap.
        price = spot_price()
        now = time.time()
        process_day(state, records, day, price, now)
    state.update(last_price=price, last_check=iso(now), last_check_ts=now)
    atomic_json(STATE, state)
    status(state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--migrate-fees', action='store_true', help='Offline migration of old fee state; creates backup')
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
        if args.migrate_fees:
            migrate_fee_file(STATE)
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
