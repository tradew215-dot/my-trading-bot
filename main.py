#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SUPER TREND MTF SCALPING BOT - Python 3
========================================
Strategy: 1m + 3m + 5m SuperTrend
- Quick scalp when 1m flips with 3m confirmation
- Full-size trend entry when 1m/3m/5m agree
- Fake-break protection & Chop filter
- ATR volatility filter & Partial profit booking
- Trailing stop & Dynamic position sizing
- Cooldown after exit & Persistent local state
"""

import os
import json
import time
import math
import hmac
import hashlib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests

# ============================================================
# CONFIG
# ============================================================
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")

# SuperTrend parameters
ST_ATR_PERIOD = 10
ST_MULTIPLIER = 3.0

# Data
POLL_SECONDS = 2.0
CANDLE_LIMIT = 180

# Fake-break / chop protection
BREAK_ATR_BUFFER = 0.12
MIN_BODY_ATR = 0.10
CHOP_LOOKBACK = 12
MAX_FLIPS_IN_CHOP = 3
CONFIRM_CLOSES = 1

# Risk
RISK_PER_TRADE_PCT = 0.35
MAX_QTY = 0.002
MIN_QTY = 0.001

# Stop / profit
INITIAL_SL_ATR = 1.20
TRAIL_START_PCT = 0.25
TRAIL_GAP_PCT = 0.10
PARTIAL_TP_PCT = 0.35
PARTIAL_CLOSE_FRACTION = 0.50
RUNNER_ATR_MULT = 1.70

# Cooldown
COOLDOWN_SECONDS = 20

# Capital used for sizing in DRY RUN / fallback
PAPER_EQUITY = float(os.getenv("PAPER_EQUITY", "10000"))

# Persistent files
STATE_FILE = Path("supertrend_bot_state.json")
TRADE_LOG = Path("supertrend_trades.jsonl")

# ============================================================
# COSMIC CONFIG
# ============================================================
COSMIC_BASE_URL = os.getenv("COSMIC_BASE_URL", "https://api.cosmictrade.io")
COSMIC_API_KEY = os.getenv("COSMIC_API_KEY", "")
COSMIC_API_SECRET = os.getenv("COSMIC_API_SECRET", "")

COSMIC_ORDER_ENDPOINT = os.getenv("COSMIC_ORDER_ENDPOINT", "")
COSMIC_POSITION_ENDPOINT = os.getenv("COSMIC_POSITION_ENDPOINT", "")
COSMIC_BALANCE_ENDPOINT = os.getenv("COSMIC_BALANCE_ENDPOINT", "")

# 0 = paper mode, 1 = live
LIVE_TRADING = os.getenv("LIVE_TRADING", "0") == "1"

# ============================================================
# HTTP
# ============================================================
HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "SuperTrend-MTF-Bot/1.0",
    "Accept": "application/json",
})

BINANCE_DATA_URLS = [
    "https://api.binance.com/api/v3/klines",
    "https://api1.binance.com/api/v3/klines",
    "https://api2.binance.com/api/v3/klines",
    "https://api3.binance.com/api/v3/klines",
]

def parse_json(response):
    try:
        return response.json()
    except Exception:
        return None

def get_with_retry(url, params=None):
    last_error = None
    for attempt in range(4):
        try:
            response = HTTP.get(url, params=params, timeout=(3.0, 7.0))
            if response.status_code == 200:
                return response
            last_error = f"HTTP {response.status_code}: {response.text[:100]}"
        except requests.RequestException as e:
            last_error = str(e)
        time.sleep(min(2.0, 0.35 * (2 ** attempt)))
    raise RuntimeError(last_error or "request failed")

# ============================================================
# MARKET DATA
# ============================================================
def get_candles(interval):
    params = {
        "symbol": SYMBOL,
        "interval": interval,
        "limit": CANDLE_LIMIT
    }
    last_error = None
    for url in BINANCE_DATA_URLS:
        try:
            response = get_with_retry(url, params)
            data = parse_json(response)
            if not isinstance(data, list) or len(data) < 50:
                last_error = "invalid kline response"
                continue
            
            # Drop current unfinished candle.
            data = data[:-1]
            candles = []
            for k in data:
                candles.append({
                    "time": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            return candles
        except Exception as e:
            last_error = str(e)
    print(f"⚠️ {interval} data error: {last_error}")
    return []

# ============================================================
# INDICATORS
# ============================================================
def true_ranges(candles):
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return trs

def atr_series(candles, period):
    if len(candles) < period + 2:
        return []
    trs = true_ranges(candles)
    result = [None] * len(candles)
    first = sum(trs[:period]) / period
    result[period] = first
    value = first
    for i in range(period + 1, len(candles)):
        tr = trs[i - 1]
        value = ((value * (period - 1)) + tr) / period
        result[i] = value
    return result

def supertrend(candles, period=10, multiplier=3.0):
    n = len(candles)
    if n < period + 5:
        return [], [], []
    atrs = atr_series(candles, period)
    upper = [None] * n
    lower = [None] * n
    st = [None] * n
    direction = [0] * n

    for i in range(n):
        if atrs[i] is None:
            continue
        hl2 = (candles[i]["high"] + candles[i]["low"]) / 2
        basic_upper = hl2 + multiplier * atrs[i]
        basic_lower = hl2 - multiplier * atrs[i]

        if i == period:
            upper[i] = basic_upper
            lower[i] = basic_lower
            if candles[i]["close"] >= hl2:
                direction[i] = 1
                st[i] = lower[i]
            else:
                direction[i] = -1
                st[i] = upper[i]
            continue

        prev_upper = upper[i - 1]
        prev_lower = lower[i - 1]
        prev_close = candles[i - 1]["close"]

        if prev_upper is None:
            prev_upper = basic_upper
        if prev_lower is None:
            prev_lower = basic_lower

        if basic_upper < prev_upper or prev_close > prev_upper:
            upper[i] = basic_upper
        else:
            upper[i] = prev_upper

        if basic_lower > prev_lower or prev_close < prev_lower:
            lower[i] = basic_lower
        else:
            lower[i] = prev_lower

        prev_dir = direction[i - 1]
        if prev_dir == 1:
            if candles[i]["close"] < lower[i]:
                direction[i] = -1
                st[i] = upper[i]
            else:
                direction[i] = 1
                st[i] = lower[i]
        elif prev_dir == -1:
            if candles[i]["close"] > upper[i]:
                direction[i] = 1
                st[i] = lower[i]
            else:
                direction[i] = -1
                st[i] = upper[i]
        else:
            direction[i] = 1
            st[i] = lower[i]

    return direction, st, atrs

def candle_body_ok(candle, atr):
    if not atr:
        return False
    return abs(candle["close"] - candle["open"]) >= MIN_BODY_ATR * atr

def is_choppy(candles, dirs):
    if len(candles) < CHOP_LOOKBACK + 3:
        return True
    recent = dirs[-CHOP_LOOKBACK:]
    flips = 0
    for i in range(1, len(recent)):
        if recent[i] != recent[i - 1] and recent[i] != 0 and recent[i - 1] != 0:
            flips += 1
    if flips >= MAX_FLIPS_IN_CHOP:
        return True

    ranges = [c["high"] - c["low"] for c in candles[-CHOP_LOOKBACK:]]
    avg_range = sum(ranges) / len(ranges)
    if avg_range <= 0:
        return True

    close_move = abs(candles[-1]["close"] - candles[-CHOP_LOOKBACK]["close"])
    if close_move < avg_range * 0.55:
        return True
    return False

# ============================================================
# SIGNAL ENGINE
# ============================================================
def build_signal(c1, c3, c5):
    d1, st1, a1 = supertrend(c1, ST_ATR_PERIOD, ST_MULTIPLIER)
    d3, st3, a3 = supertrend(c3, ST_ATR_PERIOD, ST_MULTIPLIER)
    d5, st5, a5 = supertrend(c5, ST_ATR_PERIOD, ST_MULTIPLIER)

    if not d1 or not d3 or not d5:
        return {"signal": "WAIT", "reason": "indicator_not_ready"}

    i1 = len(c1) - 1
    i3 = len(c3) - 1
    i5 = len(c5) - 1

    if any([
        a1[i1] is None, a3[i3] is None, a5[i5] is None,
        st1[i1] is None, st3[i3] is None, st5[i5] is None
    ]):
        return {"signal": "WAIT", "reason": "atr_not_ready"}

    prev_dir1 = d1[i1 - 1]
    curr_dir1 = d1[i1]
    flip_up = prev_dir1 == -1 and curr_dir1 == 1
    flip_down = prev_dir1 == 1 and curr_dir1 == -1

    price = c1[i1]["close"]
    atr1 = a1[i1]

    buy_break = price > st1[i1] + BREAK_ATR_BUFFER * atr1
    sell_break = price < st1[i1] - BREAK_ATR_BUFFER * atr1

    body_ok = candle_body_ok(c1[i1], atr1)
    chop = is_choppy(c1, d1)

    bull3 = d3[i3] == 1
    bear3 = d3[i3] == -1
    bull5 = d5[i5] == 1
    bear5 = d5[i5] == -1

    signal = "WAIT"
    strength = "NONE"

    if flip_up and buy_break and bull3 and bull5:
        signal = "BUY"
        strength = "STRONG"
    elif flip_down and sell_break and bear3 and bear5:
        signal = "SELL"
        strength = "STRONG"
    elif flip_up and buy_break and bull3 and not bear5:
        signal = "BUY"
        strength = "SCALP"
    elif flip_down and sell_break and bear3 and not bull5:
        signal = "SELL"
        strength = "SCALP"

    if chop or not body_ok:
        signal = "WAIT"

    return {
        "signal": signal,
        "strength": strength,
        "price": price,
        "atr1": atr1,
        "st1": st1[i1],
        "st3": st3[i3],
        "st5": st5[i5],
        "dir1": "BULL" if curr_dir1 == 1 else "BEAR",
        "dir3": "BULL" if bull3 else "BEAR",
        "dir5": "BULL" if bull5 else "BEAR",
        "chop": chop,
        "body_ok": body_ok,
        "candle_time": c1[i1]["time"],
    }

# ============================================================
# POSITION SIZING
# ============================================================
def normalize_qty(qty):
    step = 0.001
    qty = math.floor(qty / step) * step
    qty = min(qty, MAX_QTY)
    return round(max(qty, 0), 6)

def calculate_qty(equity, entry, stop):
    risk_money = equity * RISK_PER_TRADE_PCT / 100
    distance = abs(entry - stop)
    if distance <= 0:
        return 0.0
    raw_qty = risk_money / distance
    qty = normalize_qty(raw_qty)
    if qty < MIN_QTY:
        return 0.0
    return qty

# ============================================================
# STATE
# ============================================================
@dataclass
class Position:
    side: str = ""
    qty: float = 0.0
    entry: float = 0.0
    peak: float = 0.0
    stop: float = 0.0
    partial_done: bool = False
    opened_at: float = 0.0
    signal_candle: int = 0

    @property
    def active(self):
        return self.side in ("BUY", "SELL") and self.qty > 0

def load_position():
    try:
        if STATE_FILE.exists():
            return Position(**json.loads(STATE_FILE.read_text()))
    except Exception:
        pass
    return Position()

def save_position(pos):
    STATE_FILE.write_text(json.dumps(asdict(pos), indent=2), encoding="utf-8")

def log_trade(data):
    data["time"] = time.time()
    with TRADE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data) + "\n")

# ============================================================
# COSMIC BROKER ADAPTER
# ============================================================
class CosmicBroker:
    def __init__(self):
        self.base = COSMIC_BASE_URL.rstrip("/")
        self.key = COSMIC_API_KEY
        self.secret = COSMIC_API_SECRET

    def equity(self):
        if not LIVE_TRADING:
            return PAPER_EQUITY
        try:
            endpoint = COSMIC_BALANCE_ENDPOINT or f"{self.base}/v1/balance"
            headers = {
                "X-API-KEY": self.key,
                "X-API-SECRET": self.secret,
                "Content-Type": "application/json"
            }
            res = HTTP.get(endpoint, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                return float(data.get("equity") or data.get("balance") or PAPER_EQUITY)
        except Exception as e:
            print(f"⚠️ Balance fetch error: {e}. Fallback to paper equity.")
        return PAPER_EQUITY

    def remote_position(self):
        if not LIVE_TRADING:
            return None
        return None

    def market_order(self, side, qty):
        if not LIVE_TRADING:
            print(f"🧪 PAPER ORDER -> {side} {qty}")
            return {"ok": True, "price": None, "qty": qty, "side": side, "paper": True}

        if not self.key:
            print("❌ Error: COSMIC_API_KEY Missing!")
            return {"ok": False, "error": "API Key Missing"}

        try:
            endpoint = COSMIC_ORDER_ENDPOINT or f"{self.base}/v1/order"
            headers = {
                "X-API-KEY": self.key,
                "X-API-SECRET": self.secret,
                "Content-Type": "application/json"
            }
            payload = {
                "symbol": SYMBOL,
                "side": side.upper(),
                "type": "MARKET",
                "quantity": qty
            }
            res = HTTP.post(endpoint, json=payload, headers=headers, timeout=10)
            data = res.json()
            if res.status_code in [200, 201] and data.get("status") != "ERROR":
                return {"ok": True, "price": data.get("price"), "qty": qty, "side": side, "data": data}
            else:
                print(f"❌ Cosmic Order Rejected: {data}")
                return {"ok": False, "error": data}
        except Exception as e:
            print(f"❌ Market order error: {e}")
            return {"ok": False, "error": str(e)}

    def reduce_only(self, side, qty):
        if not LIVE_TRADING:
            print(f"🧪 PAPER REDUCE -> {side} {qty}")
            return {"ok": True, "qty": qty, "side": side, "paper": True}

        try:
            endpoint = COSMIC_ORDER_ENDPOINT or f"{self.base}/v1/order"
            headers = {
                "X-API-KEY": self.key,
                "X-API-SECRET": self.secret,
                "Content-Type": "application/json"
            }
            payload = {
                "symbol": SYMBOL,
                "side": side.upper(),
                "type": "MARKET",
                "quantity": qty,
                "reduceOnly": True
            }
            res = HTTP.post(endpoint, json=payload, headers=headers, timeout=10)
            data = res.json()
            if res.status_code in [200, 201] and data.get("status") != "ERROR":
                return {"ok": True, "qty": qty, "side": side, "data": data}
            else:
                print(f"❌ Cosmic Reduce Order Rejected: {data}")
                return {"ok": False, "error": data}
        except Exception as e:
            print(f"❌ Reduce order error: {e}")
            return {"ok": False, "error": str(e)}

# ============================================================
# TRADING ENGINE
# ============================================================
def enter(broker, pos, sig):
    side = sig["signal"]
    entry = sig["price"]
    atr1 = sig["atr1"]

    if side == "BUY":
        stop = entry - INITIAL_SL_ATR * atr1
    else:
        stop = entry + INITIAL_SL_ATR * atr1

    equity = broker.equity()
    qty = calculate_qty(equity, entry, stop)

    if qty <= 0:
        print("⚠️ Position size too small; skipping.")
        return

    if sig["strength"] == "SCALP":
        qty = normalize_qty(qty * 0.50)
        if qty < MIN_QTY:
            print("⚠️ Quantity below minimum; skipping.")
            return

    print(
        f"🚀 {sig['strength']} {side} | "
        f"price={entry:.2f} | qty={qty} | SL={stop:.2f} | "
        f"1m={sig['dir1']} 3m={sig['dir3']} 5m={sig['dir5']}"
    )

    result = broker.market_order(side, qty)
    if not result.get("ok"):
        print(f"❌ Order failed: {result}")
        return

    fill = float(result.get("price") or entry)
    pos.side = side
    pos.qty = qty
    pos.entry = fill
    pos.peak = fill
    pos.stop = stop
    pos.partial_done = False
    pos.opened_at = time.time()
    pos.signal_candle = sig["candle_time"]
    save_position(pos)

    log_trade({
        "event": "ENTRY",
        "side": side,
        "strength": sig["strength"],
        "qty": qty,
        "entry": fill,
        "stop": stop
    })

def close_position(broker, pos, reason):
    if not pos.active:
        return False
    close_side = "SELL" if pos.side == "BUY" else "BUY"
    print(f"🎯 EXIT {pos.side} | qty={pos.qty} | reason={reason}")

    result = broker.reduce_only(close_side, pos.qty)
    if not result.get("ok"):
        print(f"❌ Exit failed: {result}")
        return False

    log_trade({
        "event": "EXIT",
        "side": pos.side,
        "qty": pos.qty,
        "entry": pos.entry,
        "reason": reason
    })

    pos.side = ""
    pos.qty = 0.0
    pos.entry = 0.0
    pos.peak = 0.0
    pos.stop = 0.0
    pos.partial_done = False
    pos.opened_at = 0
    pos.signal_candle = 0
    save_position(pos)
    return True

def manage(broker, pos, sig):
    if not pos.active:
        return False
    price = sig["price"]
    atr1 = sig["atr1"]

    if pos.side == "BUY":
        pos.peak = max(pos.peak, price)
        profit_pct = (price - pos.entry) / pos.entry * 100

        if price <= pos.stop:
            return close_position(broker, pos, "INITIAL_SL")

        if not pos.partial_done and profit_pct >= PARTIAL_TP_PCT:
            part = normalize_qty(pos.qty * PARTIAL_CLOSE_FRACTION)
            if part >= MIN_QTY and part < pos.qty:
                result = broker.reduce_only("SELL", part)
                if result.get("ok"):
                    pos.qty = normalize_qty(pos.qty - part)
                    pos.partial_done = True
                    pos.stop = max(pos.stop, pos.entry)
                    save_position(pos)
                    print(f"💰 PARTIAL TP -> {part} booked | runner={pos.qty}")

        if profit_pct >= TRAIL_START_PCT:
            percent_stop = pos.peak * (1 - TRAIL_GAP_PCT / 100)
            pos.stop = max(pos.stop, percent_stop)

        if pos.partial_done:
            atr_stop = price - RUNNER_ATR_MULT * atr1
            pos.stop = max(pos.stop, atr_stop)

        if sig["dir1"] == "BEAR" and profit_pct > 0:
            return close_position(broker, pos, "SUPERTREND_REVERSAL")

        if price <= pos.stop:
            return close_position(broker, pos, "TRAILING_SL")

    else:
        pos.peak = min(pos.peak, price)
        profit_pct = (pos.entry - price) / pos.entry * 100

        if price >= pos.stop:
            return close_position(broker, pos, "INITIAL_SL")

        if not pos.partial_done and profit_pct >= PARTIAL_TP_PCT:
            part = normalize_qty(pos.qty * PARTIAL_CLOSE_FRACTION)
            if part >= MIN_QTY and part < pos.qty:
                result = broker.reduce_only("BUY", part)
                if result.get("ok"):
                    pos.qty = normalize_qty(pos.qty - part)
                    pos.partial_done = True
                    pos.stop = min(pos.stop, pos.entry)
                    save_position(pos)
                    print(f"💰 PARTIAL TP -> {part} booked | runner={pos.qty}")

        if profit_pct >= TRAIL_START_PCT:
            percent_stop = pos.peak * (1 + TRAIL_GAP_PCT / 100)
            pos.stop = min(pos.stop, percent_stop)

        if pos.partial_done:
            atr_stop = price + RUNNER_ATR_MULT * atr1
            pos.stop = min(pos.stop, atr_stop)

        if sig["dir1"] == "BULL" and profit_pct > 0:
            return close_position(broker, pos, "SUPERTREND_REVERSAL")

        if price >= pos.stop:
            return close_position(broker, pos, "TRAILING_SL")

    save_position(pos)
    print(
        f"📌 {pos.side} price={price:.2f} "
        f"entry={pos.entry:.2f} stop={pos.stop:.2f} "
        f"qty={pos.qty}"
    )
    return False

# ============================================================
# MAIN LOOP
# ============================================================
def main():
    print("=" * 70)
    print("🚀 SUPER TREND 1m / 3m / 5m SCALPING BOT")
    print("=" * 70)
    print(f"SYMBOL: {SYMBOL}")
    print(f"SuperTrend: ATR {ST_ATR_PERIOD} x {ST_MULTIPLIER}")
    print(f"LIVE_TRADING: {LIVE_TRADING}")
    if LIVE_TRADING:
        print("⚠️ LIVE MODE — REAL ORDERS CAN BE SENT.")
    else:
        print("🧪 PAPER MODE — NO REAL ORDERS.")

    broker = CosmicBroker()
    pos = load_position()
    last_exit = 0
    last_candle = pos.signal_candle

    while True:
        try:
            c1 = get_candles("1m")
            c3 = get_candles("3m")
            c5 = get_candles("5m")

            if not c1 or not c3 or not c5:
                time.sleep(POLL_SECONDS)
                continue

            sig = build_signal(c1, c3, c5)
            print(
                f"📊 {SYMBOL} | {sig.get('signal')} "
                f"{sig.get('strength')} | "
                f"1m {sig.get('dir1')} | "
                f"3m {sig.get('dir3')} | "
                f"5m {sig.get('dir5')} | "
                f"Price {sig.get('price', 0):.2f}"
            )

            if pos.active:
                before = pos.active
                closed = manage(broker, pos, sig)
                if before and closed:
                    last_exit = time.time()
            else:
                if time.time() - last_exit < COOLDOWN_SECONDS:
                    time.sleep(POLL_SECONDS)
                    continue

                candle = sig.get("candle_time", 0)
                if candle != last_candle:
                    if sig.get("signal") in ("BUY", "SELL"):
                        enter(broker, pos, sig)
                    last_candle = candle
                    pos.signal_candle = candle
                    save_position(pos)

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            print("\n🛑 Bot stopped.")
            break
        except Exception as e:
            print(f"⚠️ Bot error: {type(e).__name__}: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
