#!/usr/bin/env python3
"""Audit the existing 2022–2023 hybrid simulation. Cached data only; no network."""
import json
import math
from collections import defaultdict
from pathlib import Path

import backtest_window as data
import compare_strategies as model
import hybrid_research as hybrid
import long_history_research as history

ROOT = Path(__file__).resolve().parent
FEE = .005
SLIP = .0005


def cached_training(root):
    start, end = map(history.stamp, hybrid.PROTOCOL['training'])
    rows = []
    for left in range(start, end, 300 * 299):
        right = min(end, left + 300 * 299)
        path = root / 'backtest-data' / f'coinbase-BTC-USD-5m-{left}-{right}.json'
        if not path.exists():
            raise FileNotFoundError(f'Missing cached batch: {path}. No download attempted.')
        rows.extend(data.validate(json.loads(path.read_text()), left, right))
    return data.validate(rows, start, end)


def close_trade(buy, sell, rows, index, means):
    buy_time, _, buy_fill, amount = buy
    sell_time, _, sell_fill, proceeds = sell
    i, j = index[buy_time], index[sell_time]
    if j == 0 or rows[j][0] - rows[j - 1][0] != 300:
        raise ValueError('Sale does not follow a contiguous signal candle')
    previous_close = rows[j - 1][4]
    fast, slow = means[168][j - 1], means[720][j - 1]
    trend = (fast is not None and slow is not None
             and (fast <= slow or previous_close < slow))
    reasons = []
    if trend:
        reasons.append('trend')
    if previous_close <= buy_fill * .95:
        reasons.append('stop')
    if previous_close >= buy_fill * 1.06:
        reasons.append('target')
    if not reasons:
        raise ValueError('Sale has no reproducible exit trigger')
    quantity = amount * (1 - FEE) / buy_fill
    entry_fee = amount * FEE
    exit_fee = quantity * sell_fill * FEE
    expected_proceeds = quantity * sell_fill * (1 - FEE)
    if not math.isclose(proceeds, expected_proceeds, abs_tol=1e-8):
        raise ValueError('Sale proceeds do not match the simulated fee model')
    gross = quantity * (sell_fill - buy_fill)
    net = proceeds - amount
    if not math.isclose(gross - entry_fee - exit_fee, net, abs_tol=1e-8):
        raise ValueError('Trade accounting does not reconcile')
    # Counterfactuals keep actual trade dates and purchase budgets fixed.
    # They are diagnostics, not reruns that would generate their own signals.
    fixed_no_fees = amount * (sell_fill / buy_fill - 1)
    fixed_no_costs = amount * (rows[j][3] / rows[i][3] - 1)
    return dict(entry_utc=data.iso(buy_time), exit_utc=data.iso(sell_time),
                hours_held=(sell_time - buy_time) / 3600,
                exit_reasons=reasons, reason_group='+'.join(reasons),
                purchase_cost_usd=amount, entry_fill_usd=buy_fill,
                exit_fill_usd=sell_fill, net_profit_usd=net,
                before_explicit_fees_usd=gross,
                fees_paid_usd=entry_fee + exit_fee,
                fixed_dates_no_fees_usd=fixed_no_fees,
                fixed_dates_no_fees_or_slippage_usd=fixed_no_costs)


def analyze(rows, simulation):
    if simulation['open_cost'] > 1e-8 or simulation['unsold_btc'] > 1e-12:
        raise ValueError('This closed-trade audit requires no remaining position')
    index = {r[0]: i for i, r in enumerate(rows)}
    means = {h: model.averages(rows, h) for h in (168, 720)}
    trades = []
    buy = None
    for fill in simulation['fills']:
        if fill[1] == 'BUY':
            if buy is not None:
                raise ValueError('Overlapping purchases')
            buy = fill
        else:
            if buy is None:
                raise ValueError('Sale without a purchase')
            trades.append(close_trade(buy, fill, rows, index, means))
            buy = None
    if buy is not None:
        raise ValueError('Unpaired final purchase')
    net = sum(t['net_profit_usd'] for t in trades)
    fees = sum(t['fees_paid_usd'] for t in trades)
    gross = sum(t['before_explicit_fees_usd'] for t in trades)
    if not math.isclose(net, simulation['profit'], abs_tol=1e-7):
        raise ValueError('Trade total differs from account profit')
    if not math.isclose(fees, simulation['fees_paid'], abs_tol=1e-7):
        raise ValueError('Trade fees differ from account fees')
    groups = defaultdict(lambda: dict(count=0, net=0.0, before_fees=0.0, fees=0.0))
    years = defaultdict(lambda: dict(count=0, net=0.0))
    for t in trades:
        group = groups[t['reason_group']]
        group['count'] += 1
        group['net'] += t['net_profit_usd']
        group['before_fees'] += t['before_explicit_fees_usd']
        group['fees'] += t['fees_paid_usd']
        year = years[t['exit_utc'][:4]]
        year['count'] += 1
        year['net'] += t['net_profit_usd']
    wins = [t['net_profit_usd'] for t in trades if t['net_profit_usd'] > 0]
    losses = [t['net_profit_usd'] for t in trades if t['net_profit_usd'] < 0]
    return dict(trades=trades, trade_count=len(trades), net_profit_usd=net,
                before_explicit_fees_usd=gross, fees_paid_usd=fees,
                fixed_dates_no_fees_usd=sum(t['fixed_dates_no_fees_usd'] for t in trades),
                fixed_dates_no_fees_or_slippage_usd=sum(
                    t['fixed_dates_no_fees_or_slippage_usd'] for t in trades),
                wins=len(wins), losses=len(losses), breakevens=len(trades)-len(wins)-len(losses),
                average_win_usd=sum(wins)/len(wins) if wins else 0,
                average_loss_usd=sum(losses)/len(losses) if losses else 0,
                exits=dict(groups), exit_years=dict(years))


def main():
    saved = json.loads((ROOT / 'hybrid-results.json').read_text())
    if saved['protocol_hash'] != history.digest(hybrid.PROTOCOL):
        raise RuntimeError('Rules differ from the saved experiment')
    if saved['source_hashes'] != hybrid.source_hashes():
        raise RuntimeError('Research source changed since the saved experiment')
    print('READ-ONLY DIAGNOSIS | cached training candles only | no new strategy test', flush=True)
    rows = cached_training(ROOT)
    if history.digest(rows) != saved['training_data']['sha256']:
        raise RuntimeError('Cached prices differ from the original test')
    simulation = hybrid.simulate(rows)
    expected = next(r for r in saved['training'] if r['strategy'] == hybrid.NAME)
    for key in ('profit', 'fees_paid', 'buys', 'sells', 'open_cost', 'max_drawdown_pct'):
        if not math.isclose(simulation[key], expected[key], abs_tol=1e-7):
            raise RuntimeError('Original result not reproduced: ' + key)
    report = analyze(rows, simulation)
    report.update(original_protocol_hash=saved['protocol_hash'],
                  training_data_sha256=saved['training_data']['sha256'],
                  limitations='Exit groups describe the original signals; they do not establish '
                  'what would happen if an exit were removed. Counterfactual cost totals retain '
                  'the same dates and budgets, not independently generated trades. '
                  'All amounts USD, before taxes/GIS/CAD conversion. Reserved history untouched.')
    output = ROOT / 'hybrid-trade-audit.json'
    data.atomic_json(output, report)
    print('Original result and cached price fingerprint verified.', flush=True)
    print(f"\nCompleted trades: {report['trade_count']}")
    print(f"Before explicit fees (slippage included): ${report['before_explicit_fees_usd']:+.2f}")
    print(f"Actual modeled fees paid:                ${report['fees_paid_usd']:.2f}")
    print(f"Net account profit:                     ${report['net_profit_usd']:+.2f}")
    print(f"Wins / losses / breakeven: {report['wins']} / {report['losses']} / {report['breakevens']}")
    print(f"Average win ${report['average_win_usd']:.2f}; average loss ${report['average_loss_usd']:.2f}")
    print('\nEXIT SIGNAL GROUPS (overlapping triggers are grouped together)')
    print('Reason                 Count   Before fees    Fees       Net USD')
    for reason, r in sorted(report['exits'].items()):
        print(f"{reason:22} {r['count']:5} {r['before_fees']:+12.2f} {r['fees']:8.2f} {r['net']:+12.2f}")
    print('\nBY EXIT YEAR')
    for year, r in sorted(report['exit_years'].items()):
        print(f"{year}: {r['count']} trades, ${r['net']:+.2f} net")
    print('\nDIAGNOSTIC ONLY: same trade dates and dollar budgets')
    print(f"Without fees, keeping slippage: ${report['fixed_dates_no_fees_usd']:+.2f}")
    print(f"Without fees or slippage:       ${report['fixed_dates_no_fees_or_slippage_usd']:+.2f}")
    print(report['limitations'])
    print('Report:', output)


if __name__ == '__main__':
    main()
