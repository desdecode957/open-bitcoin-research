#!/usr/bin/env python3
"""Frozen reserved-history validation of hypothesis 8; no tuning or trading."""
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import audit_hybrid_trades as cached
import audit_research_engine as ledger
import backtest_window as data
import compare_strategies as model
import daily_breakout_research as daily
import long_history_research as history

ROOT = Path(__file__).resolve().parent
DAILY_SHA256 = 'e6ccf3faedc0b13232daab07fcfef91c9390171bc1ba53f9c0e804835ec92452'
TRAIN_PROTOCOL = dict(hypothesis=8, entry_prior_days=60, exit_prior_days=30,
                      fee=.005, slip=.0005, account_usd=1000, entry_budget_usd=150)
PROTOCOL = dict(TRAIN_PROTOCOL, evaluation_start='2024-01-01',
                evaluation_end_exclusive='2026-04-30',
                initial_positions='fresh cash; earlier observations warm daily levels only',
                comparison='hold $150 BTC and $850 cash, with identical costs',
                cost_stress_fee_each_side=.01,
                latency_stress='next five-minute candle close instead of its open',
                no_parameter_selection=True)


def hashes():
    names = ('backtest_window.py', 'compare_strategies.py', 'long_history_research.py',
             'hybrid_research.py', 'audit_hybrid_trades.py', 'selective_entry_research.py',
             'audit_research_engine.py', 'daily_breakout_research.py', 'validate_daily_breakout.py')
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def freeze(root, identity):
    """Save identity before requesting evaluation prices; never overwrite it."""
    path = root / 'daily-breakout-validation-frozen.json'
    if path.exists():
        saved = json.loads(path.read_text())
        if any(history.digest(saved[k]) != history.digest(v) for k, v in identity.items()):
            raise RuntimeError('Frozen rules, code, or training result changed. Preserve prior results.')
        return saved
    saved = dict(identity, frozen_at_utc=datetime.now(timezone.utc).isoformat())
    data.atomic_json(path, saved)
    return saved


def evaluation(rows, first):
    normal = daily.simulate(rows, first)
    normal_audit = ledger.replay(rows, first, normal)
    hold_spec = ('hold_150', 'hold', 0, 0, 0)
    hold = model.simulate(rows, hold_spec, first)
    ledger.replay(rows, first, hold)
    later_rows = ledger.delayed_prices(rows)
    later = daily.simulate(later_rows, first)
    ledger.replay(later_rows, first, later)
    later_hold = model.simulate(later_rows, hold_spec, first)
    ledger.replay(later_rows, first, later_hold)
    stress = daily.simulate(rows, first, fee=.01)
    ledger.replay(rows, first, stress, fee=.01)
    stress_hold = model.simulate(rows, hold_spec, first, fee=.01)
    ledger.replay(rows, first, stress_hold, fee=.01)
    return dict(normal=normal, hold=hold, later=later, later_hold=later_hold,
                higher_fee=stress, higher_fee_hold=stress_hold,
                independent_ledger=normal_audit,
                positive_after_costs=normal['profit'] > 0,
                above_holding=normal['profit'] > hold['profit'],
                positive_with_slower_fills=later['profit'] > 0,
                positive_with_double_fees=stress['profit'] > 0)


def display(report):
    r = report['evaluation']
    history.show('RESERVED JAN 2024–APR 29, 2026 | fresh $1,000 account',
                 [r['normal'], r['hold']])
    print(f"\nDaily strategy: ${r['normal']['realized']:+.2f} realized; "
          f"${r['normal']['open_net_profit']:+.2f} on unsold holdings; "
          f"${r['normal']['fees_paid']:.2f} fees paid.", flush=True)
    print('Stress scenario             Strategy net USD    Holding net USD', flush=True)
    for label, strategy, baseline in (
            ('Slower fills', 'later', 'later_hold'), ('Double fees', 'higher_fee', 'higher_fee_hold')):
        print(f"{label:26} {r[strategy]['profit']:+15.2f} {r[baseline]['profit']:+18.2f}", flush=True)
    print('Positive after costs:', r['positive_after_costs'], flush=True)
    print('Above same-budget holding:', r['above_holding'], flush=True)
    print('Positive with slower fills:', r['positive_with_slower_fills'], flush=True)
    print('Positive with doubled fees:', r['positive_with_double_fees'], flush=True)
    print('Completed sales:', r['normal']['sells'], flush=True)
    print(f"Low-price stress decline: {r['independent_ledger']['low_price_stress_decline_pct']:.2f}% "
          '(not exact tick-level drawdown).', flush=True)
    print('Independent accounting verified for every scenario.', flush=True)
    print(report['limitations'], flush=True)
    print('Report:', ROOT / 'daily-breakout-validation-results.json', flush=True)


def run(download):
    report_path = ROOT / 'daily-breakout-validation-results.json'
    source_hashes = hashes()
    if source_hashes['daily_breakout_research.py'] != DAILY_SHA256:
        raise RuntimeError('Daily strategy source differs from the successful training version')
    identity = dict(protocol=PROTOCOL, protocol_hash=history.digest(PROTOCOL), source_hashes=source_hashes)
    if report_path.exists():
        report = json.loads(report_path.read_text())
        if any(history.digest(report[k]) != history.digest(v) for k, v in identity.items()):
            raise RuntimeError('Saved validation belongs to different code/rules; preserve it.')
        print('Reading saved validation; not a new independent test.', flush=True)
        display(report)
        return report
    print('FROZEN RESERVED VALIDATION | hypothesis 8 | no tuning or real trades', flush=True)
    original = json.loads((ROOT / 'daily-breakout-results.json').read_text())
    if original['protocol'] != TRAIN_PROTOCOL:
        raise RuntimeError('Training settings differ from the approved 60/30 test')
    train = cached.cached_training(ROOT)
    if history.digest(train) != original['training_data']['sha256']:
        raise RuntimeError('Training data fingerprint changed')
    reproduced = daily.simulate(train)
    ledger.replay(train, 0, reproduced)
    for key in ('profit', 'realized', 'open_net_profit', 'fees_paid', 'buys', 'sells', 'max_drawdown_pct'):
        if not math.isclose(reproduced[key], original['result'][key], abs_tol=1e-7):
            raise RuntimeError('Successful training result not reproduced: ' + key)
    if reproduced['profit'] <= 0:
        raise RuntimeError('The training candidate is not profitable')
    identity.update(training_data=original['training_data'], training_result=model.brief(reproduced))
    manifest = freeze(ROOT, identity)
    print('Exact source, rules, and training result verified and frozen.', flush=True)
    print('Now downloading Jan 2024–Apr 29, 2026; first download may take several minutes.', flush=True)
    start, end = map(history.stamp, (PROTOCOL['evaluation_start'], PROTOCOL['evaluation_end_exclusive']))
    reserved = download(start, end)
    if reserved[0][0] < start or reserved[-1][0] >= end:
        raise ValueError('Evaluation prices fall outside the reserved period')
    warm = [row for row in train if row[0] >= start - 70 * 86400]
    rows = warm + reserved
    result = evaluation(rows, len(warm))
    report = dict(identity, manifest_sha256=history.digest(manifest),
                  evaluation_data=history.metadata(reserved, start, end), evaluation=result,
                  limitations='This interval was excluded from earlier strategy selection. '
                  'After this run it is examined history, not a reusable untouched test. '
                  'Hypothesis 8 follows seven failed approaches; repeated-testing bias and a small '
                  'trade count remain concerns. Gains on unsold holdings are not spendable realized income. '
                  'Historical profit does not establish future income; future paper observation remains necessary. '
                  'USD, before taxes/GIS/CAD conversion; cash interest excluded. Actual costs and fills may differ. '
                  'No service settings changed and no real trades placed.')
    data.atomic_json(report_path, report)
    display(report)
    return report


def main():
    data.BASE = ROOT
    with patch.object(data.urllib.request, 'urlopen', history.RateLimiter(data.urllib.request.urlopen)):
        run(data.download)


if __name__ == '__main__':
    main()
