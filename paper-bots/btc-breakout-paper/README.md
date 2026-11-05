# Daily breakout 60/30 paper bot

Current Pi source supplied on 2026-11-05. Public Coinbase BTC/USD GETs only.
Starts with 1,000 simulated USD, with one position up to $150. Buy after a daily
UTC close exceeds the prior 60-day high; sell after it falls below the prior
30-day low. The signal day is excluded from the prior high/low lookback.

This research strategy CAN sell at a loss. It has no Overton profit-only guard.
It is kept unchanged to compare trend exits against the Overton approaches.
Fees are 1.2% and adverse slippage 0.05% each side. Equity and the same-budget
holding benchmark include estimated exit costs; the benchmark uses $150 in BTC
and $850 in cash. Completed days require at least 286 five-minute candles with
the first and last candle present. Signals more than 15 minutes old are skipped;
history loading refreshes clock and spot before a decision. No historical fills
are replayed. State is reconciled against its trade ledger on load/update.

```bash
python3 breakout_paper.py --status
python3 breakout_paper.py --once
python3 breakout_paper.py
python3 -m unittest -v test_breakout
```

State and `candle-cache/` are local runtime data, not repository content.
`--migrate-fees` explicitly validates/backups older fee-model state; publication
does not invoke it or change the running account.
