#!/usr/bin/env python3
"""RESEARCH ONLY. Compare a fixed BTC momentum rule with buy-and-hold.

Never contacts the network or imports/changes any trading bot service.
Rules fixed before this run: at a completed UTC daily close, if BTC's
30-day return crosses from below +10% to at least +10%, buy $150 at the
NEXT daily open; exit 30 completed daily bars later at the NEXT daily open.
No overlapping positions. Costs: 1.2% fee + 0.05% adverse slippage per side.
Each calendar year independently starts with $1,000; prior 30 days provide
warm-up only. Positions still open at year-end are hypothetically liquidated.
This is a research approximation, not a production bot or verified edge.
"""
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'backtest-data'
FEE, SLIP = 0.012, 0.0005
ENTRY = 150.0
INITIAL = 1000.0
LOOKBACK = 30
HOLD = 30
YEARS = (2022, 2023, 2024, 2025)


def day_of(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).date()


def load_daily():
    # Existing cache files are already downloaded from Coinbase by earlier work.
    files = sorted(ROOT.glob('coinbase-BTC-USD-5m-*.json'))
    if not files:
        raise RuntimeError('No cached price data found')
    seen = {}
    for path in files:
        try:
            with path.open() as fh:
                candles = json.load(fh)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f'Cannot read {path.name}: {exc}') from exc
        for r in candles:
            if not isinstance(r, list) or len(r) < 6:
                raise RuntimeError(f'Malformed candle in {path.name}')
            stamp = int(r[0])
            if stamp % 300:
                raise RuntimeError(f'Invalid candle timestamp in {path.name}')
            # Coinbase format: time, low, high, open, close, volume
            candle = (float(r[3]), float(r[4]))
            if candle[0] <= 0 or candle[1] <= 0:
                raise RuntimeError('Nonpositive BTC price')
            if stamp in seen and seen[stamp] != candle:
                raise RuntimeError(f'Conflicting data at {stamp}')
            seen[stamp] = candle
    groups = defaultdict(list)
    for stamp, (opening, close) in seen.items():
        groups[day_of(stamp)].append((stamp, opening, close))
    daily = {}
    for day, bars in groups.items():
        bars.sort()
        # Demand a complete UTC day and valid boundary candles to avoid
        # mistaking a late start or missing close for an executable signal.
        if len(bars) != 288:
            continue
        first = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
        if bars[0][0] != first or bars[-1][0] != first + 287 * 300:
            continue
        daily[day] = (bars[0][1], bars[-1][2])  # open, close
    return daily, len(files), len(seen)


def net_sell(btc, price):
    return btc * price * (1 - SLIP) * (1 - FEE)


def btc_bought(spend, price):
    return spend * (1 - FEE) / (price * (1 + SLIP))


def run_year(year, daily):
    dates = sorted(d for d in daily if d.year <= year and d.year >= year - 1)
    days_in_year = sorted(d for d in daily if d.year == year)
    calendar_days = 366 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 365
    if len(days_in_year) < .90 * calendar_days:
        return None, f'{len(days_in_year)}/{calendar_days} complete UTC days (below 90%)'
    # Only allow a signal after 30 *consecutive* preceding days of valid data.
    positions = {d: i for i, d in enumerate(dates)}
    momentum = {}
    for i in range(LOOKBACK, len(dates)):
        segment = dates[i - LOOKBACK:i + 1]
        if (segment[-1] - segment[0]).days != LOOKBACK:
            continue
        momentum[dates[i]] = daily[dates[i]][1] / daily[dates[i-LOOKBACK]][1] - 1

    cash, btc = INITIAL, 0.0
    buys = sells = 0
    realized = 0.0
    entry_day = None
    entry_price = None
    costs = 0.0
    equity_peak, max_dd = INITIAL, 0.0
    for idx, day in enumerate(days_in_year):
        opening, close = daily[day]
        # First execute orders scheduled by yesterday's completed close.
        # Store pending signal in local state below.
        if idx == 0:
            pending_buy = False
            pending_sell = False
        if pending_sell and btc:
            proceeds = net_sell(btc, opening)
            cash += proceeds
            realized += proceeds - ENTRY
            costs += btc * opening * (1 - SLIP) * FEE
            btc = 0.0
            sells += 1
            entry_day = None
        elif pending_buy and not btc and cash >= ENTRY:
            btc = btc_bought(ENTRY, opening)
            cash -= ENTRY
            costs += ENTRY * FEE
            buys += 1
            entry_day = day
            entry_price = opening
        pending_buy = pending_sell = False
        liquidation = net_sell(btc, close) if btc else 0.0
        equity = cash + liquidation
        equity_peak = max(equity_peak, equity)
        max_dd = max(max_dd, (equity_peak - equity) / equity_peak)
        if btc:
            if (day - entry_day).days >= HOLD - 1:
                pending_sell = True
        else:
            current = momentum.get(day)
            previous = momentum.get(dates[positions[day] - 1]) if positions[day] > 0 else None
            if current is not None and previous is not None and previous < .10 <= current:
                pending_buy = True
        # Do not carry overnight orders into a different calendar year.
        if idx == len(days_in_year) - 1:
            pending_buy = pending_sell = False
    if btc:
        # Hypothetical exit at year-end close, including liquidation costs.
        proceeds = net_sell(btc, daily[days_in_year[-1]][1])
        realized += proceeds - ENTRY
        cash += proceeds
        sells += 1
    # Baseline uses identical $150 notional, year-start buy and year-end exit.
    hold_btc = btc_bought(ENTRY, daily[days_in_year[0]][0])
    hold_value = INITIAL - ENTRY + net_sell(hold_btc, daily[days_in_year[-1]][1])
    return dict(profit=cash - INITIAL, buys=buys, sells=sells,
                hold_profit=hold_value - INITIAL, drawdown=max_dd * 100,
                complete_days=len(days_in_year), fees_paid=costs), None


def main():
    daily, files, bars = load_daily()
    print(f'RESEARCH ONLY | {files:,} cached files | {bars:,} unique 5-minute bars')
    print('Rule: cross above +10% trailing 30 days; buy next UTC day open; exit after 30 days.')
    print('One $150 position; $1,000 starting cash; 1.2% fees and 0.05% slippage per side.')
    print('Year-end positions hypothetically liquidated. No bot services changed.\n')
    totals = [0.0, 0.0]
    complete = 0
    for year in YEARS:
        result, reason = run_year(year, daily)
        if result is None:
            print(f'{year}: SKIPPED — {reason}')
            continue
        complete += 1
        totals[0] += result['profit']
        totals[1] += result['hold_profit']
        print(f"{year}: momentum ${result['profit']:+.2f} ({result['buys']} buys / {result['sells']} exits; "
              f"drawdown {result['drawdown']:.2f}%) | hold $150 BTC ${result['hold_profit']:+.2f} "
              f"| {result['complete_days']} complete days")
    if complete:
        print(f'\nSum of independently reset annual profits: momentum ${totals[0]:+.2f}; '
              f'hold ${totals[1]:+.2f} (NOT a compounded live portfolio).')
    print('Caution: incomplete-day exclusions, end-of-year liquidations, and trading at next daily open')
    print('make this a simplified backtest. Check rules and out-of-sample performance before use.')


if __name__ == '__main__':
    main()
