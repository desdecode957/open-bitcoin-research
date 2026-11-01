#!/usr/bin/env python3
"""Download historical BTC-USD training data for research only."""

from pathlib import Path
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
    import json

    actual = history.digest(rows)
    report = json.loads(
        (Path(__file__).resolve().parent / "reports/training-breakout.json").read_text()
    )
    expected = report["training_data"]["sha256"]

    print("Data fingerprint:", actual)
    if actual != expected:
        raise SystemExit("FAIL: Historical data differs from original research")
    print("PASS: Historical data matches original research")
