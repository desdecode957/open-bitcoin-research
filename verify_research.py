#!/usr/bin/env python3
"""Verify original historical research. No live trading."""

import json
import math
from pathlib import Path

import audit_hybrid_trades as audit
import daily_breakout_research as breakout
import long_history_research as history

source = Path.home() / "btc-long-history-20261027"
report = json.loads((source / "daily-breakout-results.json").read_text())

rows = audit.cached_training(source)

assert history.digest(rows) == report["training_data"]["sha256"], \
    "FAIL: Historical data fingerprint differs"

result = breakout.simulate(rows)
original = report["result"]

for key in ("profit", "max_drawdown_pct"):
    assert math.isclose(result[key], original[key], abs_tol=1e-8), \
        f"FAIL: {key} differs"

for key in ("buys", "sells"):
    assert result[key] == original[key], f"FAIL: {key} differs"

print("PASS: Historical data fingerprint")
print("PASS: Net profit")
print("PASS: Maximum drawdown")
print("PASS: Buy and sell counts")
print("VERIFIED: Daily breakout 60/30 historical reproduction")
