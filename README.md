# Open Bitcoin Research

Our mission:
Develop and openly share Bitcoin trading research
that ordinary people can understand and verify.

Our principles:
1. Protect capital before chasing profits.
2. Test every strategy with realistic fees.
3. Compare results against buying and holding Bitcoin.
4. Publish failures as well as successes.
5. Never promise guaranteed profits.
6. Keep research separate from real trading.

Our first goal:
Find a repeatable trading advantage that survives
independent testing.

Current status:
Research only. No proven profitable strategy yet.

## Reproducing the research

Requires Python 3 and an internet connection to download data.
No live trading account or API keys are required.

### 1. Download historical Bitcoin prices

python3 download_training_data.py

This downloads Coinbase BTC-USD five-minute candles
for January 2022 through December 2023.

The downloader checks the historical data fingerprint.
If Coinbase's records differ from our original dataset,
verification will fail rather than silently accept changes.

### 2. Verify the original breakout results

python3 verify_research.py

Or, if your historical data is in another folder:

python3 verify_research.py /path/to/data-folder

Expected historical training results:
- Net profit: +$90.02 USD
- Maximum drawdown: 4.56%
- Buys: 3
- Sells: 2

### 3. Regenerate the strategy scoreboard

python3 generate_scoreboard.py

All results are historical simulations.
No strategy has demonstrated reliable future profitability.

### Current reproducibility status

Verified using the original cached historical data.
Fresh downloads and clean-computer installation are not yet tested.
Some additional research scripts still require saved files not included here.

## Project maturity

Verified:
- Automatic strategy scoreboard
- Historical Bitcoin data downloader
- Daily breakout 60/30 reproduction using freshly downloaded data
- Equal-cost historical comparisons with weekly SMA200

Still experimental:
- Additional research and audit programs
- Independent reproduction on a different computer
- Forward paper-trading results

No strategy is proven to produce reliable future profits.
This repository is research software, not a live trading system.

## Forward paper-bot experiments

The [paper-bots](paper-bots/README.md) folder contains moving-reference, rolling, sustained-price-shift, NDAX BTC/CAD,
daily breakout and weekly trend simulations, with synthetic offline tests.
Contributions and independently checked improvements are welcome. These are
forward experiments, not additional validated historical results.
