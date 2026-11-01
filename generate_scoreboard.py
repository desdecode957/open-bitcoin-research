#!/usr/bin/env python3
"""Generate a historical research scoreboard from saved results.

Read-only source reports. No bot access, trades, or downloads.
"""
import json
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent

DAILY = HOME / "btc-long-history-20261027/daily-breakout-validation-results.json"
WEEKLY = HOME / "btc-weekly-trend-paper/research-close-valid-v2/results.json"


def load(path):
    if not path.is_file():
        raise FileNotFoundError(f"Missing research report: {path}")
    return json.loads(path.read_text())


def money(value):
    return f"{float(value):+.2f}"


def main():
    daily = load(DAILY)
    weekly = load(WEEKLY)

    d = daily["evaluation"]
    w = weekly["validation"]

    if not isinstance(w, list) or len(w) != 2:
        raise ValueError("Unexpected weekly report format")

    weekly_results = {
        item["strategy"]: item for item in w
    }

    breakout = d["normal"]
    hold = d["hold"]
    trend = weekly_results["Weekly SMA200"]

    daily_fee = 100 * daily["protocol"]["fee"]
    weekly_fee = 100 * weekly["manifest"]["protocol"]["strategy"]["fee_each_side"]

    entries = [
        ("Daily breakout 60/30", breakout, daily_fee),
        ("Weekly SMA200", trend, weekly_fee),
        ("Buy and hold (daily benchmark)", hold, daily_fee),
    ]

    lines = [
        "# Bitcoin Strategy Scoreboard",
        "",
        "Historical simulations, not real profits or financial advice.",
        "",
        "## January 2024 – April 29, 2026",
        "",
        "Starting account: $1,000 USD; maximum BTC position: $150 USD.",
        "",
        "| Strategy | Net USD | Maximum drawdown | Fee per side |",
        "|---|---:|---:|---:|",
    ]

    for name, result, fee in entries:
        lines.append(
            f"| {name} | ${money(result['profit'])} | "
            f"{result['max_drawdown_pct']:.2f}% | {fee:.2f}% |"
        )

    lines += [
        "",
        "## Limitations",
        "",
        "- Strategy results use different fee assumptions and backtesting implementations.",
        "- They are not fully comparable until tested under identical conditions.",
        "- Historical returns do not establish future profitability.",
        "- The original source reports remain unchanged.",
        "",
        "## Sources",
        "",
        "- `daily-breakout-validation-results.json` (original research folder)",
        "- `research-close-valid-v2/results.json` (weekly research folder)",
    ]

    output = ROOT / "SCOREBOARD.md"
    output.write_text("\n".join(lines) + "\n")
    print(f"Scoreboard generated: {output}")
    print(output.read_text())


if __name__ == "__main__":
    main()
