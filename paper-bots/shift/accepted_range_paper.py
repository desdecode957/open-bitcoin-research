#!/usr/bin/env python3
"""PAPER ONLY: sustained hourly-price-average shift experiment; no keys/orders."""
import argparse
import copy
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import time
import urllib.request

BASE = Path(__file__).resolve().parent
STATE = BASE / 'accepted_range_state.json'
CONFIG = dict(start_cash=1000.0, buy_size=50.0, max_cost=150.0, max_buys=3,
              buy_drop=0.01, target_net=2.0, fee=0.012, slippage=0.0005,
              lookback=3600, shift_fraction=0.01, confirmation=1800,
              interval=180, max_gap=600, average='time_weighted_previous_quote')


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def initial(price):
    return dict(version=1, config=CONFIG.copy(), anchor=price, initialized=False,
                cash=CONFIG['start_cash'], btc=0.0, cost=0.0, open_buys=0,
                buys=0, sells=0, fees=0.0, realized=0.0, shifts=0,
                last_ts=0.0, last_price=price, samples=[], average=None,
                candidate_direction=0, candidate_since=None,
                benchmark_btc=CONFIG['start_cash'] / price, trades=[], events=[])


def validate(s):
    if s['version'] != 1 or s['config'] != CONFIG:
        raise ValueError('State/settings mismatch; refusing to reset balances')
    for key in ('anchor', 'cash', 'btc', 'cost', 'fees', 'realized',
                'last_ts', 'last_price', 'benchmark_btc'):
        if not math.isfinite(s[key]) or s[key] < 0:
            raise ValueError('Invalid state field: ' + key)
    if s['anchor'] <= 0 or s['last_price'] <= 0:
        raise ValueError('Nonpositive price/reference')
    for key in ('open_buys', 'buys', 'sells', 'shifts'):
        if type(s[key]) is not int or s[key] < 0:
            raise ValueError('Invalid counter: ' + key)
    if s['open_buys'] > CONFIG['max_buys']:
        raise ValueError('Too many open buys')
    if abs(s['cost'] - s['open_buys'] * CONFIG['buy_size']) > 1e-7:
        raise ValueError('Position cost mismatch')
    if (s['btc'] == 0) != (s['open_buys'] == 0):
        raise ValueError('Position quantity mismatch')
    if abs(s['cash'] + s['cost'] - CONFIG['start_cash'] - s['realized']) > 1e-6:
        raise ValueError('Cash accounting mismatch')
    previous = -1
    for t, p in s['samples']:
        if not math.isfinite(t) or not math.isfinite(p) or t <= previous or p <= 0:
            raise ValueError('Invalid sample history')
        previous = t
    if s['samples'] and s['samples'][-1][0] != s['last_ts']:
        raise ValueError('Sample timestamp mismatch')
    if s['average'] is not None and (not math.isfinite(s['average']) or s['average'] <= 0):
        raise ValueError('Invalid average')
    if s['candidate_direction'] not in (-1, 0, 1):
        raise ValueError('Invalid confirmation direction')
    since = s['candidate_since']
    if (since is None) != (s['candidate_direction'] == 0):
        raise ValueError('Invalid confirmation timer')
    if since is not None and (not math.isfinite(since) or not 0 < since <= s['last_ts']):
        raise ValueError('Invalid confirmation start')


def reset_timer(s):
    s['candidate_direction'] = 0
    s['candidate_since'] = None


def update_reference(s, price, timestamp):
    previous_ts = s['last_ts']
    if previous_ts and timestamp - previous_ts > CONFIG['max_gap']:
        s['samples'] = []
        reset_timer(s)
        s['events'].append(dict(time=timestamp, kind='DATA_GAP',
                                seconds=timestamp-previous_ts))
    s['last_ts'] = timestamp
    s['last_price'] = price
    s['samples'].append([timestamp, price])
    cutoff = timestamp - CONFIG['lookback']
    # Keep the last quote at/before cutoff to cover the beginning of the hour.
    while len(s['samples']) > 1 and s['samples'][1][0] <= cutoff:
        s['samples'].pop(0)
    if s['samples'][0][0] > cutoff:
        s['average'] = None
        reset_timer(s)
        return False
    # Weight each quote by the elapsed time until the next quote. No future data.
    samples = s['samples']
    total = math.fsum(p * (samples[i+1][0] - max(t, cutoff))
                      for i, (t, p) in enumerate(samples[:-1]))
    average = total / CONFIG['lookback']
    s['average'] = average
    if not s['initialized']:
        s['anchor'] = average
        s['initialized'] = True
        s['events'].append(dict(time=timestamp, kind='INITIAL_REFERENCE', reference=average))
        return True
    direction = (1 if average >= s['anchor'] * (1 + CONFIG['shift_fraction'])
                 else -1 if average <= s['anchor'] * (1 - CONFIG['shift_fraction']) else 0)
    if direction == 0:
        reset_timer(s)
    elif direction != s['candidate_direction']:
        s['candidate_direction'] = direction
        s['candidate_since'] = timestamp
    elif timestamp - s['candidate_since'] >= CONFIG['confirmation']:
        old = s['anchor']
        s['anchor'] = average
        s['shifts'] += 1
        s['events'].append(dict(time=timestamp, kind='REFERENCE_SHIFT',
                                old=old, reference=average, direction=direction,
                                confirmed_seconds=timestamp-s['candidate_since']))
        reset_timer(s)
    return True


def sell_target(s):
    if s['btc'] == 0:
        return None
    target = ((s['cost'] + CONFIG['target_net']) /
              (s['btc'] * (1-CONFIG['slippage']) * (1-CONFIG['fee'])))
    return math.ceil(target * 100) / 100


def step(saved, price, timestamp):
    validate(saved)
    if not math.isfinite(price) or price <= 0 or not math.isfinite(timestamp) or timestamp <= max(0, saved['last_ts']):
        raise ValueError('Invalid quote or non-advancing clock')
    s = copy.deepcopy(saved)
    ready = update_reference(s, price, timestamp)
    target = sell_target(s)
    trade = None
    # An open position may exit after a quote gap; fresh buys require a full hour.
    if target is not None and price >= target:
        gross = s['btc'] * price * (1-CONFIG['slippage'])
        fee = gross * CONFIG['fee']
        proceeds = gross - fee
        profit = proceeds - s['cost']
        if profit >= CONFIG['target_net']:
            trade = dict(time=timestamp, side='SELL', price=price,
                         btc=s['btc'], fee=fee, profit=profit)
            s['cash'] += proceeds
            s['fees'] += fee
            s['realized'] += profit
            s['sells'] += 1
            s.update(btc=0.0, cost=0.0, open_buys=0)
    elif (ready and price <= s['anchor'] * (1-CONFIG['buy_drop'])
          and s['open_buys'] < CONFIG['max_buys']
          and s['cost'] + CONFIG['buy_size'] <= CONFIG['max_cost']
          and s['cash'] >= CONFIG['buy_size']):
        size = CONFIG['buy_size']
        fee = size * CONFIG['fee']
        quantity = (size-fee) / (price * (1+CONFIG['slippage']))
        s['cash'] -= size
        s['btc'] += quantity
        s['cost'] += size
        s['fees'] += fee
        s['open_buys'] += 1
        s['buys'] += 1
        trade = dict(time=timestamp, side='BUY', price=price,
                     btc=quantity, fee=fee, profit=0.0)
    # Trades never move the reference in this version.
    if trade:
        s['trades'].append(trade)
    validate(s)
    return s, trade


def save(s):
    temporary = STATE.with_suffix('.tmp')
    with temporary.open('w') as f:
        json.dump(s, f, indent=2, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(STATE)


def price_now():
    req = urllib.request.Request('https://api.coinbase.com/v2/prices/BTC-USD/spot',
                                 headers={'User-Agent': 'overton-accepted-range-paper/1'})
    with urllib.request.urlopen(req, timeout=20) as response:
        price = float(json.load(response)['data']['amount'])
    if not math.isfinite(price) or price <= 0:
        raise ValueError('Invalid BTC quote')
    return price


def status(s):
    target = sell_target(s)
    sale = f'${target:,.2f}' if target is not None else 'pending first buy'
    average = f"${s['average']:,.2f}" if s['average'] is not None else 'collecting one continuous hour'
    timer = (f"{int(s['last_ts']-s['candidate_since'])}/1800 seconds ({'up' if s['candidate_direction'] > 0 else 'down'})"
             if s['candidate_since'] is not None else 'none')
    value = s['cash'] + s['btc'] * s['last_price']
    net_exit = s['cash'] + s['btc'] * s['last_price'] * (1-CONFIG['slippage']) * (1-CONFIG['fee'])
    buying = 'PAUSED at cap' if s['open_buys'] == 3 else ('available' if s['average'] is not None else 'WARMING UP')
    print(f"PAPER ONLY | last sample {iso(s['last_ts'])} | BTC/USD ${s['last_price']:,.2f}")
    print(f"Hourly average {average} | reference ${s['anchor']:,.2f} | confirmation {timer} | shifts {s['shifts']}")
    print(f"BUY <= ${s['anchor'] * 0.99:,.2f} | SELL >= {sale} | open buys {s['open_buys']}/3 | cost ${s['cost']:.2f}/150 | buying {buying}")
    print(f"Cash ${s['cash']:.2f} | value before exit costs ${value:.2f} | net exit estimate ${net_exit:.2f} | hold before exit costs ${s['benchmark_btc']*s['last_price']:.2f}")
    print(f"{s['buys']} buys / {s['sells']} sells | fees ${s['fees']:.2f} | realized ${s['realized']:.2f}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        import unittest
        from test_accepted_range import Checks
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    if args.status:
        if STATE.exists():
            s = json.loads(STATE.read_text())
            validate(s)
            status(s)
        else:
            print('No saved paper samples yet.')
        return
    with (BASE / 'accepted_range.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        s = json.loads(STATE.read_text()) if STATE.exists() else None
        if s is not None:
            validate(s)
        print('PAPER ONLY | $1,000 simulated USD; $50 buys; $150 cap; $2 net full-position sale.\nReference follows hourly average only after a same-direction 1% shift persists 30 minutes.\nOne hour warm-up; gaps over 10 minutes restart collection/confirmation.\nAssumptions: 1.2% fee + 0.05% slippage each way; Coinbase BTC/USD spot, no order API.', flush=True)
        while True:
            try:
                price = price_now()
            except Exception as error:
                print(f'Quote unavailable: {type(error).__name__}: {error}', flush=True)
            else:
                now = time.time()
                if s is None:
                    s = initial(price)
                if now > s['last_ts']:
                    event_count = len(s['events'])
                    new, trade = step(s, price, now)
                    # Atomic state includes trades, shifts and balances together.
                    save(new)
                    s = new
                    for event in s['events'][event_count:]:
                        print('PAPER EVENT ' + json.dumps(event), flush=True)
                    if trade:
                        print(f"PAPER {trade['side']} | net profit ${trade['profit']:.2f}", flush=True)
                    status(s)
                else:
                    print('Clock has not advanced; no trade or confirmation.', flush=True)
            time.sleep(CONFIG['interval'])


if __name__ == '__main__':
    main()
