# -*- coding: utf-8 -*-
# XAUUSD AI Scalping Signal Bot -- MTU Premium
# Strategy: S&R, S&D, Engulfing, Market Structure, RSI, EMA, MACD
# Timeframe: 15-min | Sessions: Asia, London, New York

import os
import json
import sys
import requests
from datetime import datetime, date, timezone

# ── Secrets ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
TWELVEDATA_API_KEY  = os.environ["TWELVEDATA_API_KEY"]
NEWSAPI_KEY         = os.environ.get("NEWSAPI_KEY", "")  # Optional -- get free at newsapi.org

# ── Config ────────────────────────────────────────────────────────────────────
MAX_SIGNALS_PER_DAY = 10
COOLDOWN_MINUTES    = 90
SYMBOL              = "XAU/USD"
INTERVAL            = "15min"

# ── Persistent storage paths ──────────────────────────────────────────────────
# Railway Volume should be mounted at /data for persistence across restarts
# If /data not available, falls back to current directory (non-persistent)
import pathlib as _pathlib
_DATA_DIR         = _pathlib.Path("/data") if _pathlib.Path("/data").exists() else _pathlib.Path(".")
SIGNAL_COUNT_FILE = str(_DATA_DIR / "signal_count.json")
OPEN_SIGNALS_FILE = str(_DATA_DIR / "open_signals.json")
print(f"Storage directory: {_DATA_DIR} ({'persistent' if str(_DATA_DIR) == '/data' else 'non-persistent -- add Railway Volume!'})")


# ── Session helpers ───────────────────────────────────────────────────────────

SESSIONS = {
    "Asia":     (0,  8),
    "London":   (7,  16),
    "New York": (13, 21),
}

# ── Active trading hours (MYT) ────────────────────────────────────────────────
# Bot active: 7:00 AM - 2:00 AM MYT
# In UTC:     23:00 - 18:00 (wraps midnight)
# Off hours:  2:00 AM - 7:00 AM MYT = 18:00 - 23:00 UTC

def is_active_hours(utc_hour: int) -> bool:
    """Returns True if within active trading hours (7AM-2AM MYT)."""
    # Active UTC hours: 23,0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17
    # Off UTC hours: 18,19,20,21,22
    return utc_hour not in (18, 19, 20, 21, 22)

def get_current_session(utc_hour: int) -> str:
    if not is_active_hours(utc_hour):
        return "Off-hours"
    active = [n for n, (s, e) in SESSIONS.items() if s <= utc_hour < e]
    return " / ".join(active) if active else "Asia"


# ── State management ──────────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        with open(SIGNAL_COUNT_FILE) as f:
            data = json.load(f)
        if data.get("date") == str(date.today()):
            return data
    except FileNotFoundError:
        pass
    return {"date": str(date.today()), "count": 0, "last_signal_utc": None}


def save_state(state: dict):
    with open(SIGNAL_COUNT_FILE, "w") as f:
        json.dump(state, f)


def cooldown_ok(state: dict) -> bool:
    last = state.get("last_signal_utc")
    if not last:
        return True
    diff = (datetime.now(timezone.utc) -
            datetime.fromisoformat(last)).total_seconds() / 60
    return diff >= COOLDOWN_MINUTES


# ── Twelve Data fetcher ───────────────────────────────────────────────────────

def td_get(endpoint: str, **params) -> dict:
    url = f"https://api.twelvedata.com/{endpoint}"
    params.update({"symbol": SYMBOL, "interval": INTERVAL,
                   "apikey": TWELVEDATA_API_KEY})
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("status") == "error" or ("code" in data and data["code"] != 200):
        raise ValueError(f"Twelve Data /{endpoint}: {data.get('message', data)}")
    return data


def compute_ema(closes: list, period: int) -> list:
    """Calculate EMA from list of closes (oldest first)."""
    k      = 2 / (period + 1)
    ema    = closes[0]
    result = [ema]
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
        result.append(ema)
    return result  # same order as input (oldest first)


def compute_rsi(closes: list, period: int = 14) -> list:
    """Calculate RSI from list of closes (oldest first). Returns RSI list."""
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    if len(gains) < period:
        return [50.0]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_vals = []

    for i in range(period, len(gains)):
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rs  = avg_gain / avg_loss
            rsi_vals.append(round(100 - (100 / (1 + rs)), 2))
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    return rsi_vals if rsi_vals else [50.0]


def compute_atr(candles_asc: list, period: int = 14) -> list:
    """Calculate ATR. candles_asc = oldest first."""
    trs = []
    for i in range(1, len(candles_asc)):
        c    = candles_asc[i]
        prev = candles_asc[i - 1]
        tr   = max(
            c["high"] - c["low"],
            abs(c["high"] - prev["close"]),
            abs(c["low"]  - prev["close"])
        )
        trs.append(tr)

    if len(trs) < period:
        return [sum(trs) / len(trs)] if trs else [5.0]

    atr    = sum(trs[:period]) / period
    result = [atr]
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
        result.append(atr)
    return result  # oldest first


def compute_macd(closes: list, fast=12, slow=26, signal=9):
    """Returns (macd_line, signal_line) lists -- oldest first."""
    ema_fast   = compute_ema(closes, fast)
    ema_slow   = compute_ema(closes, slow)
    macd_line  = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = compute_ema(macd_line, signal)
    return macd_line, signal_line


def fetch_market_data() -> dict:
    """Single API call -- compute all indicators locally. Saves 5 credits per run."""
    print("  -> time_series (1 API call only)")
    price_data = td_get("time_series", outputsize=60)

    # Build candles list -- newest first (as returned by API)
    candles = []
    for v in price_data["values"]:
        candles.append({
            "open":  float(v["open"]),
            "high":  float(v["high"]),
            "low":   float(v["low"]),
            "close": float(v["close"]),
            "dt":    v["datetime"],
        })

    # Reverse to oldest-first for indicator calculation
    candles_asc = list(reversed(candles))
    closes_asc  = [c["close"] for c in candles_asc]

    # ── Compute all indicators locally ───────────────────────────────────────
    ema9_asc   = compute_ema(closes_asc, 9)
    ema21_asc  = compute_ema(closes_asc, 21)
    rsi_asc    = compute_rsi(closes_asc, 14)
    atr_asc    = compute_atr(candles_asc, 14)
    macd_asc, macd_sig_asc = compute_macd(closes_asc)

    # Latest values (last item = most recent)
    ema9_val      = round(ema9_asc[-1], 2)
    ema9_prev     = round(ema9_asc[-2], 2)
    ema21_val     = round(ema21_asc[-1], 2)
    ema21_prev    = round(ema21_asc[-2], 2)
    rsi_val       = round(rsi_asc[-1], 2)
    rsi_prev      = round(rsi_asc[-2], 2) if len(rsi_asc) >= 2 else rsi_val
    atr_val       = round(atr_asc[-1], 2)
    avg_atr       = round(sum(atr_asc[-20:]) / min(20, len(atr_asc)), 2)
    macd_val      = round(macd_asc[-1], 4)
    macd_prev_val = round(macd_asc[-2], 4)
    macd_sig_val  = round(macd_sig_asc[-1], 4)
    macd_sig_prev = round(macd_sig_asc[-2], 4)

    latest = candles[0]  # most recent (newest first)

    return {
        "candles":       candles,
        "price":         latest["close"],
        "prev_close":    candles[1]["close"],
        "open":          latest["open"],
        "high":          latest["high"],
        "low":           latest["low"],
        "rsi":           rsi_val,
        "rsi_prev":      rsi_prev,
        "ema9":          ema9_val,
        "ema9_prev":     ema9_prev,
        "ema21":         ema21_val,
        "ema21_prev":    ema21_prev,
        "macd":          macd_val,
        "macd_signal":   macd_sig_val,
        "macd_prev":     macd_prev_val,
        "macd_sig_prev": macd_sig_prev,
        "atr":           atr_val,
        "avg_atr":       avg_atr,
        "timestamp":     latest["dt"],
    }


# ══════════════════════════════════════════════════════════════════════════════
#  PRICE ACTION ANALYSIS ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def detect_market_structure(candles: list) -> str:
    highs = [c["high"] for c in candles[:20]]
    lows  = [c["low"]  for c in candles[:20]]

    swing_highs = []
    swing_lows  = []

    for i in range(1, len(highs) - 1):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
            swing_highs.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
            swing_lows.append(lows[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "ranging"

    hh = swing_highs[-1] > swing_highs[-2]
    hl = swing_lows[-1]  > swing_lows[-2]
    ll = swing_lows[-1]  < swing_lows[-2]
    lh = swing_highs[-1] < swing_highs[-2]

    if hh and hl:
        return "bullish"
    if ll and lh:
        return "bearish"
    return "ranging"


def find_sr_levels(candles: list, price: float, atr: float) -> dict:
    highs = [c["high"] for c in candles]
    lows  = [c["low"]  for c in candles]

    swing_highs = []
    swing_lows  = []

    for i in range(2, len(highs) - 2):
        if highs[i] == max(highs[i-2:i+3]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i-2:i+3]):
            swing_lows.append(lows[i])

    def cluster(levels):
        if not levels:
            return []
        levels = sorted(set(round(l, 2) for l in levels))
        result = [levels[0]]
        for l in levels[1:]:
            if l - result[-1] > atr * 0.3:
                result.append(l)
            else:
                result[-1] = round((result[-1] + l) / 2, 2)
        return result

    res_levels = [l for l in cluster(swing_highs) if l > price]
    sup_levels = [l for l in cluster(swing_lows)  if l < price]

    nearest_res = min(res_levels) if res_levels else round(price + atr * 3, 2)
    nearest_sup = max(sup_levels) if sup_levels else round(price - atr * 3, 2)

    return {
        "support":         nearest_sup,
        "resistance":      nearest_res,
        "near_support":    abs(price - nearest_sup) < atr * 0.5,
        "near_resistance": abs(price - nearest_res) < atr * 0.5,
    }


def find_sd_zones(candles: list, price: float, atr: float) -> dict:
    demand_zones = []
    supply_zones = []
    threshold    = atr * 1.5

    for i in range(2, len(candles) - 1):
        c    = candles[i]
        body = abs(c["close"] - c["open"])

        if body > threshold:
            if c["close"] > c["open"]:
                demand_zones.append((round(candles[i+1]["low"], 2),
                                     round(c["open"], 2)))
            else:
                supply_zones.append((round(c["open"], 2),
                                     round(candles[i+1]["high"], 2)))

    in_demand = any(zl <= price <= zh for zl, zh in demand_zones[-5:])
    in_supply = any(zl <= price <= zh for zl, zh in supply_zones[-5:])

    nearest_demand = next(
        ((zl, zh) for zl, zh in reversed(demand_zones) if zh < price), None)
    nearest_supply = next(
        ((zl, zh) for zl, zh in reversed(supply_zones) if zl > price), None)

    return {
        "in_demand":      in_demand,
        "in_supply":      in_supply,
        "nearest_demand": nearest_demand,
        "nearest_supply": nearest_supply,
    }


def detect_candle_patterns(candles: list, atr: float) -> dict:
    c0 = candles[0]
    c1 = candles[1]
    c2 = candles[2]

    body0  = abs(c0["close"] - c0["open"])
    body1  = abs(c1["close"] - c1["open"])
    range0 = c0["high"] - c0["low"]

    upper_wick0 = c0["high"] - max(c0["open"], c0["close"])
    lower_wick0 = min(c0["open"], c0["close"]) - c0["low"]

    is_bull0 = c0["close"] > c0["open"]
    is_bull1 = c1["close"] > c1["open"]
    is_bear0 = c0["close"] < c0["open"]
    is_bear1 = c1["close"] < c1["open"]

    bullish_engulfing = (
        is_bear1 and is_bull0 and
        c0["open"]  <= c1["close"] and
        c0["close"] >= c1["open"]  and
        body0 > body1
    )
    bearish_engulfing = (
        is_bull1 and is_bear0 and
        c0["open"]  >= c1["close"] and
        c0["close"] <= c1["open"]  and
        body0 > body1
    )
    bullish_pin = (
        lower_wick0 >= body0 * 2.5 and
        upper_wick0 <= body0 * 0.5 and
        range0 > atr * 0.5
    )
    bearish_pin = (
        upper_wick0 >= body0 * 2.5 and
        lower_wick0 <= body0 * 0.5 and
        range0 > atr * 0.5
    )
    inside_bar    = c0["high"] < c1["high"] and c0["low"] > c1["low"]
    double_bottom = (abs(c0["low"] - c2["low"]) < atr * 0.3
                     and c1["low"] > c0["low"] and is_bull0)
    double_top    = (abs(c0["high"] - c2["high"]) < atr * 0.3
                     and c1["high"] < c0["high"] and is_bear0)

    patterns = []
    if bullish_engulfing: patterns.append("Bullish Engulfing")
    if bearish_engulfing: patterns.append("Bearish Engulfing")
    if bullish_pin:       patterns.append("Bullish Pin Bar")
    if bearish_pin:       patterns.append("Bearish Pin Bar")
    if inside_bar:        patterns.append("Inside Bar")
    if double_bottom:     patterns.append("Double Bottom")
    if double_top:        patterns.append("Double Top")

    return {
        "patterns":          patterns,
        "bullish_patterns":  [p for p in patterns if "Bullish" in p or "Bottom" in p],
        "bearish_patterns":  [p for p in patterns if "Bearish" in p or "Top" in p],
        "bullish_engulfing": bullish_engulfing,
        "bearish_engulfing": bearish_engulfing,
        "bullish_pin":       bullish_pin,
        "bearish_pin":       bearish_pin,
        "double_bottom":     double_bottom,
        "double_top":        double_top,
    }


def check_conditions(d: dict) -> tuple:
    candles = d["candles"]
    price   = d["price"]
    rsi     = d["rsi"]
    atr     = d["atr"]
    avg_atr = d["avg_atr"]

    # Volatility gate
    if atr < avg_atr * 1.0:  # ATR must be AT LEAST average -- no ranging market signals
        return None, None, None, 0, {}

    structure = detect_market_structure(candles)
    sr        = find_sr_levels(candles, price, atr)
    sd        = find_sd_zones(candles, price, atr)
    cp        = detect_candle_patterns(candles, atr)

    ema_cross_up   = d["ema9_prev"] < d["ema21_prev"] and d["ema9"] > d["ema21"]
    ema_cross_down = d["ema9_prev"] > d["ema21_prev"] and d["ema9"] < d["ema21"]
    ema_bull       = d["ema9"] > d["ema21"]
    ema_bear       = d["ema9"] < d["ema21"]
    macd_bull      = d["macd_prev"] < d["macd_sig_prev"] and d["macd"] > d["macd_signal"]
    macd_bear      = d["macd_prev"] > d["macd_sig_prev"] and d["macd"] < d["macd_signal"]

    # Block signal completely if market is ranging -- most SL hits happen here
    if structure == "ranging":
        return None, None, None, 0, {}

    # -- BUY score ─────────────────────────────────────────────────────────────
    buy_score   = 0
    buy_reasons = []
    buy_data    = {}

    if structure == "bullish":
        buy_score += 2
        buy_reasons.append("Bullish market structure (HH+HL)")
        buy_data["structure"] = "bullish"

    if sr["near_support"]:
        buy_score += 1
        buy_reasons.append(f"Price at key support {sr['support']}")
        buy_data["support"] = sr["support"]

    if sd["in_demand"]:
        buy_score += 2
        buy_reasons.append("Price inside demand zone")
        buy_data["in_demand"] = True

    if cp["bullish_engulfing"] or cp["bullish_pin"]:
        buy_score += 1
        for p in cp["bullish_patterns"]:
            buy_reasons.append(p)
        buy_data["candle_pattern"] = cp["bullish_patterns"]

    if cp["double_bottom"]:
        buy_score += 1
        buy_reasons.append("Double Bottom pattern confirmed")
        buy_data["double_bottom"] = True

    if rsi < 35:  # Tightened from 40
        buy_score += 1
        buy_reasons.append(f"RSI {rsi:.1f} -- oversold")
        buy_data["rsi"] = rsi

    if ema_cross_up or ema_bull:
        buy_score += 1
        label = "EMA9 crossed above EMA21" if ema_cross_up else "EMA9 above EMA21"
        buy_reasons.append(label)
        buy_data["ema"] = label

    if macd_bull:
        buy_score += 1
        buy_reasons.append("MACD bullish crossover")
        buy_data["macd"] = "bullish"

    # ── SELL score ────────────────────────────────────────────────────────────
    sell_score   = 0
    sell_reasons = []
    sell_data    = {}

    if structure == "bearish":
        sell_score += 2
        sell_reasons.append("Bearish market structure (LL+LH)")
        sell_data["structure"] = "bearish"

    if sr["near_resistance"]:
        sell_score += 1
        sell_reasons.append(f"Price at key resistance {sr['resistance']}")
        sell_data["resistance"] = sr["resistance"]

    if sd["in_supply"]:
        sell_score += 2
        sell_reasons.append("Price inside supply zone")
        sell_data["in_supply"] = True

    if cp["bearish_engulfing"] or cp["bearish_pin"]:
        sell_score += 1
        for p in cp["bearish_patterns"]:
            sell_reasons.append(p)
        sell_data["candle_pattern"] = cp["bearish_patterns"]

    if cp["double_top"]:
        sell_score += 1
        sell_reasons.append("Double Top pattern confirmed")
        sell_data["double_top"] = True

    if rsi > 65:  # Tightened from 60
        sell_score += 1
        sell_reasons.append(f"RSI {rsi:.1f} -- overbought")
        sell_data["rsi"] = rsi

    if ema_cross_down or ema_bear:
        sell_score += 1
        label = "EMA9 crossed below EMA21" if ema_cross_down else "EMA9 below EMA21"
        sell_reasons.append(label)
        sell_data["ema"] = label

    if macd_bear:
        sell_score += 1
        sell_reasons.append("MACD bearish crossover")
        sell_data["macd"] = "bearish"

    # ── Pick winner ───────────────────────────────────────────────────────────
    MIN_SCORE = 5  # Raised from 4 -- reduces weak signals

    if buy_score >= sell_score and buy_score >= MIN_SCORE:
        confidence = "HIGH" if buy_score >= 7 else "MEDIUM"
        analysis   = {**buy_data, "sr": sr, "sd": sd, "score": buy_score}
        return "BUY", buy_reasons, confidence, buy_score, analysis

    if sell_score > buy_score and sell_score >= MIN_SCORE:
        confidence = "HIGH" if sell_score >= 7 else "MEDIUM"
        analysis   = {**sell_data, "sr": sr, "sd": sd, "score": sell_score}
        return "SELL", sell_reasons, confidence, sell_score, analysis

    return None, None, None, 0, {}


# ── AI signal message generator ───────────────────────────────────────────────

def generate_signal_message(signal_type: str, d: dict, confidence: str,
                             session: str, reasons: list,
                             score: int, analysis: dict) -> str:
    price = d["price"]
    atr   = d["atr"]
    sr    = analysis.get("sr", {})
    sd    = analysis.get("sd", {})

    # ── Risk & R:R rules ──────────────────────────────────────────────────────
    MAX_SL_PIPS = 50     # hard cap -- SL max 50 pips from entry
    MIN_RR      = 2.0    # minimum R:R 1:2 (TP2 must be 2x the risk)

    if signal_type == "BUY":
        entry   = price
        sl_sr   = round(sr.get("support", price - atr * 1.2) - atr * 0.3, 2)
        sl_atr  = round(price - atr * 1.2, 2)
        sl_raw  = max(sl_sr, sl_atr)
        # Cap SL to max 50 pips below entry
        sl      = round(max(sl_raw, price - MAX_SL_PIPS), 2)
        risk    = round(price - sl, 2)
        # TP2 must be at least 2x risk (1:2 R:R minimum)
        tp1     = round(price + risk * 1.0, 2)   # 1:1
        tp2     = round(price + risk * 2.0, 2)   # 1:2 minimum
        # TP3 must ALWAYS be higher than TP2 for BUY
        tp3_base = round(price + risk * 3.0, 2)
        tp3_sr   = sr.get("resistance", tp3_base)
        # Only use SR if it's above TP2, otherwise use ATR-based TP3
        tp3      = round(tp3_sr if tp3_sr > tp2 else tp3_base, 2)
    else:
        entry   = price
        sl_sr   = round(sr.get("resistance", price + atr * 1.2) + atr * 0.3, 2)
        sl_atr  = round(price + atr * 1.2, 2)
        sl_raw  = min(sl_sr, sl_atr)
        # Cap SL to max 50 pips above entry
        sl      = round(min(sl_raw, price + MAX_SL_PIPS), 2)
        risk    = round(sl - price, 2)
        # TP2 must be at least 2x risk (1:2 R:R minimum)
        tp1     = round(price - risk * 1.0, 2)   # 1:1
        tp2     = round(price - risk * 2.0, 2)   # 1:2 minimum
        # TP3 must ALWAYS be lower than TP2 for SELL
        tp3_base = round(price - risk * 3.0, 2)
        tp3_sr   = sr.get("support", tp3_base)
        # Only use SR if it's below TP2, otherwise use ATR-based TP3
        tp3      = round(tp3_sr if tp3_sr < tp2 else tp3_base, 2)

    # Block signal if SL exceeds 50 pips hard cap
    actual_risk = round(abs(entry - sl), 2)
    if actual_risk > MAX_SL_PIPS:
        print(f"Signal blocked -- SL {actual_risk} pips exceeds {MAX_SL_PIPS} pip hard cap.")
        return None

    # Block signal if R:R at TP2 is less than 1:2
    tp2_rr = round(abs(tp2 - entry) / actual_risk, 2) if actual_risk > 0 else 0
    if tp2_rr < MIN_RR:
        print(f"Signal blocked -- R:R {tp2_rr} at TP2 is below minimum 1:{MIN_RR}.")
        return None

    risk = actual_risk
    rr1  = round(abs(tp1 - entry) / risk, 1) if risk > 0 else 0.8
    rr2  = round(abs(tp2 - entry) / risk, 1) if risk > 0 else 1.5
    rr3  = round(abs(tp3 - entry) / risk, 1) if risk > 0 else 2.5
    sl_pips  = round(abs(entry - sl),  2)
    tp1_pips = round(abs(tp1 - entry), 2)
    tp2_pips = round(abs(tp2 - entry), 2)
    tp3_pips = round(abs(tp3 - entry), 2)
    sl_sign  = "-" if signal_type == "BUY" else "+"
    tp_sign  = "+" if signal_type == "BUY" else "-"

    direction_emoji  = "📈" if signal_type == "BUY" else "📉"
    confidence_emoji = "🔥" if confidence == "HIGH" else "⚡"
    reasons_str      = "\n".join(f"  • {r}" for r in reasons[:6])

    zone_note = ""
    if signal_type == "BUY" and sd.get("in_demand"):
        zone_note = "Price is currently inside a demand zone -- high-probability long area."
    elif signal_type == "SELL" and sd.get("in_supply"):
        zone_note = "Price is currently inside a supply zone -- high-probability short area."
    elif signal_type == "BUY" and sd.get("nearest_demand"):
        z = sd["nearest_demand"]
        zone_note = f"Nearest demand zone sits at {z[0]}-{z[1]}."
    elif signal_type == "SELL" and sd.get("nearest_supply"):
        z = sd["nearest_supply"]
        zone_note = f"Nearest supply zone sits at {z[0]}-{z[1]}."

    prompt = f"""You are a professional XAUUSD scalping signal analyst for MTU Premium Telegram channel.

Write a signal message using EXACTLY this template. Do NOT change any numbers I provide:

{confidence_emoji} XAUUSD {signal_type} SCALP {direction_emoji}
━━━━━━━━━━━━━━━━━━━━━
🕐 Session: {session}
📊 Pair: XAUUSD (Gold/USD)
⏱ Timeframe: 15 Min Scalp
💪 Confidence: {confidence} (Score: {score}/8)

🎯 Entry:     {entry}
🛑 Stop Loss: {sl}  ({sl_sign}{sl_pips} pips)

✅ TP1: {tp1}  ({tp_sign}{tp1_pips} pips)  → R:R 1:{rr1}
✅ TP2: {tp2}  ({tp_sign}{tp2_pips} pips)  → R:R 1:{rr2}
✅ TP3: {tp3}  ({tp_sign}{tp3_pips} pips)  → R:R 1:{rr3}

📐 Risk: {sl_pips} pips  |  Best R:R: 1:{rr3}
━━━━━━━━━━━━━━━━━━━━━
🔍 Confluences:
{reasons_str}

📝 Analysis:
[Write exactly 3 sharp sentences in English:
 1. Describe market structure and what it signals for this trade direction.
 2. Explain the S&R or S&D context: {zone_note if zone_note else 'describe the key price levels at play'}.
 3. Summarise the momentum confirmation from RSI={d['rsi']:.1f}, EMA alignment, and MACD.
 Be sharp, professional and confident. No filler words.]

💡 Trade Management:
• Close 50% at TP1 -- move SL to breakeven
• Hold 50% for TP2/TP3
• Exit immediately on candle close beyond SL

⚠️ Not financial advice. Trade at your own risk.
🔔 MTU Premium | XAUUSD Signals

Output ONLY the message. No preamble or extra text."""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":    "claude-sonnet-4-20250514",
            "max_tokens": 700,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"].strip()


# ── Telegram sender ────────────────────────────────────────────────────────────

def send_to_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id":    TELEGRAM_CHANNEL_ID,
        "text":       message,
        "parse_mode": "HTML",
    }, timeout=15)
    r.raise_for_status()


# ── Daily morning update ───────────────────────────────────────────────────────

def generate_morning_update(d: dict) -> str:
    price      = d["price"]
    prev_close = d["prev_close"]
    change     = round(price - prev_close, 2)
    change_pct = round((change / prev_close) * 100, 2)
    direction  = "🟢" if change >= 0 else "🔴"
    sign       = "+" if change >= 0 else ""
    date_str   = datetime.now(timezone.utc).strftime("%A, %d %B %Y")

    candles   = d["candles"]
    structure = detect_market_structure(candles)
    sr        = find_sr_levels(candles, price, d["atr"])
    sd        = find_sd_zones(candles, price, d["atr"])

    structure_label = {"bullish": "Bullish (Menaik) ⬆️",
                       "bearish": "Bearish (Menurun) ⬇️",
                       "ranging": "Ranging (Mendatar) ↔️"}.get(structure, "Ranging ↔️")

    demand_str = (f"{sd['nearest_demand'][0]}-{sd['nearest_demand'][1]}"
                  if sd.get("nearest_demand") else "No nearby zone")
    supply_str = (f"{sd['nearest_supply'][0]}-{sd['nearest_supply'][1]}"
                  if sd.get("nearest_supply") else "No nearby zone")

    prompt = f"""You are a professional XAUUSD market analyst for MTU Premium Telegram channel.

Write a daily morning market update using EXACTLY this format:

🌅 GOOD MORNING, TRADERS!
📅 {date_str}
━━━━━━━━━━━━━━━━━━━━━
🥇 XAUUSD DAILY OUTLOOK

💰 Current Price: {price}
{direction} Change: {sign}{change} ({sign}{change_pct}%)

📊 Technical Summary:
• Structure: {structure_label}
• RSI(14): {d['rsi']:.1f}
• EMA9: {d['ema9']:.2f} | EMA21: {d['ema21']:.2f}
• ATR(14): {d['atr']:.2f}

🗺 Key Levels Today:
• Resistance: {sr['resistance']}
• Support: {sr['support']}
• Supply Zone: {supply_str}
• Demand Zone: {demand_str}

🧭 Bias: {structure_label}
━━━━━━━━━━━━━━━━━━━━━
📝 Today's Outlook:
[Write exactly 3 sharp sentences in English:
 1. Comment on the current market structure and momentum.
 2. Highlight the most important S&R and S&D levels traders must watch today.
 3. Give a clear actionable bias -- buy dips, sell rallies, or wait for breakout confirmation.
 Keep it professional and concise.]

🕐 Sessions Today (MYT):
🌏 Asia: 08:00 - 16:00
🇬🇧 London: 15:00 - 00:00
🇺🇸 New York: 21:00 - 05:00

⚠️ Not financial advice. Trade responsibly.
🔔 MTU Premium | XAUUSD Signals

Output ONLY the message. No preamble or extra text."""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":    "claude-sonnet-4-20250514",
            "max_tokens": 700,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"].strip()


def morning_update():
    now_utc = datetime.now(timezone.utc)
    print(f"[{now_utc.strftime('%Y-%m-%d %H:%M')} UTC] Morning update running...")
    try:
        data = fetch_market_data()
    except Exception as e:
        print(f"Data fetch failed: {e}"); return
    try:
        message = generate_morning_update(data)
    except Exception as e:
        print(f"AI generation failed: {e}"); return
    print("-" * 50)
    print(message)
    print("-" * 50)
    try:
        send_to_telegram(message)
        print("Kemaskini pagi telah dihantar!")
    except Exception as e:
        print(f"Telegram send failed: {e}")


# ── Main signal loop ───────────────────────────────────────────────────────────

def main():
    now_utc  = datetime.now(timezone.utc)
    utc_hour = now_utc.hour
    session  = get_current_session(utc_hour)

    print(f"[{now_utc.strftime('%Y-%m-%d %H:%M')} UTC] Bot running...")
    print(f"Session: {session}")

    if not is_active_hours(utc_hour):
        print(f"Off-hours (2AM-7AM MYT). Bot resting.")
        return

    state = load_state()
    if state["count"] >= MAX_SIGNALS_PER_DAY:
        print(f"Had harian dicapai ({MAX_SIGNALS_PER_DAY}). Selesai untuk hari ini.")
        return
    if not cooldown_ok(state):
        print(f"Cooldown aktif -- {COOLDOWN_MINUTES} minit antara isyarat.")
        return

    print("Mengambil data XAUUSD 15-min dari Twelve Data...")
    try:
        data = fetch_market_data()
    except ValueError as e:
        print(f"Data fetch error: {e}"); return
    except Exception as e:
        print(f"Network error: {e}"); return

    print(f"Price={data['price']:.2f}  RSI={data['rsi']:.1f}  "
          f"EMA9={data['ema9']:.2f}  EMA21={data['ema21']:.2f}  "
          f"ATR={data['atr']:.2f}  AvgATR={data['avg_atr']:.2f}")

    structure = detect_market_structure(data["candles"])
    sr        = find_sr_levels(data["candles"], data["price"], data["atr"])
    sd        = find_sd_zones(data["candles"], data["price"], data["atr"])
    cp        = detect_candle_patterns(data["candles"], data["atr"])

    print(f"Structure={structure}  "
          f"NearSupport={sr['near_support']}  NearResistance={sr['near_resistance']}  "
          f"InDemand={sd['in_demand']}  InSupply={sd['in_supply']}  "
          f"Patterns={cp['patterns']}")

    signal_type, reasons, confidence, score, analysis = check_conditions(data)

    if not signal_type:
        print("Tiada persediaan yang sah. Skor di bawah ambang.")
        return

    print(f"Setup: {signal_type} [{confidence}] Score={score}/8")
    print(f"Reasons: {' | '.join(reasons)}")
    print("Menjana isyarat dengan Claude AI...")

    try:
        message = generate_signal_message(
            signal_type, data, confidence, session, reasons, score, analysis)
    except Exception as e:
        print(f"AI generation failed: {e}"); return

    print("-" * 50)
    print(message)
    print("-" * 50)

    try:
        send_to_telegram(message)
        state["count"]           += 1
        state["last_signal_utc"]  = now_utc.isoformat()
        save_state(state)
        print(f"Dihantar! Isyarat hari ini: {state['count']}/{MAX_SIGNALS_PER_DAY}")
    except Exception as e:
        print(f"Telegram send failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  SIGNAL TRACKER -- monitors open signals and posts live updates
# ══════════════════════════════════════════════════════════════════════════════

# Use /data for persistent storage (Railway Volume)
# Falls back to current directory if /data not mounted
import pathlib
_DATA_DIR = pathlib.Path("/data") if pathlib.Path("/data").exists() else pathlib.Path(".")
OPEN_SIGNALS_FILE = str(_DATA_DIR / "open_signals.json")

# How many pips of floating profit triggers a running-profit update
RUNNING_PROFIT_NOTIFY_INTERVAL = 5.0   # every $5 move after entry


def load_open_signals() -> list:
    try:
        with open(OPEN_SIGNALS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_open_signals(signals: list):
    with open(OPEN_SIGNALS_FILE, "w") as f:
        json.dump(signals, f, indent=2)


def register_signal(signal_type: str, entry: float, sl: float,
                    tp1: float, tp2: float, tp3: float,
                    confidence: str, session: str):
    """Save a newly sent signal so the tracker can monitor it."""
    signals = load_open_signals()
    sig = {
        "id":           datetime.now(timezone.utc).strftime("%Y%m%d%H%M"),
        "type":         signal_type,
        "entry":        entry,
        "sl":           sl,
        "tp1":          tp1,
        "tp2":          tp2,
        "tp3":          tp3,
        "confidence":   confidence,
        "session":      session,
        "status":       "open",        # open | tp1_hit | tp2_hit | closed
        "tp1_hit":      False,
        "tp2_hit":      False,
        "tp3_hit":      False,
        "sl_hit":       False,
        "last_notified_profit": 0.0,   # last floating profit we notified at
        "opened_utc":   datetime.now(timezone.utc).isoformat(),
    }
    signals.append(sig)
    save_open_signals(signals)
    print(f"Signal {sig['id']} registered for tracking.")


def fetch_current_price() -> float:
    """Quick single-endpoint price fetch for the tracker."""
    url = "https://api.twelvedata.com/price"
    r = requests.get(url, params={
        "symbol": SYMBOL,
        "apikey": TWELVEDATA_API_KEY,
    }, timeout=10)
    r.raise_for_status()
    data = r.json()
    if "price" not in data:
        raise ValueError(f"Price fetch failed: {data}")
    return float(data["price"])


def pips(a: float, b: float) -> float:
    return round(abs(a - b), 2)


def format_tracker_message(sig: dict, event: str, current_price: float) -> str:
    """Build the Telegram update message for a signal event."""

    signal_id  = sig["id"]
    direction  = sig["type"]
    entry      = sig["entry"]
    sl         = sig["sl"]
    tp1        = sig["tp1"]
    tp2        = sig["tp2"]
    tp3        = sig["tp3"]

    # Floating P&L in dollars (1 lot XAUUSD = $100/pip, we use pips as proxy)
    if direction == "BUY":
        floating = round(current_price - entry, 2)
    else:
        floating = round(entry - current_price, 2)

    sign   = "+" if floating >= 0 else ""
    pl_str = f"{sign}{floating}"

    if event == "tp1_hit":
        emoji   = "✅"
        title   = "TP1 HIT!"
        action  = "Close 50% of position now.\nMove Stop Loss to breakeven entry."
        status  = f"Target 1 reached at {current_price:.2f}"

    elif event == "tp2_hit":
        emoji   = "✅✅"
        title   = "TP2 HIT!"
        action  = "Close another 50% of position.\nTrail remaining SL below last swing."
        status  = f"Target 2 reached at {current_price:.2f}"

    elif event == "tp3_hit":
        emoji   = "🎯"
        title   = "TP3 HIT -- FULL CLOSE!"
        action  = "Close entire position. Signal complete. Well done!"
        status  = f"Full target reached at {current_price:.2f}"

    elif event == "sl_hit":
        emoji   = "🛑"
        title   = "STOP LOSS HIT"
        action  = "Signal closed. Cut losses, protect your capital."
        status  = f"SL triggered at {current_price:.2f}"

    elif event == "running_profit":
        emoji   = "📊"
        title   = "LIVE UPDATE"
        action  = "Consider partial close if needed."
        status  = f"Floating P&L: {pl_str} pips"

    else:
        return ""

    direction_emoji = "📈" if direction == "BUY" else "📉"

    msg = (
        f"{emoji} SIGNAL UPDATE -- {direction} {direction_emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 Signal ID: {signal_id}\n"
        f"📍 {title}\n"
        f"💰 Current Price: {current_price:.2f}\n"
        f"🎯 Entry: {entry}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Levels:\n"
        f"  SL: {sl}  |  TP1: {tp1}  |  TP2: {tp2}  |  TP3: {tp3}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Status: {status}\n"
        f"💡 Action: {action}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Not financial advice.\n"
        f"🔔 MTU Premium | XAUUSD Signals"
    )
    return msg


def check_and_update_signals():
    """
    Load all open signals, fetch current price,
    check each signal for TP/SL hits and running profit,
    post updates to Telegram, and save updated state.
    """
    signals = load_open_signals()
    if not signals:
        print("Tiada isyarat terbuka untuk dipantau.")
        return

    # Filter to only active signals
    active = [s for s in signals if s["status"] not in ("closed", "sl_hit")]
    if not active:
        print("Semua isyarat telah ditutup.")
        return

    print(f"Memantau {len(active)} isyarat terbuka...")

    try:
        price = fetch_current_price()
    except Exception as e:
        print(f"Price fetch failed for tracker: {e}")
        return

    print(f"Harga semasa: {price:.2f}")

    updated = False

    for sig in signals:
        if sig["status"] in ("closed", "sl_hit"):
            continue

        direction = sig["type"]
        entry     = sig["entry"]
        sl        = sig["sl"]
        tp1       = sig["tp1"]
        tp2       = sig["tp2"]
        tp3       = sig["tp3"]

        # ── Check SL hit ──────────────────────────────────────────────────────
        sl_hit = (direction == "BUY"  and price <= sl) or \
                 (direction == "SELL" and price >= sl)

        if sl_hit and not sig["sl_hit"]:
            print(f"Signal {sig['id']}: SL hit at {price:.2f}")
            msg = format_tracker_message(sig, "sl_hit", price)
            try:
                send_to_telegram(msg)
            except Exception as e:
                print(f"Telegram failed: {e}")
            sig["sl_hit"] = True
            sig["status"] = "sl_hit"
            updated = True
            continue   # no more checks needed

        # ── Check TP3 hit ─────────────────────────────────────────────────────
        tp3_hit = (direction == "BUY"  and price >= tp3) or \
                  (direction == "SELL" and price <= tp3)

        if tp3_hit and not sig["tp3_hit"]:
            print(f"Signal {sig['id']}: TP3 hit at {price:.2f}")
            msg = format_tracker_message(sig, "tp3_hit", price)
            try:
                send_to_telegram(msg)
            except Exception as e:
                print(f"Telegram failed: {e}")
            sig["tp3_hit"] = True
            sig["tp2_hit"] = True
            sig["tp1_hit"] = True
            sig["status"]  = "closed"
            updated = True
            continue

        # ── Check TP2 hit ─────────────────────────────────────────────────────
        tp2_hit = (direction == "BUY"  and price >= tp2) or \
                  (direction == "SELL" and price <= tp2)

        if tp2_hit and not sig["tp2_hit"]:
            print(f"Signal {sig['id']}: TP2 hit at {price:.2f}")
            msg = format_tracker_message(sig, "tp2_hit", price)
            try:
                send_to_telegram(msg)
            except Exception as e:
                print(f"Telegram failed: {e}")
            sig["tp2_hit"] = True
            sig["tp1_hit"] = True
            sig["status"]  = "tp2_hit"
            updated = True
            continue

        # ── Check TP1 hit ─────────────────────────────────────────────────────
        tp1_hit = (direction == "BUY"  and price >= tp1) or \
                  (direction == "SELL" and price <= tp1)

        if tp1_hit and not sig["tp1_hit"]:
            print(f"Signal {sig['id']}: TP1 hit at {price:.2f}")
            msg = format_tracker_message(sig, "tp1_hit", price)
            try:
                send_to_telegram(msg)
            except Exception as e:
                print(f"Telegram failed: {e}")
            sig["tp1_hit"] = True
            sig["status"]  = "tp1_hit"
            updated = True
            # Don't continue -- also check running profit below

        # ── Running profit update ─────────────────────────────────────────────
        if direction == "BUY":
            floating = round(price - entry, 2)
        else:
            floating = round(entry - price, 2)

        last_notified = sig.get("last_notified_profit", 0.0)

        # Notify every RUNNING_PROFIT_NOTIFY_INTERVAL pips of profit
        if (floating > 0 and
                floating - last_notified >= RUNNING_PROFIT_NOTIFY_INTERVAL):
            print(f"Signal {sig['id']}: Running profit update +{floating}")
            msg = format_tracker_message(sig, "running_profit", price)
            try:
                send_to_telegram(msg)
            except Exception as e:
                print(f"Telegram failed: {e}")
            sig["last_notified_profit"] = floating
            updated = True

    if updated:
        save_open_signals(signals)
        print("Status isyarat disimpan.")
    else:
        print("Tiada kemaskini isyarat dicetuskan.")


# ── Updated main -- now also saves signal for tracking ─────────────────────────

def main():
    now_utc  = datetime.now(timezone.utc)
    utc_hour = now_utc.hour
    session  = get_current_session(utc_hour)

    print(f"[{now_utc.strftime('%Y-%m-%d %H:%M')} UTC] Bot running...")
    print(f"Session: {session}")

    if not is_active_hours(utc_hour):
        print(f"Off-hours (2AM-7AM MYT). Bot resting.")
        return

    # ── Always run tracker first on every 30-min cycle ───────────────────────
    print("\n--- Running signal tracker ---")
    check_and_update_signals()
    print("--- Tracker done ---\n")

    state = load_state()
    if state["count"] >= MAX_SIGNALS_PER_DAY:
        print(f"Had harian dicapai ({MAX_SIGNALS_PER_DAY}). Selesai untuk hari ini.")
        return
    if not cooldown_ok(state):
        print(f"Cooldown aktif -- {COOLDOWN_MINUTES} minit antara isyarat.")
        return

    print("Mengambil data XAUUSD 15-min dari Twelve Data...")
    try:
        data = fetch_market_data()
    except ValueError as e:
        print(f"Data fetch error: {e}"); return
    except Exception as e:
        print(f"Network error: {e}"); return

    print(f"Price={data['price']:.2f}  RSI={data['rsi']:.1f}  "
          f"EMA9={data['ema9']:.2f}  EMA21={data['ema21']:.2f}  "
          f"ATR={data['atr']:.2f}  AvgATR={data['avg_atr']:.2f}")

    structure = detect_market_structure(data["candles"])
    sr        = find_sr_levels(data["candles"], data["price"], data["atr"])
    sd        = find_sd_zones(data["candles"], data["price"], data["atr"])
    cp        = detect_candle_patterns(data["candles"], data["atr"])

    print(f"Structure={structure}  "
          f"NearSupport={sr['near_support']}  NearResistance={sr['near_resistance']}  "
          f"InDemand={sd['in_demand']}  InSupply={sd['in_supply']}  "
          f"Patterns={cp['patterns']}")

    # -- News blackout check -- block signal during high impact news
    news_blocked, news_reason = is_news_blackout(now_utc)
    if news_blocked:
        print(f"NEWS BLACKOUT: {news_reason} -- skipping signal for safety.")
        return

    signal_type, reasons, confidence, score, analysis = check_conditions(data)

    if not signal_type:
        print("Tiada persediaan yang sah. Skor di bawah ambang.")
        return

    print(f"Setup: {signal_type} [{confidence}] Score={score}/8")
    print(f"Reasons: {' | '.join(reasons)}")
    print("Menjana isyarat dengan Claude AI...")

    try:
        message = generate_signal_message(
            signal_type, data, confidence, session, reasons, score, analysis)
    except Exception as e:
        print(f"AI generation failed: {e}"); return

    # ── Calculate levels (same logic as generate_signal_message) ─────────────
    price = data["price"]
    atr   = data["atr"]
    MAX_SL_PIPS = 50
    if signal_type == "BUY":
        entry   = price
        sl_sr   = round(sr.get("support", price - atr * 1.2) - atr * 0.3, 2)
        sl_atr  = round(price - atr * 1.2, 2)
        sl_raw  = max(sl_sr, sl_atr)
        sl      = round(max(sl_raw, price - MAX_SL_PIPS), 2)
        risk     = round(price - sl, 2)
        tp1      = round(price + risk * 1.0, 2)
        tp2      = round(price + risk * 2.0, 2)
        tp3_base = round(price + risk * 3.0, 2)
        tp3_sr   = sr.get("resistance", tp3_base)
        tp3      = round(tp3_sr if tp3_sr > tp2 else tp3_base, 2)
    else:
        entry   = price
        sl_sr   = round(sr.get("resistance", price + atr * 1.2) + atr * 0.3, 2)
        sl_atr  = round(price + atr * 1.2, 2)
        sl_raw  = min(sl_sr, sl_atr)
        sl      = round(min(sl_raw, price + MAX_SL_PIPS), 2)
        risk     = round(sl - price, 2)
        tp1      = round(price - risk * 1.0, 2)
        tp2      = round(price - risk * 2.0, 2)
        tp3_base = round(price - risk * 3.0, 2)
        tp3_sr   = sr.get("support", tp3_base)
        tp3      = round(tp3_sr if tp3_sr < tp2 else tp3_base, 2)

    print("-" * 50)
    print(message)
    print("-" * 50)

    try:
        send_to_telegram(message)
        state["count"]           += 1
        state["last_signal_utc"]  = now_utc.isoformat()
        save_state(state)
        # Register signal for live tracking
        register_signal(signal_type, entry, sl, tp1, tp2, tp3,
                        confidence, session)
        print(f"Dihantar! Isyarat hari ini: {state['count']}/{MAX_SIGNALS_PER_DAY}")
    except Exception as e:
        print(f"Telegram send failed: {e}")


# ── Entry point ────────────────────────────────────────────────────────────────

import time

# ══════════════════════════════════════════════════════════════════════════════
#  HIGH IMPACT NEWS BLACKOUT FILTER
# ══════════════════════════════════════════════════════════════════════════════

# Fixed weekly/monthly high impact US news schedule (UTC times)
# Bot will block signals 15 min before and 20 min after these events
HIGH_IMPACT_NEWS = [
    # Weekly recurring
    {"name": "Initial Jobless Claims",  "day": 3, "hour": 12, "min": 30},  # Thursday 12:30 UTC
    {"name": "Crude Oil Inventories",   "day": 2, "hour": 14, "min": 30},  # Wednesday 14:30 UTC

    # Monthly recurring (approximate -- varies by month)
    {"name": "Non-Farm Payrolls (NFP)", "day": 4, "hour": 12, "min": 30},  # First Friday 12:30 UTC
    {"name": "CPI",                     "day": 1, "hour": 12, "min": 30},  # Usually Tuesday/Wednesday
    {"name": "PPI",                     "day": 2, "hour": 12, "min": 30},  # Usually Wednesday
    {"name": "Retail Sales",            "day": 2, "hour": 12, "min": 30},  # Usually Wednesday
    {"name": "FOMC Statement",          "day": 2, "hour": 18, "min": 0},   # Wednesday 18:00 UTC
    {"name": "FOMC Press Conference",   "day": 2, "hour": 18, "min": 30},  # Wednesday 18:30 UTC
    {"name": "GDP",                     "day": 2, "hour": 12, "min": 30},  # Usually Wednesday
    {"name": "PCE Price Index",         "day": 4, "hour": 12, "min": 30},  # Usually Friday
    {"name": "ISM Manufacturing",       "day": 0, "hour": 14, "min": 0},   # First Monday 14:00 UTC
    {"name": "ISM Services",            "day": 2, "hour": 14, "min": 0},   # First Wednesday
    {"name": "Fed Chair Speech",        "day": -1, "hour": -1, "min": -1}, # Ad-hoc -- skip
]

# FOMC dates 2025-2026 (exact dates, UTC)
FOMC_DATES = [
    (2025, 1, 29), (2025, 3, 19), (2025, 5, 7),
    (2025, 6, 18), (2025, 7, 30), (2025, 9, 17),
    (2025, 10, 29),(2025, 12, 10),
    (2026, 1, 28), (2026, 3, 18), (2026, 4, 29),
    (2026, 6, 17), (2026, 7, 29), (2026, 9, 16),
    (2026, 10, 28),(2026, 12, 9),
]

# NFP is always first Friday of month at 12:30 UTC
# CPI is usually 2nd or 3rd week -- we check via NewsAPI

BLACKOUT_BEFORE_MIN = 15   # block 15 min before news
BLACKOUT_AFTER_MIN  = 20   # block 20 min after news


def is_fomc_blackout(now_utc: datetime) -> tuple:
    """Check if current time is within FOMC blackout window."""
    today = (now_utc.year, now_utc.month, now_utc.day)
    for y, m, d in FOMC_DATES:
        if (y, m, d) == today:
            # FOMC announcement at 18:00 UTC, press conference 18:30 UTC
            # Blackout: 17:45 UTC to 19:30 UTC
            fomc_start = now_utc.replace(hour=17, minute=45, second=0, microsecond=0)
            fomc_end   = now_utc.replace(hour=19, minute=30, second=0, microsecond=0)
            if fomc_start <= now_utc <= fomc_end:
                return True, "FOMC Statement/Press Conference"
    return False, ""


def is_nfp_blackout(now_utc: datetime) -> tuple:
    """Check if today is NFP day (first Friday of month) and within blackout."""
    if now_utc.weekday() != 4:  # Not Friday
        return False, ""
    # Check if first Friday of month
    if now_utc.day > 7:
        return False, ""
    # NFP at 12:30 UTC -- blackout 12:15 to 12:50
    nfp_start = now_utc.replace(hour=12, minute=15, second=0, microsecond=0)
    nfp_end   = now_utc.replace(hour=12, minute=50, second=0, microsecond=0)
    if nfp_start <= now_utc <= nfp_end:
        return True, "Non-Farm Payrolls (NFP)"
    return False, ""


def check_newsapi_breaking_news(now_utc: datetime) -> tuple:
    """Check NewsAPI for breaking high-impact news in last 30 min."""
    if not NEWSAPI_KEY:
        return False, ""

    keywords = [
        "Federal Reserve rate decision",
        "CPI inflation data",
        "PPI producer price",
        "FOMC meeting",
        "Fed Powell speech",
        "US GDP data",
        "NFP jobs report",
        "PCE inflation",
        "ISM manufacturing",
    ]

    try:
        from datetime import timedelta
        since = (now_utc - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for keyword in keywords[:3]:  # Check top 3 to save API calls
            r = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q":        keyword,
                    "from":     since,
                    "sortBy":   "publishedAt",
                    "pageSize": 1,
                    "apiKey":   NEWSAPI_KEY,
                },
                timeout=8,
            )
            data = r.json()
            if data.get("totalResults", 0) > 0:
                return True, keyword
    except Exception as e:
        print(f"NewsAPI check failed: {e}")

    return False, ""


def is_news_blackout(now_utc: datetime) -> tuple:
    """
    Master check -- returns (is_blackout, reason).
    Checks FOMC dates, NFP, and breaking news.
    """
    # Check FOMC
    blocked, reason = is_fomc_blackout(now_utc)
    if blocked:
        return True, reason

    # Check NFP
    blocked, reason = is_nfp_blackout(now_utc)
    if blocked:
        return True, reason

    # Check JoblessClaims (Thursday 12:30 UTC)
    if now_utc.weekday() == 3:  # Thursday
        claims_start = now_utc.replace(hour=12, minute=15, second=0, microsecond=0)
        claims_end   = now_utc.replace(hour=12, minute=50, second=0, microsecond=0)
        if claims_start <= now_utc <= claims_end:
            return True, "Initial Jobless Claims"

    # Check NewsAPI for breaking news (only during US session)
    if 12 <= now_utc.hour <= 16:
        blocked, reason = check_newsapi_breaking_news(now_utc)
        if blocked:
            return True, f"Breaking news: {reason}"

    return False, ""


# ══════════════════════════════════════════════════════════════════════════════
#  US SESSION FUNDAMENTAL NEWS UPDATE
# ══════════════════════════════════════════════════════════════════════════════

def fetch_gold_news() -> list:
    """Fetch latest gold-related news from NewsAPI. Returns list of articles."""
    if not NEWSAPI_KEY:
        print("NEWSAPI_KEY not set -- skipping news fetch.")
        return []

    queries = [
        "gold XAU price",
        "Federal Reserve interest rate",
        "USD dollar strength",
        "geopolitical gold safe haven",
    ]

    articles = []
    for q in queries:
        try:
            r = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q":          q,
                    "language":   "en",
                    "sortBy":     "publishedAt",
                    "pageSize":   3,
                    "apiKey":     NEWSAPI_KEY,
                },
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            for a in data.get("articles", []):
                if a.get("title") and a.get("description"):
                    articles.append({
                        "title":       a["title"],
                        "description": a["description"],
                        "source":      a.get("source", {}).get("name", "Unknown"),
                        "url":         a.get("url", ""),
                    })
        except Exception as e:
            print(f"News fetch error for '{q}': {e}")

    # Deduplicate by title
    seen  = set()
    unique = []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)

    return unique[:8]  # Max 8 articles


def generate_fundamental_update(articles: list, price_data: dict) -> str:
    """Use Claude AI to summarize news and give fundamental outlook for gold."""

    price = price_data.get("price", "N/A")
    rsi   = price_data.get("rsi",   "N/A")

    # Build news context for Claude
    news_context = ""
    for i, a in enumerate(articles, 1):
        news_context += f"{i}. [{a['source']}] {a['title']}\n   {a['description']}\n\n"

    if not news_context:
        news_context = "No live news available. Use your knowledge of current macro environment."

    now_myt = datetime.now(timezone.utc)
    date_str = now_myt.strftime("%A, %d %B %Y")

    prompt = f"""You are a professional gold (XAUUSD) fundamental analyst for MTU Premium Telegram channel.

Today is {date_str}. Current XAUUSD Price: {price} | RSI: {rsi}

Here are the latest news headlines:
{news_context}

Write a US Session fundamental update using EXACTLY this format:

🇺🇸 US SESSION FUNDAMENTAL UPDATE
📅 {date_str}
━━━━━━━━━━━━━━━━━━━━━
💰 XAUUSD Current Price: {price}

📰 KEY FUNDAMENTALS:

💵 USD Strength/Weakness:
[1-2 sentences about USD based on news above]

🥇 Gold Demand/Supply:
[1-2 sentences about gold demand, ETF flows, central bank buying]

🏦 Fed & Interest Rates:
[1-2 sentences about Fed policy, rate expectations]

🌍 Geopolitical Factors:
[1-2 sentences about geopolitical events affecting gold]
━━━━━━━━━━━━━━━━━━━━━
📊 FUNDAMENTAL BIAS FOR US SESSION:
[ONE of these: 🟢 BULLISH | 🔴 BEARISH | 🟡 NEUTRAL]

📝 Summary:
[Write 2-3 sentences explaining the overall fundamental picture for gold during US session today. Be specific and actionable.]

⚡ Key levels to watch: Support {round(float(price)-20, 2) if price != 'N/A' else 'N/A'} | Resistance {round(float(price)+20, 2) if price != 'N/A' else 'N/A'}

⚠️ Not financial advice. Trade at your own risk.
🔔 MTU Premium | XAUUSD Signals

Output ONLY the message. No preamble or extra text."""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      "claude-sonnet-4-20250514",
            "max_tokens": 800,
            "messages":   [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"].strip()


def us_session_fundamental():
    """Called once when US session starts -- 13:00 UTC (9PM MYT)."""
    now_utc = datetime.now(timezone.utc)
    print(f"[{now_utc.strftime('%Y-%m-%d %H:%M')} UTC] US Session fundamental update running...")

    # Fetch current price data
    try:
        price_data = fetch_market_data()
    except Exception as e:
        print(f"Price fetch failed: {e}")
        price_data = {}

    # Fetch news
    print("Fetching gold news...")
    articles = fetch_gold_news()
    print(f"Found {len(articles)} articles")

    # Generate AI summary
    print("Generating fundamental update with Claude AI...")
    try:
        message = generate_fundamental_update(articles, price_data)
    except Exception as e:
        print(f"AI generation failed: {e}")
        return

    print("-" * 50)
    print(message)
    print("-" * 50)

    try:
        send_to_telegram(message)
        print("US Session fundamental update sent!")
    except Exception as e:
        print(f"Telegram send failed: {e}")


import time

def run_loop():
    """
    Continuous loop for Railway/VPS hosting.
    Checks signal every 60 seconds.
    Sends morning update once per day at 00:00 UTC (08:00 MYT).
    """
    print("MTU Premium Signal Bot starting -- Railway mode...")
    morning_sent_date      = None
    fundamental_sent_date  = None

    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            today   = now_utc.date()

            # ── Morning update once per day at 00:00 UTC (08:00 MYT) ─────────
            if now_utc.hour == 0 and now_utc.minute < 2 and morning_sent_date != today:
                print("Sending morning update...")
                morning_update()
                morning_sent_date = today

            # ── US Session fundamental update at 13:00 UTC (21:00 MYT) ───────
            if now_utc.hour == 13 and now_utc.minute < 2 and fundamental_sent_date != today:
                print("Sending US Session fundamental update...")
                us_session_fundamental()
                fundamental_sent_date = today

            # ── Signal check ──────────────────────────────────────────────────
            main()

        except Exception as e:
            print(f"Loop error: {e}")

        # Sleep 120 seconds before next check (keeps Twelve Data within 800 credits/day)
        print("Sleeping 120 seconds...")
        time.sleep(120)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "morning":
        morning_update()
    elif len(sys.argv) > 1 and sys.argv[1] == "once":
        main()
    else:
        run_loop()
