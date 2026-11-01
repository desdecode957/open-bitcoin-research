#!/usr/bin/env python3
"""Read-only BTC pattern study using cached Coinbase five-minute candles.
No downloads, trades, or modifications to bot services. Research, not a strategy backtest.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / 'backtest-data'
FEE = 0.012
SLIP = 0.0005
ROUND_TRIP_HURDLE = (1 + SLIP) / (1 - FEE) * (1 + FEE) / (1 - SLIP) - 1
# Predeclared signals: prior 30 daily closes have changed >= +10% or <= -10%.
# Compare forward 7 and 30 calendar days, using disjoint events per signal/horizon.
LOOKBACK = 30
HORIZONS = (7, 30)
SIGNALS = {'momentum': 0.10, 'dip': -0.10}


def load_daily():
    paths = sorted(ROOT.glob('coinbase-BTC-USD-5m-*.json'))
    if not paths:
        raise RuntimeError(f'No cached data found at {ROOT}')
    bars = {}
    conflicts = 0
    for path in paths:
        pieces = path.stem.split('-')
        if len(pieces) != 6 or pieces[:4] != ['coinbase', 'BTC', 'USD', '5m']:
            continue
        try:
            left, right = map(int, pieces[4:])
            chunk = json.loads(path.read_text())
        except (ValueError, OSError) as exc:
            raise RuntimeError(f'Cannot read {path.name}: {exc}') from exc
        for record in chunk:
            if not isinstance(record, list) or len(record) < 6:
                continue
            ts = int(record[0])
            if not left <= ts < right or ts % 300:
                continue
            close = float(record[4])
            if close <= 0:
                continue
            if ts in bars and bars[ts] != close:
                conflicts += 1
            else:
                bars[ts] = close
    if conflicts:
        raise RuntimeError(f'{conflicts} conflicting duplicate candles; aborting')
    # Require exact final five-minute candle of UTC day and at least 95% day's bars.
    by_day = defaultdict(dict)
    for ts, price in bars.items():
        day = ts // 86400
        by_day[day][ts % 86400] = price
    daily = {}
    for day, candles in by_day.items():
        if len(candles) >= 274 and 86100 in candles:
            daily[day] = candles[86100]
    print(f'Cache: {len(paths):,} files, {len(bars):,} unique 5-minute candles, '
          f'{len(daily):,} sufficiently complete UTC days')
    if not daily:
        raise RuntimeError('No complete daily data')
    return daily


def year_of(day):
    return datetime.fromtimestamp(day * 86400, timezone.utc).year


def median(values):
    vals = sorted(values)
    n = len(vals)
    return (vals[(n-1)//2] + vals[n//2]) / 2


def output(label, returns):
    if not returns:
        print(f'  {label}: no eligible observations')
        return
    avg = sum(returns) / len(returns)
    wins = sum(r > ROUND_TRIP_HURDLE for r in returns)
    print(f'  {label}: n={len(returns)} | average {avg:+.2%} | median {median(returns):+.2%} | '
          f'above fee/slippage hurdle {wins}/{len(returns)} ({wins/len(returns):.1%})')


def main():
    daily = load_daily()
    print('RESEARCH ONLY | completed UTC closes | no bots changed')
    print(f'Illustrative round-trip price hurdle: {ROUND_TRIP_HURDLE:.2%} '
          '(1.2% fee + 0.05% slippage per side)')
    print('Signal at day D close; forward return from D close to D+7 or D+30 close.')
    print('This is NOT executable entry/exit pricing or a net-profit backtest.')
    print('Signals were specified before this test; older 2022–23 and later 2024–25 shown separately.')
    first, last = min(daily), max(daily)
    for horizon in HORIZONS:
        print(f'\n==== {horizon}-day forward returns ====')
        for signal, threshold in SIGNALS.items():
            print(f'\n{signal.upper()} (trailing 30-day change '
                  f'{">= +10%" if signal == "momentum" else "<= -10%"})')
            events = defaultdict(list)
            last_selected = -10**12
            for day in range(first + LOOKBACK, last - horizon + 1):
                if day - LOOKBACK not in daily or day not in daily or day + horizon not in daily:
                    continue
                # All intervening days must have complete data.
                if any(d not in daily for d in range(day - LOOKBACK, day + horizon + 1)):
                    continue
                trailing = daily[day] / daily[day-LOOKBACK] - 1
                qualified = trailing >= threshold if signal == 'momentum' else trailing <= threshold
                if not qualified or day <= last_selected + horizon:
                    continue
                # Avoid overlap between successive observations for this signal/horizon.
                last_selected = day
                if year_of(day) not in (2022, 2023, 2024, 2025):
                    continue
                events[year_of(day)].append(daily[day+horizon] / daily[day] - 1)
            for year in (2022, 2023, 2024, 2025):
                output(str(year), events[year])
            output('TRAIN 2022–23', events[2022] + events[2023])
            output('LATER 2024–25', events[2024] + events[2025])
    print('\nIMPORTANT: Event studies do not capture next-open fills, waiting time, '
          'drawdowns, capital constraints or alternatives such as holding BTC.')
    print('A positive pattern is only a candidate for a separate, realistic strategy backtest.')
    print('No files written; no network requests; no bot services accessed.')


if __name__ == '__main__':
    main()
