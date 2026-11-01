#!/usr/bin/env python3
"""Fixed-protocol BTC research, isolated from trading services.

Selection: 2022-01-01 through 2023-12-31.
Reserved historical evaluation: 2024-01-01 through 2026-04-29.
The previously inspected April 30–October 26, 2026 data is excluded.
This is historical evidence, not a promise of future profit.
"""
import hashlib
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import backtest_window as data
import compare_strategies as model

ROOT = Path(__file__).resolve().parent
TRAIN_START = '2022-01-01'
SPLIT = '2024-01-01'
EVAL_END = '2026-04-30'  # Exclusive; excludes all previously examined candles.
PROTOCOL = {
    'version': 1, 'train_start': TRAIN_START, 'split': SPLIT,
    'evaluation_end_exclusive': EVAL_END,
    'strategies': model.CANDIDATES,
    'account_usd': 1000, 'entry_cap_usd': 150,
    'fee_each_side': .005, 'slippage_each_side': .0005,
    'min_training_sales': 10, 'min_evaluation_sales': 10,
    'max_account_drawdown_pct': 5,
    'criterion': 'profit strictly above zero and same-budget holding; '
                 'at least 10 sales and maximum marked account decline <=5%',
    'tie_break': 'highest profit, then strategy name',
    'stress_fee_each_side': .01,
}


def stamp(date):
    return int(datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ('backtest_window.py', 'compare_strategies.py',
                         'long_history_research.py')}


def select(training, baseline):
    eligible = [r for r in training
                if r['profit'] > max(0, baseline['profit'])
                and r['sells'] >= PROTOCOL['min_training_sales']
                and r['max_drawdown_pct'] <= PROTOCOL['max_account_drawdown_pct']]
    winner = max(eligible, key=lambda r: (r['profit'], r['strategy'])) if eligible else None
    return winner['strategy'] if winner else None


def evaluate(rows, start, selected):
    """Evaluate only the frozen choice, plus current rules and fixed benchmarks."""
    hold = ('hold_150', 'hold', 0, 0, 0)
    specs = [model.CANDIDATES[0], hold]
    if selected and selected != model.CANDIDATES[0][0]:
        specs.append(next(s for s in model.CANDIDATES if s[0] == selected))
    results = [model.brief(model.simulate(rows, s, start)) for s in specs]
    chosen = next((r for r in results if r['strategy'] == selected), None)
    baseline = next(r for r in results if r['strategy'] == 'hold_150')
    passed = bool(chosen and chosen['profit'] > max(0, baseline['profit'])
                  and chosen['sells'] >= PROTOCOL['min_evaluation_sales']
                  and chosen['max_drawdown_pct'] <= PROTOCOL['max_account_drawdown_pct'])
    stress = []
    if selected:
        chosen_spec = next(s for s in model.CANDIDATES if s[0] == selected)
        stress = [model.brief(model.simulate(rows, s, start, fee=.01))
                  for s in (chosen_spec, hold)]
    return {'results': results, 'passed_historical_screen': passed,
            'stress_fee_results': stress}


def show(title, results):
    print('\n' + title, flush=True)
    print('Strategy                 Net USD     Worst decline   Buys/Sales   Open cost')
    for r in results:
        print(f"{r['strategy']:24} {r['profit']:+9.2f} "
              f"{r['max_drawdown_pct']:12.2f}% "
              f"{r['buys']:5}/{r['sells']:<5} ${r['open_cost']:.2f}", flush=True)
    print('All cash: $0 profit; interest not modeled.', flush=True)


def metadata(rows, start, end):
    expected = (end - start) // model.BAR
    return {'candles': len(rows), 'missing': expected - len(rows),
            'coverage': len(rows) / expected, 'sha256': digest(rows)}


class RateLimiter:
    """Bound all worker requests to four starts/second, including retries."""
    def __init__(self, opener):
        self.opener = opener
        self.lock = threading.Lock()
        self.previous = 0.0

    def __call__(self, *args, **kwargs):
        with self.lock:
            delay = self.previous + .25 - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.previous = time.monotonic()
        return self.opener(*args, **kwargs)


def run(download):
    """Write a frozen manifest before requesting any evaluation candles."""
    train_start, split, end = map(stamp, (TRAIN_START, SPLIT, EVAL_END))
    if end > int(datetime.now(timezone.utc).timestamp()):
        raise ValueError('Evaluation end is in the future')
    if model.FEE != .005 or model.SLIP != .0005 or model.START != 1000 or model.CAP != 150:
        raise RuntimeError('Comparison engine differs from the frozen protocol')
    print('RESEARCH ONLY | 5-minute BTC-USD candles | no trading credentials', flush=True)
    print('Training: 2022–2023. Reserved evaluation: Jan 2024–Apr 29, 2026.', flush=True)
    print('Five fixed strategies; 0.50% fee + assumed 0.05% slippage each way.', flush=True)
    print('First download may take several minutes; cached batches resume on rerun.', flush=True)
    manifest_path = ROOT / 'long-history-frozen-selection.json'
    report_path = ROOT / 'long-history-results.json'
    hashes = source_hashes()
    protocol_hash = digest(PROTOCOL)
    # A completed evaluation is never silently presented as a new first test.
    if report_path.exists():
        print('A result already exists. Reading saved results; no fresh-test claim.', flush=True)
        report = json.loads(report_path.read_text())
        if report['protocol_hash'] != protocol_hash or report['source_hashes'] != hashes:
            raise RuntimeError('Saved result belongs to different code/protocol; keep it for audit.')
        show('SAVED TRAINING', report['training'])
        if report.get('evaluation'):
            show('SAVED EVALUATION', report['evaluation']['results'])
        print('Selected:', report['selected'] or 'NONE', flush=True)
        print('Screen passed:', bool(report.get('evaluation', {}).get('passed_historical_screen')), flush=True)
        print('Report:', report_path, flush=True)
        return report
    train = download(train_start, split)
    if train[0][0] < train_start or train[-1][0] >= split:
        raise ValueError('Training contains prices outside its permitted interval')
    hold = ('hold_150', 'hold', 0, 0, 0)
    baseline = model.brief(model.simulate(train, hold))
    training = []
    for spec in model.CANDIDATES:
        print('Training strategy:', spec[0], flush=True)
        training.append(model.brief(model.simulate(train, spec)))
    selected = select(training, baseline)
    manifest = {'protocol': PROTOCOL, 'protocol_hash': protocol_hash,
                'source_hashes': hashes, 'training_data': metadata(train, train_start, split),
                'training': training + [baseline], 'selected': selected}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        if digest(previous) != digest(manifest):
            raise RuntimeError('Frozen selection changed. Stop; preserve the original evaluation.')
    else:
        data.atomic_json(manifest_path, manifest)
    show('TRAINING 2022–2023', manifest['training'])
    print('Frozen selection:', selected or 'NONE', flush=True)
    report = dict(manifest)
    report['manifest_sha256'] = digest(manifest)
    report['limitations'] = (
        'Historical candidate outcomes for the reserved interval were not used for selection. '
        'This does not establish future profitability. Future paper testing is still required. '
        'Same $1,000 USD cash start and $150 entry cap in each period; fresh positions, '
        'earlier observations warm indicators. Cash benchmark has no interest. '
        'Fees and assumed slippage included, including valuation of unsold holdings. '
        'Taxes, GIS effects, CAD conversion, real spread/latency/outages beyond candle gaps '
        'are not modeled. Entry cap is not a maximum possible loss over repeated trades. '
        'Historical screening thresholds are heuristics, not safety guarantees.')
    if selected is None:
        report['evaluation'] = {}
        data.atomic_json(report_path, report)
        print('NO QUALIFYING STRATEGY. Reserved history was not downloaded or evaluated.', flush=True)
        print('Report:', report_path, flush=True)
        return report
    print('Selection saved. Now downloading reserved historical evaluation.', flush=True)
    # No evaluation data can reach select(): selection is frozen and saved above.
    reserved = download(split, end)
    if reserved[0][0] < split or reserved[-1][0] >= end:
        raise ValueError('Evaluation contains prices outside the reserved interval')
    report['evaluation_data'] = metadata(reserved, split, end)
    # Only the preceding four days are needed for max 72-hour indicators/outage guards.
    warmup = [r for r in train if r[0] >= split - 4 * 86400]
    combined = warmup + reserved
    report['evaluation'] = evaluate(combined, len(warmup), selected)
    data.atomic_json(report_path, report)
    show('RESERVED EVALUATION Jan 2024–Apr 29, 2026', report['evaluation']['results'])
    show('COST STRESS: 1.00% fee each way', report['evaluation']['stress_fee_results'])
    print('Historical screen:', 'PASSED — future paper testing required'
          if report['evaluation']['passed_historical_screen'] else 'FAILED', flush=True)
    print('No bot settings changed. No real trades placed.', flush=True)
    print(report['limitations'], flush=True)
    print('Report:', report_path, flush=True)
    return report


def main():
    data.BASE = ROOT
    limiter = RateLimiter(data.urllib.request.urlopen)
    with patch.object(data.urllib.request, 'urlopen', limiter):
        run(data.download)


if __name__ == '__main__':
    main()
