# Forward paper-bot experiments

Public-data-only simulations for comparing ways of moving a Bitcoin trading
reference. These programs have no trading credentials or real-order API.
They run on Linux/macOS with Python 3.11+ and the standard library (`fcntl` is
used for process locks). Historical research elsewhere in this repository is
unchanged. None of these strategies is established as profitable.

| Folder | Reference rule | Sell rule | Assumed costs |
| --- | --- | --- | --- |
| `moving/` | Fixed between trades; reset after each buy/sale | Reference +4%, with positive net position profit required | 1.2% per side; no spread/slippage |
| `shift/` | Previous-hour time-weighted average must be at least 1% beyond the saved reference on the same side for 30 minutes | Whole-position target of at least $2 simulated net profit | 1.2% fee +0.05% slippage per side |
| `ndax/` | Reset after trades, or manually to the latest BTC/CAD midpoint | First purchase pins the position target at its midpoint +4%; manual moves/additional buys keep it, and positive net position profit is still required | Bid/ask spread +0.20% fee +0.05% slippage per side |

Each uses $50 simulated buys with a $150 outstanding-cost cap and three buy
slots. Defaults start with 1,000 simulated units of the applicable currency.
The Coinbase experiments use USD quotes; NDAX uses CAD. The printed NDAX
USD conversion, when supplied, is a display estimate only.

The rolling-24-hour-reference bot is also being observed on the Pi, but its
current source has not yet been exported. It is intentionally not reconstructed
from logs and is not included in this publication.

## Run one simulation

Run each command in its own terminal; each folder has separate state/locks.

```bash
python3 paper-bots/moving/moving_box.py
python3 paper-bots/shift/accepted_range_paper.py
python3 paper-bots/ndax/ndax_paper.py
```

Use `--status` to read saved results without sampling or trading. NDAX requires
24 hours of sufficiently continuous samples; shift requires one continuous hour.
Gaps over 10 minutes restart shift's collection/confirmation; NDAX's gap limit
is 15 minutes. Moving's current trade-reset implementation has no warm-up gate.
The `moving/` original-bot comparison is optional and unavailable unless its
external state file exists; missing comparison data does not stop the simulation.

## Check changes offline

```bash
bash paper-bots/check.sh
```

Checks use synthetic prices/fills and require no credentials or network. Passing
them checks program behavior, not strategy profitability. API availability and
clean-host service installation are separate from these checks.

## Move NDAX's paper reference manually

Stop the running NDAX process first; the process lock prevents concurrent edits.

```bash
python3 paper-bots/ndax/ndax_paper.py --shift-window
```

Optionally append `--usd-cad RATE` with a separately checked number of CAD per
USD. No exchange rate is hardcoded in the command. The helper `shift-window.sh`
also stops/restarts a configured `ndax-overton-paper` system service; review its
service name before using it on a different host. `apply_update.py` is tested
migration/rollback support for the Pi updater, not a required research entry point.

With an open position only the next-buy reference moves; the position's sell
target, quantity and costs stay intact. Further purchases can change average
cost, so reaching the displayed sell line is not sufficient: actual simulated
net proceeds must still exceed the entire recorded position cost. The bot checks
a profitable sale before an additional buy if their eligibility overlaps.

## Fair comparisons and feedback

These are different strategies, not a controlled head-to-head result. Start time,
fees, slippage, warm-up, currencies and holding benchmarks differ. In particular,
moving/shift mark open BTC before exit costs and use a full-cash holding benchmark;
NDAX reports a bid/fee/slippage liquidation estimate and a $150-exposure holding
benchmark plus $850 cash. Do not compare their raw printed totals as a ranking.

Issues and pull requests are welcome. Include the exact rule, cost assumptions,
offline checks and a fixed-period comparison that values unsold BTC and records
drawdown. Changing a reference is not evidence that it predicts price direction.
No personal account balances, keys or operational state are published here.
