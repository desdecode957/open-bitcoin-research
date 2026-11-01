#!/usr/bin/env python3
"""Research-only, simplified TrendRider-inspired BTC spot backtest.

NOT the original TrendRider implementation. Tests EMA9/16 bullish crossover,
200-hour EMA filter, volume confirmation, then bearish crossover/6% stop/24h exit.
Uses cached Coinbase BTC-USD 5-minute candles. No network or bot access.
"""
import json
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent / 'backtest-data'
FEE, SLIP, STAKE, CASH_START = .012, .0005, 150., 1000.

def ema(prev, value, period):
    return value if prev is None else prev + 2 / (period + 1) * (value-prev)

def load_hours():
    points = {}
    for file in ROOT.glob('coinbase-BTC-USD-5m-*.json'):
        for r in json.loads(file.read_text()):
            t = int(r[0]); points[t] = r
    bars = []
    for t in sorted(points):
        hour = t - (t % 3600)
        r = points[t]
        if not bars or bars[-1]['t'] != hour:
            bars.append(dict(t=hour, first=t, last=t, o=float(r[3]), h=float(r[2]), l=float(r[1]), c=float(r[4]), v=float(r[5]), n=1))
        else:
            b=bars[-1]; b['last']=t; b['h']=max(b['h'],float(r[2])); b['l']=min(b['l'],float(r[1])); b['c']=float(r[4]); b['v']+=float(r[5]); b['n']+=1
    # Only complete 12-candle hours; do not bridge missing hours in calculations.
    return [b for b in bars if b['n']==12 and b['first']==b['t'] and b['last']==b['t']+3300]

def test(bars, year):
    # Indicators prepared across entire dataset to provide a real warm-up.
    cash=CASH_START; btc=0.; spent=0.; entry=0.; entry_time=0
    buys=sells=wins=0; peak=CASH_START; dd=0.; realized=0.
    last_time=None
    for i,b in enumerate(bars):
        if b['year'] != year: continue
        if last_time is not None and b['t']-last_time != 3600:
            # Unreliable period: close positions on next usable open, no new entry.
            if btc:
                net=btc*b['o']*(1-SLIP)*(1-FEE)
                realized += net-spent; wins += net>spent; cash+=net; btc=0; spent=0; sells+=1
            last_time=b['t']; continue
        last_time=b['t']
        # Decisions based on previous completed hour; executions at current open.
        prev=bars[i-1] if i else None
        if not prev or prev['t'] != b['t']-3600 or i<201: continue
        # 24h time exit, EMA bearish crossover, 6% stop approximated at open or intrahour low.
        if btc:
            exit_price=None
            if b['o'] <= entry*.94: exit_price=b['o']
            elif b['l'] <= entry*.94: exit_price=entry*.94
            elif b['t']-entry_time >= 24*3600 or (prev['fast'] < prev['slow'] and bars[i-2]['fast'] >= bars[i-2]['slow']): exit_price=b['o']
            if exit_price is not None:
                net=btc*exit_price*(1-SLIP)*(1-FEE)
                realized+=net-spent; wins+=net>spent; cash+=net; btc=0.; spent=0.; sells+=1
        if not btc and cash >= STAKE and i >= 201:
            before=bars[i-2]
            signal=(prev['fast'] > prev['slow'] and before['fast'] <= before['slow']
                    and prev['c'] > prev['long'] and prev['v'] > .7*prev['volavg']
                    and prev['year']==year)
            if signal:
                spent=STAKE; entry=b['o']*(1+SLIP)
                btc=(STAKE*(1-FEE))/entry; cash-=STAKE; buys+=1; entry_time=b['t']
        equity=cash+(btc*b['c']*(1-SLIP)*(1-FEE) if btc else 0)
        peak=max(peak,equity); dd=max(dd,1-equity/peak)
    # Mark-to-market at year's final close with estimated liquidation costs.
    close=next((b['c'] for b in reversed(bars) if b['year']==year),None)
    total=cash+(btc*close*(1-SLIP)*(1-FEE) if btc and close else 0)
    yr=[b for b in bars if b['year']==year]
    hold=None
    if yr:
        buy=STAKE*(1-FEE)/(yr[0]['o']*(1+SLIP))
        hold=(CASH_START-STAKE)+buy*yr[-1]['c']*(1-SLIP)*(1-FEE)-CASH_START
    return total-CASH_START, hold, buys, sells, wins, dd*100, bool(btc), len(yr)

def main():
    if not ROOT.exists(): raise SystemExit('Missing '+str(ROOT))
    hours=load_hours()
    if not hours: raise SystemExit('No cached complete hourly candles')
    fast=slow=long=None
    rolling=[]; prev_t=None
    for b in hours:
        if prev_t is None or b['t']-prev_t != 3600:
            fast=slow=long=None; rolling=[]
        fast=ema(fast,b['c'],9); slow=ema(slow,b['c'],16); long=ema(long,b['c'],200)
        rolling.append(b['v']); rolling=rolling[-20:]
        b.update(fast=fast, slow=slow, long=long, volavg=sum(rolling)/len(rolling), year=datetime.fromtimestamp(b['t'],timezone.utc).year)
        prev_t=b['t']
    print(f'RESEARCH ONLY | {len(hours):,} complete hourly candles | no bot changes')
    print('Simplified TrendRider core: EMA9/16 crossover + EMA200 + volume; 6% stop / bearish cross / 24h exit')
    print('Trades: $150 BTC spot; fees 1.2% each way; 0.05% slippage; 2022–23 development, 2024–25 later evaluation')
    print('NOT an exact TrendRider backtest. Intrahour stop fills are approximate; missing hours may affect tests.')
    for year in (2022,2023,2024,2025):
        p,h,b,s,w,dd,opened,n=test(hours,year)
        print(f'{year}: bot ${p:+.2f} | hold $150 ${h:+.2f} | {b} buys / {s} exits, {w} winning exits | drawdown {dd:.2f}% | open at end {opened} | {n} full hours')
    print('Research only. Do not deploy based on this simplified test; strategy selection after inspection is exploratory.')

if __name__=='__main__': main()
