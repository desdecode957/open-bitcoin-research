#!/usr/bin/env python3
"""Research-only approximation of Freqtrade ReinforcedAverageStrategy.
Never imports, invokes or changes trading services. Uses existing cached Coinbase BTC/USD 5m candles.
4-hour EMA(8)/EMA(21); completed 48-hour SMA(50) filter, with preceding history for warmup.
Simulated 20% protective stop / 50% ROI target, next-4h-open entries / crossover exits.
Intrabar stop/ROI testing based on 4h OHLC; stop wins if both crossed within one bar.
Uses 0.2% fee and 0.05% adverse slippage each side; spread not verified for NDAX.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / 'backtest-data'
FEE, SLIP, SIZE = .002, .0005, 150.0
START_YEAR, END_YEAR = 2022, 2025

def ts_year(y): return int(datetime(y,1,1,tzinfo=timezone.utc).timestamp())

def load_4h():
    by_time = {}
    paths = sorted(ROOT.glob('coinbase-BTC-USD-5m-*.json'))
    for path in paths:
        for row in json.loads(path.read_text()):
            t = int(row[0])
            if not ts_year(2021) <= t < ts_year(2026): continue
            bar = (t // 14400) * 14400
            entry = by_time.setdefault(bar, {})
            entry[t] = row
    result = []
    for bar, samples in sorted(by_time.items()):
        if len(samples) != 48 or min(samples) != bar or max(samples) != bar+14100:
            continue
        seq = [samples[t] for t in sorted(samples)]
        result.append((bar, float(seq[0][3]), max(float(r[2]) for r in seq),
                       min(float(r[1]) for r in seq), float(seq[-1][4]),
                       sum(float(r[5]) for r in seq)))
    return result, len(paths)

def ema(prices, span):
    a = 2/(span+1)
    out = []; last = None
    for p in prices:
        last = p if last is None else a*p + (1-a)*last
        out.append(last)
    return out

def main():
    bars, n_files = load_4h()
    if not bars: raise SystemExit('No complete 4h candles found')
    print(f'RESEARCH ONLY | {n_files} cached files | {len(bars):,} complete four-hour candles')
    print('4h EMA8/EMA21 cross, completed 48h SMA50 filter; stop -20%, ROI +50%')
    print('Estimated 0.20% fees + 0.05% slippage per side; NDAX spread not verified')
    print('Research approximation, NOT the Freqtrade engine; four bots untouched')
    closes = [x[4] for x in bars]
    fast, slow = ema(closes,8), ema(closes,21)
    # 48-hour bars: 12 four-hour slots; SMA50 only from completed periods.
    groups = {}
    for i, row in enumerate(bars):
        key = row[0]//172800
        groups.setdefault(key, []).append((i,row))
    completed = {}
    for key, entries in groups.items():
        if len(entries)==12 and [r[0] for _,r in entries] == [key*172800+k*14400 for k in range(12)]:
            completed[key]=entries[-1][1][4]
    sma_by_key = {}
    valid = sorted(completed)
    for i,key in enumerate(valid):
        if i>=49 and valid[i-49:i+1] == list(range(key-49,key+1)):
            sma_by_key[key] = sum(completed[z] for z in range(key-49,key+1))/50

    for year in range(START_YEAR,END_YEAR+1):
        start,end = ts_year(year),ts_year(year+1)
        indices = [i for i,r in enumerate(bars) if start <= r[0] < end]
        if len(indices)<1200:
            print(f'{year}: insufficient history, skipped'); continue
        cash=1000.0; qty=0.; cost=0.; entry_price=0.
        buys=sells=wins=0; peak=cash; mdd=0.; pending=None; fee_paid=0.
        # One signal per complete bar; fill following bar's open.
        for i in indices:
            t,opening,high,low,close,volume = bars[i]
            if pending=='BUY' and qty==0 and cash>=SIZE:
                entry_price=opening*(1+SLIP)
                qty=SIZE*(1-FEE)/entry_price; cash-=SIZE; cost=SIZE
                fee_paid+=SIZE*FEE; buys+=1
            elif pending=='SELL' and qty>0:
                proceeds=qty*opening*(1-SLIP)*(1-FEE)
                fee_paid += qty*opening*(1-SLIP)*FEE
                cash+=proceeds; wins += int(proceeds>cost); sells+=1
                qty=cost=0.
            pending=None
            if qty>0:
                # Intrabar protection. A gap can make stop fills worse than stop price.
                stop=entry_price*.8
                target=entry_price*1.5
                fill=None
                if low<=stop: fill=min(opening,stop)*(1-SLIP)
                elif high>=target: fill=max(opening,target)*(1-SLIP)
                if fill is not None:
                    proceeds=qty*fill*(1-FEE)
                    fee_paid+=qty*fill*FEE
                    cash+=proceeds; wins+=int(proceeds>cost); sells+=1
                    qty=cost=0.
            equity=cash+qty*close*(1-SLIP)*(1-FEE)
            peak=max(peak,equity); mdd=max(mdd,100*(1-equity/peak))
            if i<1 or i==indices[-1]: continue
            # Only use last FINISHED 48h bucket, avoiding lookahead.
            group=t//172800
            long_sma=sma_by_key.get(group-1)
            cross_up=fast[i]>slow[i] and fast[i-1]<=slow[i-1]
            cross_dn=slow[i]>fast[i] and slow[i-1]<=fast[i-1]
            if qty==0 and long_sma is not None and close>long_sma and volume>0 and cross_up:
                pending='BUY'
            elif qty>0 and volume>0 and cross_dn:
                pending='SELL'
        # Paper year-end hypothetical liquidation for comparable result.
        if qty:
            proceeds=qty*bars[indices[-1]][4]*(1-SLIP)*(1-FEE)
            cash+=proceeds; wins+=int(proceeds>cost); sells+=1
        first,last=bars[indices[0]],bars[indices[-1]]
        hold_btc=SIZE*(1-FEE)/(first[1]*(1+SLIP))
        hold_net=hold_btc*last[4]*(1-SLIP)*(1-FEE)-SIZE
        print(f'{year}: strategy ${cash-1000:+.2f} | hold ${hold_net:+.2f} | {buys} entries, '
              f'{wins} winning exits / {sells} exits | max drawdown {mdd:.2f}%')
    print('NOTE: Approximate fills, incomplete 4h bars omitted, and cached Coinbase USD pricing.')
    print('NDAX fees/spread must be confirmed. Annual portfolios restart at $1,000 each year.')
    print('No bot services or settings modified.')

if __name__=='__main__': main()
