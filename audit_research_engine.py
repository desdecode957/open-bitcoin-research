#!/usr/bin/env python3
"""Cached-only execution/accounting/start-date audit; no strategy selection."""
import bisect
import json
import math
from pathlib import Path

import audit_hybrid_trades as cached
import compare_strategies as model
import hybrid_research as hybrid
import long_history_research as history
import selective_entry_research as selective

ROOT = Path(__file__).resolve().parent
STRATEGIES = ('current_window', hybrid.NAME, selective.NAME, 'hold_150')
START_DATES = ('2022-04-01', '2022-07-01', '2022-10-01',
               '2023-01-01', '2023-04-01', '2023-07-01')


def simulate(rows, name, first=0):
    if name == hybrid.NAME:
        return hybrid.simulate(rows, first)
    if name == selective.NAME:
        return selective.simulate(rows, first)
    spec = model.CANDIDATES[0] if name == 'current_window' else ('hold_150', 'hold', 0, 0, 0)
    return model.simulate(rows, spec, first)


def replay(rows, first, result, fee=.005, slip=.0005):
    """Independent ledger: reconstruct positions solely from recorded fills.

    Also value surviving positions at candle lows. Its stress drawdown uses
    peaks from earlier candles, avoiding an invented same-candle high/low order.
    It is a stress measure, not a complete tick-by-tick maximum drawdown.
    """
    by_time = {}
    for fill in result['fills']:
        if fill[0] in by_time:
            raise ValueError('Multiple fills at the same time')
        by_time[fill[0]] = fill
    cash, btc, cost = 1000.0, 0.0, 0.0
    fees = realized = 0.0
    peak = low_peak = 1000.0
    dd = low_dd = 0.0
    buys = sells = used = 0
    for i in range(first, len(rows)):
        row = rows[i]
        fill = by_time.get(row[0])
        if fill:
            used += 1
            if i <= first or row[0] - rows[i - 1][0] != 300:
                raise ValueError('Fill without a contiguous previous signal candle')
            _, side, price, amount = fill
            expected = row[3] * (1 + slip if side == 'BUY' else 1 - slip)
            if not math.isclose(price, expected, rel_tol=1e-10):
                raise ValueError('Fill price differs from the execution model')
            if side == 'BUY':
                if amount > cash + 1e-8 or cost + amount > 150 + 1e-8:
                    raise ValueError('Purchase exceeds cash or entry-cost cap')
                btc += amount * (1 - fee) / price
                cash -= amount
                cost += amount
                fees += amount * fee
                buys += 1
            elif side == 'SELL':
                expected_proceeds = btc * price * (1 - fee)
                if not math.isclose(amount, expected_proceeds, abs_tol=1e-7):
                    raise ValueError('Recorded proceeds do not match BTC sold')
                fees += btc * price * fee
                realized += amount - cost
                cash += amount
                btc = cost = 0.0
                sells += 1
            else:
                raise ValueError('Unknown fill side')
        net_units = btc * (1 - slip) * (1 - fee)
        equity = cash + net_units * row[4]
        peak = max(peak, equity)
        dd = max(dd, 1 - equity / peak)
        low_value = cash + net_units * row[1]
        low_dd = max(low_dd, 1 - low_value / low_peak)
        low_peak = max(low_peak, cash + net_units * row[2])
    if used != len(by_time):
        raise ValueError('Fills exist outside the evaluation interval')
    actual = dict(profit=equity - 1000, end_value=equity, fees_paid=fees,
                  realized=realized, unsold_btc=btc, open_cost=cost,
                  open_net_profit=net_units * rows[-1][4] - cost,
                  max_drawdown_pct=100 * dd, buys=buys, sells=sells)
    for key, value in actual.items():
        if not math.isclose(value, result[key], abs_tol=1e-7):
            raise ValueError('Independent ledger mismatch: ' + key)
    if not math.isclose(actual['profit'], realized + actual['open_net_profit'], abs_tol=1e-7):
        raise ValueError('Realized and unrealized result do not reconcile')
    return dict(accounting_verified=True, low_price_stress_decline_pct=100 * low_dd)


def period(rows, start, days=180, warm=True):
    """Identical calendar duration, fresh $1,000; warm-up never trades."""
    end = start + days * 86400
    if start < rows[0][0] or end > rows[-1][0] + 300:
        raise ValueError('Requested window is outside cached training history')
    times = [r[0] for r in rows]
    left = bisect.bisect_left(times, start - 40 * 86400 if warm else start)
    right = bisect.bisect_left(times, end)
    selected = rows[left:right]
    first = bisect.bisect_left([r[0] for r in selected], start)
    if len(selected) - first < 2:
        raise ValueError('Not enough evaluation candles')
    return selected, first


def delayed_prices(rows):
    """Stress scenario: next bar close as fill reference, rather than open.

    Signals/indicators retain their original closes. Fills are modeled about
    five minutes later, not tick-accurate order scheduling. This changes trades
    naturally; it does not cherry-pick a better execution price.
    """
    return [[r[0], r[1], r[2], r[4], r[4], r[5]] for r in rows]


def main():
    print('ENGINE AUDIT | cached training only | no selection or bot changes', flush=True)
    rows = cached.cached_training(ROOT)
    original = json.loads((ROOT / 'hybrid-results.json').read_text())
    saved_selective = json.loads((ROOT / 'selective-entry-v7' / 'results.json').read_text())
    if history.digest(rows) != original['training_data']['sha256']:
        raise RuntimeError('Training price fingerprint changed')
    if hybrid.source_hashes() != original['source_hashes']:
        raise RuntimeError('Original research source changed')
    if selective.hashes() != saved_selective['source_hashes']:
        raise RuntimeError('Selective-entry source changed')
    report = dict(training_price_sha256=history.digest(rows), original_checks=[], windows=[])
    expected = {r['strategy']: r for r in original['training']}
    expected[selective.NAME] = saved_selective['training'][0]
    print('\nORIGINAL FULL-PERIOD REPRODUCTION AND INDEPENDENT ACCOUNTING', flush=True)
    for name in STRATEGIES:
        result = simulate(rows, name)
        ledger = replay(rows, 0, result)
        for key in ('profit', 'fees_paid', 'buys', 'sells', 'max_drawdown_pct'):
            if not math.isclose(result[key], expected[name][key], abs_tol=1e-7):
                raise RuntimeError('Saved result not reproduced: ' + name + ' ' + key)
        report['original_checks'].append(dict(strategy=name, result=model.brief(result), **ledger))
        print(f"{name}: verified; net ${result['profit']:+.2f}; closing-price decline "
              f"{result['max_drawdown_pct']:.2f}%; low-price stress {ledger['low_price_stress_decline_pct']:.2f}%", flush=True)
    print('\nSIX EQUAL 180-DAY WINDOWS; same cash/entry cap; full indicator warm-up', flush=True)
    print('Start       Strategy                   Normal net   Cold-start net   Later-fill net', flush=True)
    for date in START_DATES:
        start = history.stamp(date)
        warmed, first = period(rows, start)
        cold, _ = period(rows, start, warm=False)
        later = delayed_prices(warmed)
        for name in STRATEGIES:
            result = simulate(warmed, name, first)
            ledger = replay(warmed, first, result)
            cold_result = simulate(cold, name)
            later_result = simulate(later, name, first)
            replay(cold, 0, cold_result)
            replay(later, first, later_result)
            item = dict(start=date, strategy=name, days=180,
                        warm=model.brief(result), cold=model.brief(cold_result),
                        delayed=model.brief(later_result), **ledger)
            report['windows'].append(item)
            print(f"{date} {name:26} {result['profit']:+10.2f} "
                  f"{cold_result['profit']:+16.2f} {later_result['profit']:+16.2f}", flush=True)
    report['limitations'] = (
        'Overlapping windows are descriptive sensitivity checks, not independent unseen tests. '
        'No settings are selected from this audit. Cold starts lack earlier indicator history; '
        'the full-period original had no pre-2022 warm-up. Later fills use next-bar closes, '
        'a deliberately slower execution scenario, not an accurate latency model. '
        'Reported original drawdown used closes; low-price stress avoids assuming the order '
        'of highs/lows within the same bar, so it is not exact tick-level drawdown. '
        'Fixed fees and slippage may differ from actual historical costs. USD, before taxes, '
        'GIS effects, CAD conversion, and cash interest. Reserved history remains unused.')
    output = ROOT / 'engine-audit-results.json'
    history.data.atomic_json(output, report)
    print('\nAll accounting and original-result reproduction checks passed.', flush=True)
    print(report['limitations'], flush=True)
    print('Report:', output, flush=True)


if __name__ == '__main__':
    main()
