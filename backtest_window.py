#!/usr/bin/env python3
"""Read-only market research; never imports, edits, or runs the paper service.

Uses completed Coinbase BTC-USD five-minute candles. Signals use the previous
close; fills use the next open plus adverse slippage. This is an approximation
of the bot's three-minute spot checks, not a tick-accurate execution replay.
"""
import argparse
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

FEE = 0.012
SLIP = 0.0005  # Assumption: 0.05% adverse execution per side, beyond fees.
TRADE = 50.0
CAP = 150.0
START = 1000.0
BAR = 300
BASE = Path(__file__).resolve().parent


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def validate(rows, start, end):
    by_time = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError('Malformed Coinbase candle')
        raw = float(row[0])
        stamp = int(raw)
        values = list(map(float, row[1:6]))
        low, high, opening, close, volume = values
        if raw != stamp or stamp % BAR or not all(math.isfinite(x) for x in values):
            raise ValueError('Invalid candle timestamp or nonfinite value')
        if low <= 0 or not low <= min(opening, close) <= max(opening, close) <= high or volume < 0:
            raise ValueError('Invalid OHLC candle')
        if start <= stamp < end:
            parsed = [stamp, *values]
            if stamp in by_time and by_time[stamp] != parsed:
                raise ValueError('Conflicting duplicate candles')
            by_time[stamp] = parsed
    expected = list(range(start, end, BAR))
    missing = [stamp for stamp in expected if stamp not in by_time]
    if not by_time:
        raise ValueError(f'No candles returned for {iso(start)} through {iso(end)}')
    # Coinbase can have missing intervals. Preserve the gaps, never invent prices.
    return [by_time[stamp] for stamp in expected if stamp in by_time]


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, allow_nan=False))
    temporary.replace(path)


def download(start, end):
    cache = BASE / 'backtest-data'
    cache.mkdir(exist_ok=True)
    rows = []
    starts = list(range(start, end, BAR * 299))
    chunks = len(starts)
    def fetch_batch(left):
        right = min(end, left + BAR * 299)
        path = cache / f'coinbase-BTC-USD-5m-{left}-{right}.json'
        if path.exists():
            part = validate(json.loads(path.read_text()), left, right)
        else:
            query = urllib.parse.urlencode(dict(granularity=BAR, start=iso(left), end=iso(right)))
            request = urllib.request.Request(
                'https://api.exchange.coinbase.com/products/BTC-USD/candles?' + query,
                headers={'User-Agent': 'paper-window-research/1.0', 'Accept': 'application/json'})
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        payload = json.load(response)
                    part = validate(payload, left, right)
                    break
                except (urllib.error.URLError, TimeoutError):
                    if attempt == 3:
                        raise
                    time.sleep(2 ** attempt)
            atomic_json(path, part)
            time.sleep(0.15)
        return part
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_batch, left) for left in starts]
        for index, future in enumerate(as_completed(futures), 1):
            rows.extend(future.result())
            if index == 1 or index % 15 == 0 or index == chunks:
                print(f'Price history: {index}/{chunks} batches', flush=True)
    rows = validate(rows, start, end)
    coverage = len(rows) / ((end - start) // BAR)
    if coverage < .99:
        raise ValueError(f'Only {coverage:.2%} candle coverage; refusing an unreliable test')
    print(f'Data coverage {coverage:.3%}; {int((end-start)//BAR)-len(rows)} missing five-minute candles')
    return rows


def simulate(rows, drop, rise):
    cash, btc, cost = START, 0.0, 0.0
    anchor = rows[0][4]
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
            pending = None  # A stale signal must not trade across a data gap.
        if gap > 900:
            pause_until = row[0] + 86400  # Match the bot's continuous-data safeguard.
        paused = row[0] < pause_until
        paused_bars += int(paused)
        opening, close = row[3], row[4]
        # Execute a signal using only a subsequent price, never the candle high/low.
        if not paused and pending == 'BUY' and cash >= TRADE and cost + TRADE <= CAP:
            fill = opening * (1 + SLIP)
            fee = TRADE * FEE
            btc += (TRADE - fee) / fill
            cash -= TRADE
            cost += TRADE
            fees += fee
            buys += 1
            anchor = fill
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
        pending = None
        liquidation = btc * close * (1 - SLIP) * (1 - FEE)
        equity = cash + liquidation
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
        if not paused and close <= anchor * (1 - drop) and cash >= TRADE and cost + TRADE <= CAP:
            pending = 'BUY'
        elif not paused and close >= anchor * (1 + rise) and btc > 0 and liquidation > cost:
            pending = 'SELL'
    return dict(end_value=equity, profit=equity - START, buys=buys, sells=sells,
                fees_paid=fees, realized=realized, unsold_btc=btc, open_cost=cost,
                open_net_profit=liquidation - cost, max_drawdown_pct=drawdown * 100,
                paused_bars_after_gaps=paused_bars)


def hold(rows, amount):
    btc = amount * (1 - FEE) / (rows[0][4] * (1 + SLIP))
    end = START - amount + btc * rows[-1][4] * (1 - SLIP) * (1 - FEE)
    return dict(end_value=end, profit=end - START)


def describe(label, result):
    print(f"{label}: ending ${result['end_value']:.2f}; net ${result['profit']:+.2f}; "
          f"{result['buys']} buys / {result['sells']} sells; "
          f"max drawdown {result['max_drawdown_pct']:.2f}%; "
          f"unsold cost ${result['open_cost']:.2f}", flush=True)


def research(rows):
    split = rows[0][0] + 120 * 86400
    train = [row for row in rows if row[0] < split]
    test = [row for row in rows if row[0] >= split]
    if len(test) < 2:
        raise ValueError('Not enough data')
    candidates = [(d, r) for d in (.005, .01, .02) for r in (.03, .04, .05)]
    training = [(d, r, simulate(train, d, r)) for d, r in candidates]
    # Choose using only the earlier 120 days. The later 60 days never pick settings.
    eligible = [row for row in training if row[2]['sells'] >= 3]
    selected = max(eligible, key=lambda item: item[2]['profit']) if eligible else None
    print('\nTRAINING: earlier 120 days; nine predeclared settings')
    for d, r, result in training:
        describe(f'-{d:.1%} / +{r:.1%}', result)
    print('\nHOLDOUT: latest 60 days; fresh $1,000 start, chosen settings frozen')
    current = simulate(test, .01, .04)
    describe('Current -1% / +4%', current)
    chosen = None
    if selected:
        d, r, _ = selected
        chosen = simulate(test, d, r)
        describe(f'Training choice -{d:.1%} / +{r:.1%}', chosen)
    else:
        print('No training setting completed three sales; no candidate selected.')
    baselines = {str(amount): hold(test, amount) for amount in (150, 1000)}
    for amount, result in baselines.items():
        print(f"Hold ${amount} BTC, keep rest in cash: ending ${result['end_value']:.2f}; net ${result['profit']:+.2f}")
    print('All cash: ending $1000.00; net $+0.00 (no interest modeled)')
    print('\nCURRENT RULES: full 180 days and latest 90 days (descriptive, not extra holdouts)')
    full = simulate(rows, .01, .04)
    recent_start = rows[-1][0] + BAR - 90 * 86400
    recent = simulate([row for row in rows if row[0] >= recent_start], .01, .04)
    describe('Full 180 days', full)
    describe('Latest 90 days', recent)
    print('\nAll ending values include hypothetical exit fees/slippage on unsold BTC.')
    print('Fees: 1.2% each way, plus assumed 0.05% slippage each way; $50 buys, $150 cap.')
    print('Five-minute close signals / next-open fills approximate three-minute spot polling.')
    print('Each period starts fresh after the live bot\'s 24h warm-up; no interest or taxes.')
    print('Missing candles are not filled. Gaps over 15 minutes pause trading for 24 hours.')
    print('Historical results cannot establish future profitability. No service settings changed.')
    return dict(training=training, selected_settings=selected[:2] if selected else None,
                holdout_current=current, holdout_selected=chosen, holdout_baselines=baselines,
                full_current=full, recent_current=recent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--end', help='Exclusive UTC end date YYYY-MM-DD; default today')
    args = parser.parse_args()
    end_dt = (datetime.strptime(args.end, '%Y-%m-%d').replace(tzinfo=timezone.utc)
              if args.end else datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
    if end_dt > datetime.now(timezone.utc):
        raise ValueError('End date cannot be in the future')
    start = int((end_dt - timedelta(days=180)).timestamp())
    end = int(end_dt.timestamp())
    print(f'RESEARCH ONLY | BTC-USD | {iso(start)} through {iso(end)} (exclusive)', flush=True)
    rows = download(start, end)
    report = research(rows)
    report.update(source='Coinbase Exchange BTC-USD candles', start=iso(start), end=iso(end),
                  fee_per_side=FEE, slippage_per_side=SLIP, bar_seconds=BAR,
                  train_days=120, holdout_days=60, candles=len(rows),
                  missing_candles=(end-start)//BAR-len(rows),
                  coverage=len(rows)/((end-start)//BAR))
    output = BASE / 'backtest-data' / f'results-{end_dt.date()}.json'
    atomic_json(output, report)
    print(f'Full report saved: {output}', flush=True)


if __name__ == '__main__':
    main()
