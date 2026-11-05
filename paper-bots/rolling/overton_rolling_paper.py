#!/usr/bin/env python3
"""Independent PAPER-ONLY Overton: rolling 24h BTC price reference."""
import json
import math
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'rolling_state.json'
INTERVAL = 180
WINDOW = 24 * 3600
START_CASH = 160.0
BUY_USD = 50.0
MAX_BUYS = 3
BUY_DROP = 0.01
SELL_RISE = 0.04
FEE_RATE = 0.012
SELL_BUFFER = 0.005
TICKER = 'https://api.exchange.coinbase.com/products/BTC-USD/ticker'

def get_price():
    request = urllib.request.Request(TICKER, headers={'User-Agent': 'OvertonPaperResearch/1.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)
    price = float(result['price'])
    if not math.isfinite(price) or price <= 0:
        raise ValueError('Invalid ticker price')
    return price

def save(state):
    temp = DATA.with_suffix('.tmp')
    with temp.open('w') as handle:
        json.dump(state, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(DATA)

def load():
    if DATA.exists():
        state = json.loads(DATA.read_text())
        if state.get('strategy') != 'rolling-24h-paper-v1':
            raise ValueError('Unexpected saved strategy; no changes made')
        return state
    return dict(strategy='rolling-24h-paper-v1', cash=START_CASH, btc=0.0,
                cost=0.0, buys_this_cycle=0, buys=0, sells=0,
                fees=0.0, realized=0.0, samples=[], trades=[])

def step(state, now_ts, price):
    # Use only previous and current observed prices. Do not synthesize history.
    samples = [(float(t), float(p)) for t, p in state['samples'] if t >= now_ts - WINDOW]
    if samples and now_ts <= samples[-1][0]:
        raise ValueError('Nonadvancing sample time')
    samples.append((now_ts, price))
    state['samples'] = samples
    start_ts = samples[0][0]
    elapsed = now_ts - state.get('first_sample_ts', start_ts)
    state.setdefault('first_sample_ts', start_ts)
    # The first 24h must be observed before placing simulated trades.
    ready = elapsed >= WINDOW and len(samples) >= 100
    anchor = sum(p for _, p in samples) / len(samples)
    lower, upper = anchor * (1 - BUY_DROP), anchor * (1 + SELL_RISE)
    trade = None
    if ready and price <= lower and state['buys_this_cycle'] < MAX_BUYS and state['cash'] >= BUY_USD:
        bought = BUY_USD / (1 + FEE_RATE) / price
        fee = BUY_USD - bought * price
        state['cash'] -= BUY_USD
        state['btc'] += bought
        state['cost'] += BUY_USD
        state['fees'] += fee
        state['buys_this_cycle'] += 1
        state['buys'] += 1
        trade = dict(side='BUY', price=price, btc=bought, fee=fee)
    elif ready and state['btc'] > 0 and price >= upper:
        gross = state['btc'] * price
        fee = gross * FEE_RATE
        proceeds = gross - fee
        # Includes acquisition fee in cost and a 0.5% cushion on sell gross.
        if proceeds - gross * SELL_BUFFER > state['cost']:
            pnl = proceeds - state['cost']
            trade = dict(side='SELL', price=price, btc=state['btc'], fee=fee, profit=pnl)
            state['cash'] += proceeds
            state['btc'] = 0.0
            state['cost'] = 0.0
            state['buys_this_cycle'] = 0
            state['sells'] += 1
            state['realized'] += pnl
            state['fees'] += fee
    if trade:
        trade['time'] = datetime.fromtimestamp(now_ts, timezone.utc).isoformat()
        state['trades'].append(trade)
    return ready, anchor, lower, upper, trade

def main():
    state = load()
    print('PAPER ONLY — No exchange keys, orders or real funds.', flush=True)
    while True:
        try:
            price = get_price()
            ts = time.time()
            ready, anchor, lower, upper, trade = step(state, ts, price)
            save(state)
            if trade:
                print('PAPER TRADE:', trade, flush=True)
            print(f"{'ACTIVE' if ready else 'COLLECTING 24H'} | BTC ${price:,.2f} | "
                  f"24h reference ${anchor:,.2f} | BUY <= ${lower:,.2f} | "
                  f"SELL >= ${upper:,.2f} | cash ${state['cash']:.2f} | "
                  f"bot BTC {state['btc']:.8f} | buys {state['buys_this_cycle']}/3 | "
                  f"realized ${state['realized']:+.2f}", flush=True)
        except Exception as exc:
            print(f'PAPER ONLY | price/check failed: {exc}', flush=True)
        time.sleep(INTERVAL)

if __name__ == '__main__':
    main()
