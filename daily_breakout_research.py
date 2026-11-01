#!/usr/bin/env python3
"""Fixed hypothesis 8: daily 60-day breakout / 30-day breakdown exit.

Cached 2022–2023 research only. No trading, parameter search, or reserved data.
"""
import json
from pathlib import Path

import audit_hybrid_trades as cached
import audit_research_engine as ledger
import backtest_window as data
import hybrid_research as prior
import long_history_research as history

ROOT = Path(__file__).resolve().parent
NAME = 'daily_breakout_60_30'


def daily_levels(rows):
    """Prior completed UTC-day highs/lows only; >=99% observed bars per day."""
    days = {}
    for row in rows:
        day = row[0] // 86400 * 86400
        record = days.setdefault(day, dict(count=0, first=row[0], last=row[0],
                                          high=row[2], low=row[1]))
        record['count'] += 1
        record['last'] = row[0]
        record['high'] = max(record['high'], row[2])
        record['low'] = min(record['low'], row[1])
    valid = {day: r for day, r in days.items()
             if r['count'] >= 286 and r['first'] == day and r['last'] == day + 86100}
    result = {}
    for day in valid:
        previous = [valid.get(day - offset * 86400) for offset in range(1, 61)]
        high = max(r['high'] for r in previous) if all(previous) else None
        low = min(r['low'] for r in previous[:30]) if all(previous[:30]) else None
        result[day] = (high, low)
    return result


def simulate(rows, start=0, fee=.005, slip=.0005):
    if not 0 <= start < len(rows) - 1 or not 0 <= fee < 1 or not 0 <= slip < 1:
        raise ValueError('Invalid period or costs')
    levels = daily_levels(rows)
    cash, btc, cost = 1000.0, 0.0, 0.0
    peak = 1000.0
    dd = paid = realized = 0.0
    buys = sells = 0
    fills = []
    pending = None
    pause_until = 0
    for i in range(1, start + 1):
        if rows[i][0] - rows[i - 1][0] > 900:
            pause_until = rows[i][0] + 86400
    for i in range(start, len(rows)):
        row = rows[i]
        gap = row[0] - rows[i - 1][0] if i > start else 300
        if gap != 300:
            pending = None
        if gap > 900:
            pause_until = row[0] + 86400
        paused = row[0] < pause_until
        if not paused and pending == 'BUY' and btc == 0 and cash > 1e-8:
            amount = min(150, cash)
            fill = row[3] * (1 + slip)
            btc = amount * (1 - fee) / fill
            cash -= amount
            cost = amount
            paid += amount * fee
            buys += 1
            fills.append((row[0], 'BUY', fill, amount))
        elif not paused and pending == 'SELL' and btc > 0:
            fill = row[3] * (1 - slip)
            proceeds = btc * fill * (1 - fee)
            cash += proceeds
            realized += proceeds - cost
            paid += btc * fill * fee
            btc = cost = 0.0
            sells += 1
            fills.append((row[0], 'SELL', fill, proceeds))
        pending = None
        liquidation = btc * row[4] * (1 - slip) * (1 - fee)
        equity = cash + liquidation
        peak = max(peak, equity)
        dd = max(dd, 1 - equity / peak)
        day = row[0] // 86400 * 86400
        if paused or row[0] != day + 86100 or day not in levels:
            continue
        high, low = levels[day]
        if btc == 0 and high is not None and row[4] > high:
            pending = 'BUY'
        elif btc > 0 and low is not None and row[4] < low:
            pending = 'SELL'
    return dict(strategy=NAME, end_value=equity, profit=equity - 1000,
                fees_paid=paid, realized=realized, max_drawdown_pct=100 * dd,
                buys=buys, sells=sells, open_cost=cost, unsold_btc=btc,
                open_net_profit=liquidation - cost, fills=fills)


def main():
    print('RESEARCH ONLY | fixed hypothesis 8 | cached 2022–2023 data only', flush=True)
    print('Daily close above prior 60-day high: buy. Below prior 30-day low: sell.', flush=True)
    print('Prior highs/lows exclude the signal day. Fill at next contiguous 5-minute open.', flush=True)
    print('$1,000 USD cash start; $150 entry budget; 0.50% fee + assumed 0.05% slippage each side.', flush=True)
    original = json.loads((ROOT / 'hybrid-results.json').read_text())
    if original['source_hashes'] != prior.source_hashes():
        raise RuntimeError('Previous research source changed')
    rows = cached.cached_training(ROOT)
    if history.digest(rows) != original['training_data']['sha256']:
        raise RuntimeError('Cached data no longer matches the original test')
    normal = simulate(rows)
    verified = ledger.replay(rows, 0, normal)
    delayed_rows = ledger.delayed_prices(rows)
    delayed = simulate(delayed_rows)
    ledger.replay(delayed_rows, 0, delayed)
    stress = simulate(rows, fee=.01)
    ledger.replay(rows, 0, stress, fee=.01)
    report = dict(protocol=dict(hypothesis=8, entry_prior_days=60, exit_prior_days=30,
                               fee=.005, slip=.0005, account_usd=1000, entry_budget_usd=150),
                  training_data=original['training_data'], result=normal,
                  delayed=delayed, stress_fee=stress, independent_ledger=verified)
    history.show('TRAINING 2022–2023', [normal] + original['training'])
    print(f"\nDaily strategy: ${normal['realized']:+.2f} realized; "
          f"${normal['open_net_profit']:+.2f} on unsold BTC; ${normal['fees_paid']:.2f} fees.", flush=True)
    print(f"Slower fills: ${delayed['profit']:+.2f}. Double fees: ${stress['profit']:+.2f}.", flush=True)
    print('Independent accounting: verified.', flush=True)
    print('Closed trades:', normal['sells'], '— a small count cannot establish reliability.', flush=True)
    print('This is a fixed diagnostic of a slower strategy, not an income qualification.', flush=True)
    print('Hypothesis 8 follows seven earlier approaches; repeated testing can overfit.', flush=True)
    print('Initial 60 days warm up; only five-minute observations are available. '
          'Daily signals may give back gains before exiting. USD before tax/GIS/CAD conversion.', flush=True)
    print('No reserved prices requested. No bot settings changed.', flush=True)
    output = ROOT / 'daily-breakout-results.json'
    data.atomic_json(output, report)
    print('Report:', output, flush=True)


if __name__ == '__main__':
    main()
