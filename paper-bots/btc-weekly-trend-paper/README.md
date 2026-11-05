# Weekly close / 200-day-average paper bot

Current Pi source supplied on 2026-11-05. Uses public Coinbase BTC/USD GETs.
At a fresh completed Sunday UTC close, hold BTC above the average of 200
completed daily closes (including Sunday), and cash below it. Equal prices
cause no position change. One position up to $150 in a simulated $1,000 account.
No intraday stop; fresh weekly decisions only, with a 15-minute signal-age gate.

This research strategy CAN sell at a loss. It has no Overton profit-only guard.
Fees are 1.2% and adverse slippage 0.05% per side. Both equity and its $150 BTC
plus $850 cash holding benchmark include estimated exit costs. A daily record
requires the final five-minute closing bar; missing earlier bars do not invalidate
that close. All 200 required daily closes must be valid for the weekly average.
The adjacent `btc-breakout-paper/candle-cache/` may supply matching validated
data; otherwise it downloads public candles into its own cache. No keys required.

```bash
python3 trend_paper.py --status
python3 trend_paper.py --once
python3 trend_paper.py
python3 -m unittest -v test_weekly
```

The explicit `--migrate-close-policy` action backs up older state and preserves
ledger/processed signals. Publication does not run it, reset an account, or alter
the existing service. Cached prices and state remain outside the repository.
