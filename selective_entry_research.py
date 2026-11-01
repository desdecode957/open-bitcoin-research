#!/usr/bin/env python3
"""Seventh research hypothesis: original hybrid + rising 30-day mean entry gate.

The original exits are unchanged. This file never places orders or edits bots.
"""
import bisect
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import audit_hybrid_trades as audit
import backtest_window as data
import compare_strategies as model
import hybrid_research as base
import long_history_research as history

SOURCE = Path(__file__).resolve().parent
OUTPUT = SOURCE / 'selective-entry-v7'
NAME = 'rising_30day_mean_dip'
PROTOCOL = dict(base.PROTOCOL, hypothesis_number=7,
                additional_entry_gate='Current 30-day mean strictly greater than its value '
                'at the candle one week earlier; if lagged data unavailable, do not enter.',
                unchanged_exits=True)


def entry_gate(rows, slow):
    """Past-only comparison; reject a missing/stale one-week-ago observation."""
    times = [r[0] for r in rows]
    allowed = []
    for i, stamp in enumerate(times):
        target = stamp - 7 * 86400
        previous = bisect.bisect_right(times, target, 0, i + 1) - 1
        ready = (previous >= 0 and target - times[previous] <= 300
                 and slow[i] is not None and slow[previous] is not None)
        allowed.append(bool(ready and slow[i] > slow[previous]))
    return allowed


def simulate(rows, start=0, fee=.005, slip=.0005):
    means = {h: model.averages(rows, h) for h in (24, 168, 720)}
    allowed = entry_gate(rows, means[720])
    # The original engine uses its 24-hour mean only for flat-account entries.
    # Masking that mean adds a gate while preserving every exit calculation.
    means[24] = [value if allowed[i] else None for i, value in enumerate(means[24])]
    with patch.object(model, 'averages', side_effect=lambda input_rows, h: means[h]):
        result = base.simulate(rows, start, fee, slip)
    result['strategy'] = NAME
    return result


def hashes():
    return {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
            for name in ('backtest_window.py', 'compare_strategies.py', 'hybrid_research.py',
                         'long_history_research.py', 'audit_hybrid_trades.py',
                         'selective_entry_research.py')}


def show_result(report):
    history.show('TRAINING 2022–2023', report['training'])
    print('Training screen:', 'PASSED' if report['training_passed'] else 'FAILED', flush=True)
    candidate = report['training'][0]
    print(f"New entry filter: {candidate['buys']} buys, ${candidate['fees_paid']:.2f} fees paid.", flush=True)
    if report.get('evaluation'):
        history.show('RESERVED EVALUATION Jan 2024–Apr 29, 2026', report['evaluation'])
        history.show('COST STRESS: 1% fee each way', report['stress_fee_results'])
        print('Reserved screen:', 'PASSED — future paper testing required'
              if report['evaluation_passed'] else 'FAILED', flush=True)


def run(download):
    OUTPUT.mkdir(exist_ok=True)
    manifest_path = OUTPUT / 'frozen-selection.json'
    report_path = OUTPUT / 'results.json'
    identity = dict(protocol=PROTOCOL, protocol_hash=history.digest(PROTOCOL), source_hashes=hashes())
    print('RESEARCH ONLY | hypothesis 7 | original exits unchanged', flush=True)
    print('Additional entry gate: 30-day mean must be higher than one week earlier.', flush=True)
    print('Same $1,000 USD account, $150 entry budget, fees and screening criteria.', flush=True)
    if report_path.exists():
        report = json.loads(report_path.read_text())
        if any(history.digest(report[k]) != history.digest(v) for k, v in identity.items()):
            raise RuntimeError('Saved experiment belongs to different rules/code; preserve its results.')
        print('Reading saved results; not a new independent test.', flush=True)
        show_result(report)
        print('Report:', report_path, flush=True)
        return report
    # Only cached training candles can be inspected before selection is frozen.
    original = json.loads((SOURCE / 'hybrid-results.json').read_text())
    if original['protocol_hash'] != history.digest(base.PROTOCOL) or original['source_hashes'] != base.source_hashes():
        raise RuntimeError('Original hybrid experiment no longer matches its saved rules/code')
    train = audit.cached_training(SOURCE)
    if history.digest(train) != original['training_data']['sha256']:
        raise RuntimeError('Cached training prices differ from the original test')
    candidate = model.brief(simulate(train))
    baseline = next(r for r in original['training'] if r['strategy'] == 'hold_150')
    passed = base.qualifies(candidate, baseline)
    manifest = dict(identity, training=[candidate] + original['training'],
                    training_data=original['training_data'], training_passed=passed)
    if manifest_path.exists():
        if history.digest(json.loads(manifest_path.read_text())) != history.digest(manifest):
            raise RuntimeError('Frozen selection changed; reserved results must not guide a retry.')
    else:
        data.atomic_json(manifest_path, manifest)
    report = dict(manifest, evaluation=[])
    report['limitations'] = (
        'Hypothesis 7 was developed after inspecting training results of six earlier approaches. '
        'This is exploratory research, subject to repeated-testing bias. '
        'The gate changes entries only; it does not remove loss exits. '
        'Passing a historical screen does not establish future income. '
        'All figures USD before taxes, GIS effects and CAD conversion; cash interest excluded. '
        'Unsold BTC valued after hypothetical exit fees/slippage. Stops do not guarantee exit prices.')
    if not passed:
        data.atomic_json(report_path, report)
        show_result(report)
        print('No reserved evaluation requested. No bot settings changed.', flush=True)
        print(report['limitations'], flush=True)
        print('Report:', report_path, flush=True)
        return report
    print('Training passed; rules and result frozen. Downloading reserved evaluation.', flush=True)
    split = history.stamp(PROTOCOL['reserved_evaluation'][0])
    end = history.stamp(PROTOCOL['reserved_evaluation'][1])
    reserved = download(split, end)
    if reserved[0][0] < split or reserved[-1][0] >= end:
        raise ValueError('Out-of-period evaluation prices')
    warm = [r for r in train if r[0] >= split - 40 * 86400]
    rows = warm + reserved
    first = len(warm)
    selected = model.brief(simulate(rows, first))
    hold_spec = ('hold_150', 'hold', 0, 0, 0)
    hold = model.brief(model.simulate(rows, hold_spec, first))
    report.update(evaluation=[selected, hold], evaluation_passed=base.qualifies(selected, hold),
                  evaluation_data=history.metadata(reserved, split, end),
                  stress_fee_results=[model.brief(simulate(rows, first, fee=.01)),
                                      model.brief(model.simulate(rows, hold_spec, first, fee=.01))])
    data.atomic_json(report_path, report)
    show_result(report)
    print(report['limitations'], flush=True)
    print('No real trades or bot changes. Report:', report_path, flush=True)
    return report


def main():
    data.BASE = SOURCE
    with patch.object(data.urllib.request, 'urlopen', history.RateLimiter(data.urllib.request.urlopen)):
        run(data.download)


if __name__ == '__main__':
    main()
