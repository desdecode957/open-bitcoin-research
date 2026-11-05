#!/usr/bin/env python3
"""Independent Overton moving-box PAPER experiment; no trading credentials."""
import argparse
import csv
import fcntl
import json
import math
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE = BASE / "moving_state.json"
TRADES = BASE / "moving_trades.csv"
ORIGINAL_STATE = Path.home() / "btc-bot-audit" / "overton_paper_state.json"
WINDOW = 24 * 60 * 60
INTERVAL = 180
MAX_GAP = 15 * 60
BUY_DROP = 0.01
SELL_RISE = 0.04
TRADE_USD = 50.0
MAX_OPEN_BUYS = 3
MAX_POSITION_COST = TRADE_USD * MAX_OPEN_BUYS
START_CASH = 1000.0
FEE_RATE = 0.012


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def price_now():
    request = urllib.request.Request(
        "https://api.coinbase.com/v2/prices/BTC-USD/spot",
        headers={"User-Agent": "overton-moving-paper"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        price = float(json.load(response)["data"]["amount"])
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid BTC price")
    return price


def initial_state(price, timestamp):
    return dict(version=1, created_at=iso(timestamp), started_ts=timestamp,
                cash=START_CASH, btc=0.0, cost=0.0,
                benchmark_btc=START_CASH / price, buys=0, sells=0,
                fees=0.0, realized_profit=0.0, buy_armed=True,
                samples=[], last_price=price)


def original_snapshot(price, timestamp=None):
    timestamp = time.time() if timestamp is None else timestamp
    # Read contents and modification time from the same file, even if the
    # original bot atomically replaces its state while this report is running.
    with ORIGINAL_STATE.open() as handle:
        original = json.load(handle)
        updated = os.fstat(handle.fileno()).st_mtime
    age = timestamp - updated
    if age > MAX_GAP:
        raise ValueError(
            f"original Overton results are {age / 60:.1f} minutes old; "
            "comparison paused until fresh results arrive"
        )
    if age < -60:
        raise ValueError("original Overton file time is in the future; check the Pi clock")
    value = float(original["cash"]) + float(original["btc"]) * price
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Invalid original Overton value")
    return {"value": value, "created_at": original["created_at"]}


def load_state():
    if not STATE.exists():
        return None
    # A corrupt file must never silently reset the experiment.
    return json.loads(STATE.read_text())


def save_state(state):
    temporary = STATE.with_suffix(".tmp")
    with temporary.open("w") as handle:
        json.dump(state, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(STATE)


def box(state, timestamp):
    samples = state["samples"]
    if not samples:
        return None, False
    average = sum(sample[1] for sample in samples) / len(samples)
    # Fixed-window targets use the saved anchor, not 24-hour history.
    ready = True
    return average, ready


def window_bounds(state):
    anchor = state.get("window_anchor", state["last_price"])
    return anchor, anchor * (1 - BUY_DROP), anchor * (1 + SELL_RISE)


def reset_window(state, price):
    # The anchor moves only when adopting this strategy or completing a trade.
    state["window_anchor"] = price
    _, state["lower"], state["upper"] = window_bounds(state)
    state["buy_armed"] = True


def step(state, price, timestamp):
    """Update simulation only; returns an optional paper trade."""
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid BTC price")
    if state["samples"] and timestamp <= state["samples"][-1][0]:
        raise ValueError("Clock has not advanced; skipping sample")
    state["samples"] = [sample for sample in state["samples"]
                        if sample[0] >= timestamp - WINDOW]
    state["samples"].append([timestamp, price])
    state["last_price"] = price
    state["last_check"] = iso(timestamp)
    average, ready = box(state, timestamp)
    if "window_anchor" not in state:
        reset_window(state, price)
    _, lower, upper = window_bounds(state)
    state["average"] = average
    state["lower"] = lower
    state["upper"] = upper
    state["ready"] = ready
    if not ready:
        return None
    # Cost includes buy fees and resets only after a full profitable sale.
    # Deriving the allowance from cost also counts buys made before this update.
    if (price <= lower and state["cash"] >= TRADE_USD
            and state["cost"] + TRADE_USD <= MAX_POSITION_COST):
        fee = TRADE_USD * FEE_RATE
        bought = (TRADE_USD - fee) / price
        state["cash"] -= TRADE_USD
        state["btc"] += bought
        state["cost"] += TRADE_USD
        state["fees"] += fee
        state["buys"] += 1
        reset_window(state, price)
        return [iso(timestamp), "BUY", price, bought, fee, 0.0]
    if price >= upper and state["btc"] > 0:
        sold = state["btc"]
        gross = sold * price
        fee = gross * FEE_RATE
        proceeds = gross - fee
        profit = proceeds - state["cost"]
        if profit > 0:
            state["cash"] += proceeds
            state["btc"] = 0.0
            state["cost"] = 0.0
            state["fees"] += fee
            state["sells"] += 1
            state["realized_profit"] += profit
            reset_window(state, price)
            return [iso(timestamp), "SELL", price, sold, fee, profit]
    return None


def show_status(state, timestamp):
    price = state["last_price"]
    average, ready = box(state, timestamp)
    value = state["cash"] + state["btc"] * price
    hold = state["benchmark_btc"] * price
    mode = "ACTIVE" if ready else "COLLECTING 24 HOURS OF CONTINUOUS PRICES"
    print(f"PAPER ONLY | {mode} | last sample {state.get('last_check', state['created_at'])}")
    if average is not None:
        anchor, lower, upper = window_bounds(state)
        print(f"BTC ${price:,.2f} | reference ${anchor:,.2f} | "
              f"buy at/below ${lower:,.2f} | sell at/above ${upper:,.2f}")
        print("Fixed window: -1% / +4%; resets after each trade | "
              "selling requires profit after 1.2% fees each way")
        if "window_anchor" not in state:
            print("Reference pending: set from next successful price sample")
    print(f"Bot ${value:,.2f} | hold ${hold:,.2f} | "
          f"vs hold ${value-hold:+,.2f} | since start ${value-START_CASH:+,.2f}")
    print(f"{state['buys']} buys / {state['sells']} sells | "
          f"fees ${state['fees']:.2f} | realized ${state['realized_profit']:.2f}", flush=True)
    open_buys = math.ceil(state["cost"] / TRADE_USD)
    buying = ("PAUSED until profitable sale"
              if state["cost"] + TRADE_USD > MAX_POSITION_COST else "available")
    print(f"Position buys {open_buys}/{MAX_OPEN_BUYS} | "
          f"committed ${state['cost']:.2f}/${MAX_POSITION_COST:.2f} | "
          f"buying {buying}", flush=True)
    if state.get("original_start"):
        try:
            original = original_snapshot(price, timestamp)
            start = state["original_start"]
            if original["created_at"] != start["created_at"]:
                raise ValueError("Original experiment was reset")
            original_return = (original["value"] / start["value"] - 1) * 100
            moving_return = (value / START_CASH - 1) * 100
            print(f"Since moving test began ({state['created_at']}): "
                  f"original Overton {original_return:+.2f}% | "
                  f"moving box {moving_return:+.2f}%", flush=True)
        except Exception as error:
            print(f"Original comparison unavailable: {error}", flush=True)


def record(trade):
    new_file = not TRADES.exists()
    with TRADES.open("a", newline="") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow(["time", "side", "price", "btc", "fee", "profit"])
        writer.writerow(trade)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Read saved results only")
    args = parser.parse_args()
    if args.status:
        state = load_state()
        if state is None:
            print("No samples yet; start the paper service first.")
        else:
            show_status(state, time.time())
        return
    with (BASE / "moving.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Load errors stop the process rather than resetting its balances.
        state = load_state()
        print("Overton moving box: PAPER ONLY, $1,000 start, fixed -1% / +4% trade-reset window", flush=True)
        while True:
            try:
                price = price_now()
                timestamp = time.time()
                if state is None:
                    state = initial_state(price, timestamp)
                    try:
                        state["original_start"] = original_snapshot(price, timestamp)
                    except Exception as error:
                        print(f"Original start snapshot unavailable: {error}", flush=True)
                trade = step(state, price, timestamp)
            except Exception as error:
                print(f"Price/sample error: {type(error).__name__}: {error}", flush=True)
            else:
                # Storage errors stop the process; never continue with unsaved trades.
                save_state(state)
                if trade:
                    record(trade)
                    print(f"PAPER {trade[1]} | {trade[3]:.8f} BTC | profit ${trade[5]:.2f}", flush=True)
                show_status(state, timestamp)
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
