#!/usr/bin/env python3
"""Read-only comparison on cached historical Coinbase BTC-USD 5-minute candles.
Does not modify, import, restart or configure any paper bot service.
Fixed 0.25% recovery threshold chosen in earlier 2026 analysis; older years
are retrospective validation, not fully untouched out-of-sample data.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from backtest_window import BAR, BASE, simulate, validate, describe
from overton_recovery_test import recovery_simulate

BOUNCE = 0.0025
YEARS = (2022, 2023, 2024, 2025)

def timestamp(year):
    return int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())

def main():
    root = BASE / 'backtest-data'
    files = sorted(root.glob('coinbase-BTC-USD-5m-*.json'))
    print(f'RESEARCH ONLY | {len(files)} cache files | original vs 0.25% recovery')
    print('No downloads, no paper-bot changes. Each year starts afresh with $1,000.')
    for year in YEARS:
        start, end = timestamp(year), timestamp(year+1)
        samples = []
        matched = 0
        for path in files:
            try:
                _, _, _, _, left, right = path.stem.split('-')
                left, right = int(left), int(right)
            except (ValueError, IndexError):
                continue
            if left >= end or right <= start:
                continue
            matched += 1
            try:
                part = json.loads(path.read_text())
            except (OSError, ValueError) as e:
                print(f'{year}: failed to read {path.name}: {e}')
                return
            samples.extend(row for row in part if start <= int(row[0]) < end)
        if not samples:
            print(f'\n{year}: no cached candles available; skipped')
            continue
        try:
            rows = validate(samples, start, end)
        except ValueError as e:
            print(f'\n{year}: cache validation failed: {e}; skipped')
            continue
        total = (end-start) // BAR
        coverage = len(rows) / total
        print(f'\n{year}: {len(rows):,}/{total:,} bars ({coverage:.3%} coverage; {matched} files)')
        if coverage < .99:
            print('Less than 99% coverage: skipped, not a trustworthy comparison.')
            continue
        describe('Original -1%/+4%', simulate(rows, .01, .04))
        describe('Recovery 0.25%', recovery_simulate(rows, BOUNCE))
    print('\nIncludes 1.2% fee and 0.05% slippage per side; hypothetical liquidation cost.')
    print('Do not select a new threshold based on these years and call them untouched.')
    print('Historical tests cannot prove future profitability; no services changed.')

if __name__ == '__main__':
    main()
