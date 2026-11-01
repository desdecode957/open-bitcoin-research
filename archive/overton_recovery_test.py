#!/usr/bin/env python3
"""Research only: compare original Overton with buying after a rebound.
Place beside backtest_window.py. Does not edit or import any paper service.
"""
import argparse
from datetime import datetime, timedelta, timezone
from backtest_window import (BAR, CAP, FEE, SLIP, START, TRADE, download,
                             simulate, describe)

DROP = .01
RISE = .04
BOUNCES = (.0025, .005, .01)  # Fixed in advance: 0.25%, 0.5%, 1%


def recovery_simulate(rows, bounce):
    cash, btc, cost = START, 0.0, 0.0
    anchor = rows[0][4]
    armed_low = None
    pending = None
    buys = sells = 0
    fees = realized = drawdown = 0.0
    peak = START
    previous_time = rows[0][0]
    pause_until = 0
    paused_bars = 0
    for row in rows[1:]:
        gap = row[0] - previous_time
        previous_time = row[0]
        if gap > BAR:
            pending = None
            armed_low = None  # Confirmation requires uninterrupted prices.
        if gap > 900:
            pause_until = row[0] + 86400
        paused = row[0] < pause_until
        paused_bars += int(paused)
        opening, close = row[3], row[4]
        if not paused and pending == 'BUY' and cash >= TRADE and cost + TRADE <= CAP:
            fill = opening * (1 + SLIP)
            fee = TRADE * FEE
            btc += (TRADE - fee) / fill
            cash -= TRADE
            cost += TRADE
            fees += fee
            buys += 1
            anchor = fill
            armed_low = None
        elif not paused and pending == 'SELL' and btc > 0:
            fill = opening * (1 - SLIP)
            gross = btc * fill
            fee = gross * FEE
            profit = gross - fee - cost
            if profit > 0:
                cash += gross - fee
                realized += profit
                fees += fee
                btc = cost = 0.0
                sells += 1
                anchor = fill
                armed_low = None
        pending = None
        liquidation = btc * close * (1 - SLIP) * (1 - FEE)
        equity = cash + liquidation
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
        if paused:
            armed_low = None
            continue
        # Same exit as original. Do not schedule a buy when an exit is due.
        if close >= anchor * (1 + RISE) and btc > 0 and liquidation > cost:
            pending = 'SELL'
            armed_low = None
        elif cash >= TRADE and cost + TRADE <= CAP:
            threshold = anchor * (1 - DROP)
            if armed_low is None:
                if close <= threshold:
                    armed_low = close
            elif close < armed_low:
                armed_low = close
            elif close >= armed_low * (1 + bounce) and close <= threshold:
                pending = 'BUY'
                armed_low = None
            elif close > threshold:
                armed_low = None  # If dip is gone, wait for a fresh dip.
        else:
            armed_low = None
    return dict(end_value=equity, profit=equity - START, buys=buys, sells=sells,
                fees_paid=fees, realized=realized, unsold_btc=btc, open_cost=cost,
                open_net_profit=liquidation - cost, max_drawdown_pct=drawdown * 100,
                paused_bars_after_gaps=paused_bars)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end', default='2026-10-27',
                        help='Exclusive UTC end date YYYY-MM-DD (default 2026-10-27)')
    args = parser.parse_args()
    end_dt = datetime.strptime(args.end, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    if end_dt > datetime.now(timezone.utc):
        parser.error('End date cannot be in the future')
    start = int((end_dt - timedelta(days=180)).timestamp())
    end = int(end_dt.timestamp())
    print('RESEARCH ONLY — no bot/service changes')
    rows = download(start, end)
    split = rows[0][0] + 120 * 86400
    train = [row for row in rows if row[0] < split]
    test = [row for row in rows if row[0] >= split]
    print('\nEARLIER 120 DAYS — training')
    describe('Original -1%/+4%', simulate(train, DROP, RISE))
    train_results = []
    for bounce in BOUNCES:
        result = recovery_simulate(train, bounce)
        train_results.append((bounce, result))
        describe(f'Recovery {bounce:.2%}', result)
    eligible = [(b, r) for b, r in train_results if r['sells'] >= 3]
    selected = max(eligible, key=lambda pair: pair[1]['profit'])[0] if eligible else None
    print('\nLATER 60 DAYS — untouched holdout')
    describe('Original -1%/+4%', simulate(test, DROP, RISE))
    if selected is None:
        print('No recovery choice made: none completed 3 sales during training.')
    else:
        print(f'Recovery threshold chosen using training only: {selected:.2%}')
        describe(f'Recovery {selected:.2%}', recovery_simulate(test, selected))
    print('\nCaution: 60-day results are not proof of future profitability.')
    print('All values include 1.2% fees and 0.05% slippage per side,')
    print('$50 entries, $150 exposure cap, and hypothetical exit costs.')
    print('No existing file or bot settings were modified.')


if __name__ == '__main__':
    main()
