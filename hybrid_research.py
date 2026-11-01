#!/usr/bin/env python3
"""Research-only sixth hypothesis: buy dips during a slower upward trend.

Never imports live bots, places orders, or uses trading credentials.
Keep beside the three existing research files to reuse cached candles.
"""
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import backtest_window as data
import compare_strategies as model
import long_history_research as history

ROOT = Path(__file__).resolve().parent
NAME = 'uptrend_dip_with_exits'
PROTOCOL = {
    'hypothesis_number': 6,
    'training': ['2022-01-01', '2024-01-01'],
    'reserved_evaluation': ['2024-01-01', '2026-04-30'],
    'trend_fast_hours': 168, 'trend_slow_hours': 720, 'dip_mean_hours': 24,
    'dip_below_mean': .02, 'profit_above_fill': .06, 'stop_below_fill': .05,
    'entry': '7-day mean >30-day mean, close >=30-day mean, close <=98% of 24-hour mean',
    'exit': '7-day mean <=30-day mean, close <30-day mean, '
            'close <=95% of entry fill, or close >=106% of entry fill',
    'account_usd': 1000, 'entry_budget_usd': 150,
    'fee_each_side': .005, 'slippage_each_side': .0005,
    'screen': 'profit >max(0, same-budget hold profit); at least 10 completed sales; '
              'observed account drawdown <=5% in each period',
    'stress_fee_each_side': .01,
}


def simulate(rows, start=0, fee=.005, slip=.0005):
    if not 0 <= start < len(rows) - 1:
        raise ValueError('Need at least two evaluation candles')
    if not 0 <= fee < 1 or not 0 <= slip < 1:
        raise ValueError('Invalid execution costs')
    means = {h: model.averages(rows, h) for h in (24, 168, 720)}
    cash, btc, cost = 1000.0, 0.0, 0.0
    entry = None
    peak = 1000.0
    dd = paid = realized = 0.0
    buys = sells = 0
    pending = None
    pause_until = 0
    fills = []
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
            amount = min(150.0, cash)
            entry = row[3] * (1 + slip)
            btc = amount * (1 - fee) / entry
            cost = amount
            cash -= amount
            paid += amount * fee
            buys += 1
            fills.append((row[0], 'BUY', entry, amount))
        elif not paused and pending == 'SELL' and btc > 0:
            fill = row[3] * (1 - slip)
            proceeds = btc * fill * (1 - fee)
            cash += proceeds
            realized += proceeds - cost
            paid += btc * fill * fee
            btc = cost = 0.0
            entry = None
            sells += 1
            fills.append((row[0], 'SELL', fill, proceeds))
        pending = None
        close = row[4]
        liquidation = btc * close * (1 - slip) * (1 - fee)
        equity = cash + liquidation
        peak = max(peak, equity)
        dd = max(dd, 1 - equity / peak)
        if paused:
            continue
        daily, fast, slow = (means[h][i] for h in (24, 168, 720))
        if btc > 0:
            trend_failed = (fast is not None and slow is not None
                            and (fast <= slow or close < slow))
            if trend_failed or close <= entry * .95 or close >= entry * 1.06:
                pending = 'SELL'
        elif all(v is not None for v in (daily, fast, slow)):
            if fast > slow and close >= slow and close <= daily * .98:
                pending = 'BUY'
    return dict(strategy=NAME, profit=equity - 1000, end_value=equity,
                max_drawdown_pct=100 * dd, buys=buys, sells=sells,
                fees_paid=paid, realized=realized, open_cost=cost, unsold_btc=btc,
                open_net_profit=liquidation - cost, fills=fills)


def qualifies(candidate, baseline):
    return (candidate['profit'] > max(0, baseline['profit'])
            and candidate['sells'] >= 10 and candidate['max_drawdown_pct'] <= 5)


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ('backtest_window.py', 'compare_strategies.py',
                         'long_history_research.py', 'hybrid_research.py')}


def run(download):
    manifest_path = ROOT / 'hybrid-frozen-selection.json'
    report_path = ROOT / 'hybrid-results.json'
    identity = dict(protocol=PROTOCOL, protocol_hash=history.digest(PROTOCOL),
                    source_hashes=source_hashes())
    if report_path.exists():
        saved = json.loads(report_path.read_text())
        if any(history.digest(saved[k]) != history.digest(v) for k, v in identity.items()):
            raise RuntimeError('Saved experiment uses different rules/code; preserve its results.')
        history.show('SAVED TRAINING', saved['training'])
        if saved['evaluation']:
            history.show('SAVED RESERVED EVALUATION', saved['evaluation'])
        print('Reading previous results; this is not a new independent test.', flush=True)
        print('Training passed:', saved['training_passed'], flush=True)
        print('Reserved evaluation passed:', saved.get('evaluation_passed', False), flush=True)
        print('Report:', report_path, flush=True)
        return saved
    print('RESEARCH ONLY | sixth fixed hypothesis | no service changes', flush=True)
    print('Buy: 2% dip below 24h mean, only above 30-day mean with 7-day mean >30-day mean.', flush=True)
    print('Exit: failed trend, 6% price-rise trigger, or 5% price-fall trigger.', flush=True)
    print('Triggers use closes; fills use the next contiguous open. Exit prices are not guaranteed.', flush=True)
    print('$1,000 USD start; $150 entry budget; 0.50% fees + assumed 0.05% slippage each side.', flush=True)
    start, split = map(history.stamp, PROTOCOL['training'])
    end = history.stamp(PROTOCOL['reserved_evaluation'][1])
    train = download(start, split)
    if train[0][0] < start or train[-1][0] >= split:
        raise ValueError('Training contains out-of-period prices')
    baseline_spec = ('hold_150', 'hold', 0, 0, 0)
    candidate = model.brief(simulate(train))
    baseline = model.brief(model.simulate(train, baseline_spec))
    current = model.brief(model.simulate(train, model.CANDIDATES[0]))
    passed = qualifies(candidate, baseline)
    manifest = dict(identity, training_data=history.metadata(train, start, split),
                    training=[candidate, current, baseline], training_passed=passed)
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if history.digest(previous) != history.digest(manifest):
            raise RuntimeError('Frozen hypothesis/training changed. Stop and preserve prior results.')
    else:
        data.atomic_json(manifest_path, manifest)
    history.show('TRAINING 2022–2023', manifest['training'])
    report = dict(manifest, evaluation=[])
    report['limitations'] = (
        'Sixth hypothesis, developed after viewing the earlier five failures. '
        'Repeated research can overfit; historical screening is not proof of future income. '
        'Reserved strategy results never select or change these rules. '
        'Each period starts in cash; earlier observations warm indicators only. '
        'Profit includes net liquidation value of unsold BTC. '
        'Fees/slippage included; taxes, GIS, CAD conversion and cash interest excluded. '
        'Stop trigger does not cap losses during gaps/outages or adverse fills. '
        'The $150 entry budget does not cap cumulative losses over repeated trades.')
    if not passed:
        data.atomic_json(report_path, report)
        print('TRAINING FAILED. Reserved history remains unrequested.', flush=True)
        print(report['limitations'], flush=True)
        print('Report:', report_path, flush=True)
        return report
    print('Training passed; fixed hypothesis saved. Downloading reserved evaluation.', flush=True)
    reserved = download(split, end)
    if reserved[0][0] < split or reserved[-1][0] >= end:
        raise ValueError('Evaluation contains out-of-period prices')
    warm = [r for r in train if r[0] >= split - 31 * 86400]
    rows = warm + reserved
    first = len(warm)
    candidate = model.brief(simulate(rows, first))
    baseline = model.brief(model.simulate(rows, baseline_spec, first))
    current = model.brief(model.simulate(rows, model.CANDIDATES[0], first))
    report.update(evaluation=[candidate, current, baseline],
                  evaluation_passed=qualifies(candidate, baseline),
                  evaluation_data=history.metadata(reserved, split, end))
    stress = [model.brief(simulate(rows, first, fee=.01)),
              model.brief(model.simulate(rows, baseline_spec, first, fee=.01))]
    report['stress_fee_results'] = stress
    data.atomic_json(report_path, report)
    history.show('RESERVED EVALUATION Jan 2024–Apr 29, 2026', report['evaluation'])
    history.show('COST STRESS: 1% fee each way', stress)
    print('Historical screen:', 'PASSED — needs future paper testing'
          if report['evaluation_passed'] else 'FAILED', flush=True)
    print(report['limitations'], flush=True)
    print('Report:', report_path, flush=True)
    return report


def main():
    data.BASE = ROOT
    limiter = history.RateLimiter(data.urllib.request.urlopen)
    with patch.object(data.urllib.request, 'urlopen', limiter):
        run(data.download)


if __name__ == '__main__':
    main()
