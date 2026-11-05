#!/usr/bin/env python3
"""NDAX BTC/CAD PAPER experiment; public data only, no order API."""
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
STATE = BASE / "ndax_state.json"
TRADES = BASE / "ndax_trades.csv"
WINDOW = 24 * 60 * 60
INTERVAL = 180
MAX_GAP = 15 * 60
BUY_DROP = 0.01
SELL_RISE = 0.04
TRADE_CAD = 50.0
MAX_OPEN_BUYS = 3
MAX_POSITION_COST = TRADE_CAD * MAX_OPEN_BUYS
START_CASH = 1000.0
FEE_RATE = 0.002
SLIPPAGE = 0.0005
LAST_QUOTE = {}
MOVABLE_WINDOW_VERSION = 1


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def price_now():
    global LAST_QUOTE
    request = urllib.request.Request(
        "https://api.ndax.io/AP/GetL2Snapshot?OMSId=1&InstrumentId=1&Depth=100",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        book = json.load(response)
    bids = [(float(x[6]), float(x[8])) for x in book if x[9] == 0]
    asks = [(float(x[6]), float(x[8])) for x in book if x[9] == 1]
    bids = [(p, q) for p, q in bids if math.isfinite(p) and math.isfinite(q) and p > 0 and q > 0]
    asks = [(p, q) for p, q in asks if math.isfinite(p) and math.isfinite(q) and p > 0 and q > 0]
    if not bids or not asks:
        raise ValueError("Missing NDAX order book")
    bid, bid_qty = max(bids)
    ask, ask_qty = min(asks)
    if ask < bid:
        raise ValueError("Crossed order book")
    LAST_QUOTE = dict(bid=bid, ask=ask, bid_qty=bid_qty, ask_qty=ask_qty)
    return (bid + ask) / 2


def verify_market():
    request = urllib.request.Request(
        "https://api.ndax.io/AP/GetInstruments?OMSId=1",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        instruments = json.load(response)
    for item in instruments:
        row = {key.lower(): value for key, value in item.items()}
        if row.get("instrumentid") == 1:
            if row.get("product1symbol") == "BTC" and row.get("product2symbol") == "CAD":
                return
    raise ValueError("Instrument 1 is not confirmed as BTC/CAD")


def initial_state(price, timestamp):
    return dict(version=1, created_at=iso(timestamp), started_ts=timestamp,
                cash=START_CASH, btc=0.0, cost=0.0,
                benchmark_btc=MAX_POSITION_COST * (1-FEE_RATE) / (LAST_QUOTE["ask"] * (1+SLIPPAGE)), buys=0, sells=0,
                fees=0.0, realized_profit=0.0, buy_armed=True,
                samples=[], last_price=price)


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
    ready = (
        timestamp - state["started_ts"] >= WINDOW
        and samples[0][0] <= timestamp - WINDOW + MAX_GAP
        and timestamp - samples[-1][0] <= MAX_GAP
        and all(0 < b[0] - a[0] <= MAX_GAP
                for a, b in zip(samples, samples[1:]))
    )
    return average, ready


def window_bounds(state):
    anchor = state.get("window_anchor", state["last_price"])
    upper = anchor * (1 + SELL_RISE)
    if state["btc"] > 0:
        upper = state.get("position_sell_target", state.get("upper", upper))
    if not math.isfinite(upper) or upper <= 0:
        raise ValueError("Invalid position sell target")
    return anchor, anchor * (1 - BUY_DROP), upper


def pin_sell_target(state):
    """Adopt an existing position's sell line without changing its cost/history."""
    if state["btc"] > 0 and "position_sell_target" not in state:
        state["position_sell_target"] = window_bounds(state)[2]
    elif state["btc"] == 0:
        state.pop("position_sell_target", None)


def reset_window(state, price):
    # The buy reference can move while an open position keeps its sell target.
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid reference price")
    pin_sell_target(state)
    state["window_anchor"] = price
    _, state["lower"], state["upper"] = window_bounds(state)
    state["buy_armed"] = True


def move_window(state, price, timestamp):
    if not math.isfinite(timestamp) or timestamp <= 0:
        raise ValueError("Invalid reset time")
    old_anchor, _, old_sell = window_bounds(state)
    reset_window(state, price)
    state.setdefault("manual_window_moves", []).append(dict(
        time=iso(timestamp), old_reference=old_anchor, new_reference=price,
        open_position=state["btc"] > 0,
        preserved_sell_target=old_sell if state["btc"] > 0 else None))


def print_window(state, usd_cad=None):
    for label, value in zip(("Buy reference", "Next buy line", "Position sell line"), window_bounds(state)):
        converted = f" | approx US${value/usd_cad:,.2f}" if usd_cad else ""
        print(f"{label}: C${value:,.2f}{converted}")
    if usd_cad:
        print(f"USD estimates use supplied C${usd_cad:.4f} per US$1 rate.")
    if state["btc"] > 0:
        print("Open position sell target preserved. Sale still requires positive profit after spread, fees and slippage.")
    print("Price history, purchase costs, balances and the $150 cap preserved.")


def step(state, price, timestamp):
    """Update simulation only; returns an optional paper trade."""
    if not math.isfinite(price) or price <= 0:
        raise ValueError("Invalid BTC price")
    if state["samples"] and timestamp <= state["samples"][-1][0]:
        raise ValueError("Clock has not advanced; skipping sample")
    pin_sell_target(state)
    state["samples"] = [sample for sample in state["samples"]
                        if sample[0] >= timestamp - WINDOW]
    state["samples"].append([timestamp, price])
    state["last_price"] = price
    state["quote"] = dict(LAST_QUOTE)
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
    # Prefer a profitable sale if a raised buying reference makes both lines
    # eligible. A manual shift cannot postpone this existing sell target.
    if price >= upper and state["btc"] > 0 and LAST_QUOTE["bid_qty"] >= state["btc"]:
        sold = state["btc"]
        fill = LAST_QUOTE["bid"] * (1 - SLIPPAGE)
        gross = sold * fill
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
            state.pop("position_sell_target", None)
            reset_window(state, price)
            return [iso(timestamp), "SELL", fill, sold, fee, profit]
    # Cost includes buy fees and resets only after a full profitable sale.
    # Deriving the allowance from cost also counts buys made before this update.
    if (price <= lower and state["cash"] >= TRADE_CAD
            and state["cost"] + TRADE_CAD <= MAX_POSITION_COST
            and LAST_QUOTE["ask_qty"] * LAST_QUOTE["ask"] >= TRADE_CAD):
        fee = TRADE_CAD * FEE_RATE
        fill = LAST_QUOTE["ask"] * (1 + SLIPPAGE)
        bought = (TRADE_CAD - fee) / fill
        if state["btc"] == 0:
            # Match the old first-buy rule, then hold this target for the cycle.
            state["position_sell_target"] = price * (1 + SELL_RISE)
        state["cash"] -= TRADE_CAD
        state["btc"] += bought
        state["cost"] += TRADE_CAD
        state["fees"] += fee
        state["buys"] += 1
        reset_window(state, price)
        return [iso(timestamp), "BUY", fill, bought, fee, 0.0]
    return None


def show_status(state, timestamp):
    price = state["last_price"]
    average, ready = box(state, timestamp)
    quote = state.get("quote", {})
    liquidation = quote.get("bid", price) * (1-SLIPPAGE) * (1-FEE_RATE)
    value = state["cash"] + state["btc"] * liquidation
    hold = START_CASH - MAX_POSITION_COST + state["benchmark_btc"] * liquidation
    mode = "ACTIVE" if ready else "COLLECTING 24 HOURS OF CONTINUOUS PRICES"
    print(f"NDAX BTC/CAD | PAPER ONLY | {mode} | last sample {state.get('last_check', state['created_at'])}")
    if average is not None:
        anchor, lower, upper = window_bounds(state)
        print(f"BTC midpoint CAD ${price:,.2f} | reference ${anchor:,.2f} | "
              f"buy at/below ${lower:,.2f} | sell at/above ${upper:,.2f}")
        print("Movable buy reference: -1%; first-buy sell target +4%, pinned until full sale | "
              "profit required after bid/ask spread, 0.20% fees and assumed 0.05% slippage each way")
        if "window_anchor" not in state:
            print("Reference pending: set from next successful price sample")
    print(f"Bot CAD ${value:,.2f} | same-budget hold CAD ${hold:,.2f} | "
          f"vs hold ${value-hold:+,.2f} | since start ${value-START_CASH:+,.2f}")
    print(f"{state['buys']} buys / {state['sells']} sells | "
          f"fees ${state['fees']:.2f} | realized ${state['realized_profit']:.2f}", flush=True)
    open_buys = math.ceil(state["cost"] / TRADE_CAD)
    buying = ("PAUSED until profitable sale"
              if state["cost"] + TRADE_CAD > MAX_POSITION_COST else "available")
    print(f"Position buys {open_buys}/{MAX_OPEN_BUYS} | "
          f"committed ${state['cost']:.2f}/${MAX_POSITION_COST:.2f} | "
          f"buying {buying}", flush=True)


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
    parser.add_argument("--once", action="store_true", help="Fetch one sample and exit")
    parser.add_argument("--shift-window", action="store_true", help="Move reference to current BTC quote; preserve open sell target; no trades")
    parser.add_argument("--usd-cad", type=float, help="Optional explicit CAD per USD reference for display only")
    args = parser.parse_args()
    if sum((args.status, args.once, args.shift_window)) > 1:
        parser.error("Choose only one action")
    if args.usd_cad is not None and (not math.isfinite(args.usd_cad) or args.usd_cad <= 0):
        parser.error("--usd-cad must be a positive finite conversion rate")
    if args.status:
        state = load_state()
        if state is None:
            print("No samples yet; start the paper service first.")
        else:
            show_status(state, time.time())
        return
    verify_market()
    with (BASE / "ndax.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Load errors stop the process rather than resetting its balances.
        state = load_state()
        if args.shift_window:
            if state is None:
                raise SystemExit("No saved paper state; nothing changed")
            price = price_now()
            timestamp = time.time()
            backup = STATE.with_name("ndax_state.before-shift-" + str(time.time_ns()) + ".json")
            with backup.open("xb") as handle:
                handle.write(STATE.read_bytes())
                handle.flush()
                os.fsync(handle.fileno())
            move_window(state, price, timestamp)
            save_state(state)
            print("PAPER ONLY: reference moved to the latest NDAX BTC/CAD midpoint. No trades placed.")
            print_window(state, args.usd_cad)
            return
        if state is not None:
            pin_sell_target(state)
        print("NDAX PAPER ONLY: CAD $1,000 start; $50 buys / $150 cap; movable buy reference, pinned position sell target; 24h warm-up", flush=True)
        while True:
            try:
                price = price_now()
                timestamp = time.time()
                if state is None:
                    state = initial_state(price, timestamp)
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
            if args.once:
                return
            time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
