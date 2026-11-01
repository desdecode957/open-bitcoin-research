#!/usr/bin/env python3
"""Research only: compare fixed BTC strategies. Never places orders.

Run beside backtest_window.py. USD candles and account values; not CAD income.
All strategies start with cash, with the same $150 maximum entry budget.
"""
import argparse
import bisect
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import backtest_window as data

START = 1000.0
CAP = 150.0
BAR = 300
FEE = 0.005
SLIP = 0.0005
CANDIDATES = (
    ('current_window', 'window', 0, 0, 0),
    ('trend_12h_48h', 'trend', 12, 48, 0),
    ('trend_24h_72h', 'trend', 24, 72, 0),
    ('dip_24h_2pct', 'dip', 24, 0, .02),
    ('dip_48h_3pct', 'dip', 48, 0, .03),
)


def averages(rows, hours):
    """Trailing observed closes, with >=99% coverage; never future prices."""
    times = [r[0] for r in rows]
    sums = [0.0]
    for r in rows:
        sums.append(sums[-1] + r[4])
    duration = hours * 3600
    minimum = math.ceil(.99 * duration / BAR)
    result = []
    for i, stamp in enumerate(times):
        left = bisect.bisect_left(times, stamp - duration + BAR, 0, i + 1)
        count = i + 1 - left
        ready = stamp - times[0] >= duration - BAR and count >= minimum
        result.append((sums[i + 1] - sums[left]) / count if ready else None)
    return result


def simulate(rows, spec, start=0, end=None, fee=FEE, slip=SLIP):
    """Signals at close, next observed contiguous open fills, no leverage.

    Prior rows warm indicators only; no positions carry into a new period.
    Window strategy is implemented here with the original anchor/reset rules.
    Dip and trend exits can realize losses. All ending holdings are valued net
    of hypothetical exit costs. CAP limits entry cost, not price appreciation.
    """
    end = len(rows) if end is None else end
    if not 0 <= start < end <= len(rows) or end - start < 2:
        raise ValueError('Need at least two candles in each period')
    if not 0 <= fee < 1 or not 0 <= slip < 1:
        raise ValueError('Invalid fee/slippage')
    name, kind, fast, slow, drop = spec
    if kind not in ('window', 'trend', 'dip', 'hold'):
        raise ValueError('Unknown strategy')
    fast_values = averages(rows[:end], fast) if fast else None
    slow_values = averages(rows[:end], slow) if slow else None
    cash, btc, cost = START, 0.0, 0.0
    anchor = rows[start][4]
    pending = None
    buys = sells = exposure_bars = 0
    paid = realized = 0.0
    peak = START
    dd = 0.0
    pause_until = 0
    # Preserve a recent pre-period outage in the indicator warm-up history.
    for i in range(1, start + 1):
        if rows[i][0] - rows[i - 1][0] > 900:
            pause_until = rows[i][0] + 86400
    fills = []
    equity = START
    liquidation = 0.0
    for i in range(start, end):
        row = rows[i]
        gap = row[0] - rows[i - 1][0] if i > start else BAR
        if gap != BAR:
            pending = None
        if gap > 900:
            pause_until = row[0] + 86400
        paused = row[0] < pause_until
        if pending == 'BUY' and not paused:
            amount = min(50.0 if kind == 'window' else CAP, cash, CAP - cost)
            if amount > 1e-8:
                fill = row[3] * (1 + slip)
                charge = amount * fee
                btc += (amount - charge) / fill
                cash -= amount
                cost += amount
                paid += charge
                buys += 1
                anchor = fill
                fills.append((row[0], 'BUY', fill, amount))
        elif pending == 'SELL' and btc > 0 and not paused:
            fill = row[3] * (1 - slip)
            proceeds = btc * fill * (1 - fee)
            if kind != 'window' or proceeds > cost:
                paid += btc * fill * fee
                realized += proceeds - cost
                cash += proceeds
                btc = cost = 0.0
                sells += 1
                anchor = fill
                fills.append((row[0], 'SELL', fill, proceeds))
        pending = None
        liquidation = btc * row[4] * (1 - slip) * (1 - fee)
        equity = cash + liquidation
        peak = max(peak, equity)
        dd = max(dd, 1 - equity / peak)
        exposure_bars += int(btc > 0)
        if kind == 'hold':
            if buys == 0 and not paused:
                pending = 'BUY'
            continue
        if paused:
            continue
        if kind == 'window':
            # Original backtest does not trade on its first observed close.
            if i == start:
                continue
            if row[4] <= anchor * .99 and cash >= 50 and cost + 50 <= CAP:
                pending = 'BUY'
            elif btc > 0 and row[4] >= anchor * 1.04 and liquidation > cost:
                pending = 'SELL'
        else:
            avg = fast_values[i]
            if avg is None:
                continue
            if kind == 'trend':
                other = slow_values[i]
                if other is None:
                    continue
                want_buy, want_sell = avg > other, avg <= other
            else:
                want_buy, want_sell = row[4] <= avg * (1 - drop), row[4] >= avg
            if btc == 0 and want_buy:
                pending = 'BUY'
            elif btc > 0 and want_sell:
                pending = 'SELL'
    return dict(strategy=name, end_value=equity, profit=equity - START,
                max_drawdown_pct=100 * dd, buys=buys, sells=sells,
                fees_paid=paid, realized=realized, unsold_btc=btc,
                open_cost=cost, open_net_profit=liquidation - cost,
                exposure_pct=100 * exposure_bars / (end - start), fills=fills)


def brief(result):
    return {k: v for k, v in result.items() if k != 'fills'}


def research(rows, split):
    """Fixed rules before this rerun; no selection based on later outcomes."""
    hold_spec = ('hold_150', 'hold', 0, 0, 0)
    train_hold = simulate(rows, hold_spec, 0, split)
    training = [simulate(rows, s, 0, split) for s in CANDIDATES]
    # Heuristics screen weak candidates; these do not prove safety/reliability.
    eligible = [r for r in training if r['profit'] > max(0, train_hold['profit'])
                and r['sells'] >= 3 and r['max_drawdown_pct'] <= 5]
    selected = max(eligible, key=lambda r: (r['profit'], r['strategy'])) if eligible else None
    selected_name = selected['strategy'] if selected else None
    validation = [simulate(rows, s, split) for s in CANDIDATES]
    validation_hold = simulate(rows, hold_spec, split)
    chosen = next((r for r in validation if r['strategy'] == selected_name), None)
    passed = bool(chosen and chosen['profit'] > max(0, validation_hold['profit'])
                  and chosen['sells'] >= 3 and chosen['max_drawdown_pct'] <= 5)
    return dict(training=[brief(r) for r in training], training_hold=brief(train_hold),
                validation=[brief(r) for r in validation],
                validation_hold=brief(validation_hold), selected=selected_name,
                passed_research_screen=passed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end', default='2026-10-27', help='Exclusive UTC date')
    parser.add_argument('--cache-dir', type=Path,
                        help='Existing backtest-data directory (reuses candle files)')
    args = parser.parse_args()
    end_dt = datetime.strptime(args.end, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    if end_dt > datetime.now(timezone.utc):
        parser.error('End date cannot be in the future')
    if args.cache_dir:
        # Downloader appends backtest-data; accept only that explicit directory.
        if args.cache_dir.name != 'backtest-data':
            parser.error('--cache-dir must name a backtest-data directory')
        data.BASE = args.cache_dir.resolve().parent
    start = int((end_dt - timedelta(days=180)).timestamp())
    end = int(end_dt.timestamp())
    print('RESEARCH ONLY | no credentials, orders, or bot changes', flush=True)
    rows = data.download(start, end)
    split_time = start + 120 * 86400
    split = bisect.bisect_left([r[0] for r in rows], split_time)
    # Check the duplicated current rules against the existing research engine.
    data.FEE = FEE
    for segment, label in ((rows, '180d'), (rows[split:], '60d')):
        original = data.simulate(segment, .01, .04)
        ours = simulate(segment, CANDIDATES[0])
        if abs(original['profit'] - ours['profit']) > 1e-7:
            raise RuntimeError(f'Current-rule reproduction failed: {label}')
    report = research(rows, split)
    report.update(start=data.iso(start), end=data.iso(end),
                  split=data.iso(split_time), candles=len(rows),
                  missing=(end - start) // BAR - len(rows), fee=FEE, slip=SLIP,
                  account_usd=START, max_entry_cost_usd=CAP,
                  note='Reused historical validation; not an untouched test. '
                       'New rules need future paper evaluation. No taxes, cash '
                       'interest, CAD conversion, or actual spread data modeled.')
    for label, key, hold_key in (
            ('EARLIER 120 DAYS', 'training', 'training_hold'),
            ('LATER 60 DAYS — ALREADY SEEN HISTORY', 'validation', 'validation_hold')):
        print('\n' + label)
        print('Strategy                 Net USD     Worst decline   Buys/Sales   Open cost')
        for r in report[key] + [report[hold_key]]:
            print(f"{r['strategy']:24} {r['profit']:+9.2f} "
                  f"{r['max_drawdown_pct']:12.2f}% "
                  f"{r['buys']:5}/{r['sells']:<5} ${r['open_cost']:.2f}")
        print('All cash: $0.00 profit (interest not modeled).')
    print('\nSelected using earlier data only:', report['selected'] or 'NONE')
    print('Research screen:', 'PASSED; needs future paper testing'
          if report['passed_research_screen'] else 'NO QUALIFYING STRATEGY')
    print('Selection: positive profit above holding $150 BTC; >=3 completed sales;')
    print('maximum observed account decline <=5%. These are screening heuristics.')
    print('All strategies: $1,000 cash start, $150 entry cap; no reinvestment above cap.')
    print('Holding baseline buys at the next open after the period starts.')
    print('0.50% taker fee + assumed 0.05% adverse slippage on each side.')
    print('Exit costs included on unsold BTC. Profits are USD, before taxes.')
    print('Trend/dip indicators use earlier observations to warm up validation.')
    print(report['note'])
    output = Path(__file__).resolve().parent / ('strategy-comparison-' + args.end + '.json')
    data.atomic_json(output, report)
    print('Report:', output)


if __name__ == '__main__':
    main()
