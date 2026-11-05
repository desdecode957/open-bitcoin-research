# Rolling 24-hour Overton paper bot

This snapshot was supplied from the Pi on 2026-11-05. It uses public Coinbase
BTC/USD ticker GETs, 160 simulated USD, $50 total-cost buys and three buy slots
($150 total). Buy at 1% below the arithmetic mean of retained 24-hour samples;
sell at 4% above it only if net proceeds minus a 0.5% gross-sale buffer exceed
purchase costs. A 1.2% fee is assumed each way; buys use budget/(1+fee).
No order API or trading credentials are used, and no slippage is modeled.

Readiness requires 24 elapsed hours since the first sample and at least 100
samples still inside the recent window. This implementation does not enforce a
maximum intersample gap; "24 hours" here is not a strict continuous-data check.
It has no process lock, so use only one instance per state file. Its loop catches
all exceptions, including save failures; this is a paper research snapshot, not
reviewed live-execution infrastructure. Open BTC is marked before exit costs;
no buy-and-hold benchmark is printed by this version.

The private snapshot keeps the deployed `Path.home()` state path. The public
research copy stores `rolling_state.json` beside its script so it cannot open
the deployed Pi's home-level state by default. Only this state-path difference
is made in the public copy; trading rules are preserved.
