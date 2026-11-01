#!/usr/bin/env python3
"""Download historical BTC-USD training data for research only."""

import backtest_window as data
import hybrid_research as research
import long_history_research as history

start, end = map(history.stamp, research.PROTOCOL["training"])

print("Historical Bitcoin training data")
print("Period:", data.iso(start), "to", data.iso(end))
print("Expected batches:", len(range(start, end, data.BAR * 299)))

if __name__ == "__main__":
    rows = data.download(start, end)
    print("Validated candles:", len(rows))
    print("Data fingerprint:", history.digest(rows))
    print("Compare this fingerprint with the one in reports/training-breakout.json")
