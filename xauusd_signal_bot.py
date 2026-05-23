# -*- coding: utf-8 -*-
# XAUUSD AI Scalping Signal Bot -- MTU Premium
# Strategy: S&R, S&D, Engulfing, Market Structure, RSI, EMA, MACD
# Timeframe: 15-min | Sessions: Asia, London, New York
# Railway Edition: persistent 24/7 loop, /data volume, no GitHub Actions dependency

import os
import json
import sys
import time
import requests
import pathlib as _pathlib
from datetime import datetime, date, timezone, timedelta

# ── Secrets ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
TWELVEDATA_API_KEY  = os.environ["TWELVEDATA_API_KEY"]
GITHUB_TOKEN        = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO         = os.environ.get("GITHUB_REPO", "Isyrafimran25/MTUPremiumSignal")

# ── Config ────────────────────────────────────────────────────────────────────
MAX_SIGNALS_PER_DAY = 10
COOLDOWN_MINUTES    = 90
SYMBOL              = "XAU/USD"
INTERVAL            = "15min"

# ── Persistent storage -- Railway /data volume ────────────────────────────────
_DATA_DIR         = _pathlib.Path("/data") if _pathlib.Path("/data").exists() else _pathlib.Path(".")
SIGNAL_COUNT_FILE = str(_DATA_DIR / "signal_count.json")
OPEN_SIGNALS_FILE = str(_DATA_DIR / "open_signals.json")

print(f"[INIT] Storage: {_DATA_DIR} ({'✅ persistent /data volume' if str(_DATA_DIR) == '/data' else '⚠️  local dir -- add Railway Volume!'})", flush=True)

# ── Session helpers ───────────────────────────────────────────────────────────
SESSIONS = {
    "Asia":     (0,  8),
    "London":   (7,  16),
    "New York": (13, 21),
}

def is_active_hours(utc_hour: int, utc_weekday: int = -1) -> bool:
    if utc_weekday in (5, 6):
        return False
    return utc_hour not in (18, 19, 20, 21, 22)

def get_fetch_interval(utc_hour: int) -> int:
    HIGH_ACTIVITY = (0, 1, 2, 3, 7, 8, 9, 13, 14, 15)
    return 180 if utc_hour in HIGH_ACTIVITY else 300

def get_current_session(utc_hour: int, utc_weekday: int = -1) -> str:
    if not is_active_hours(utc_hour, utc_weekday):
        return "Off-hours"
    active = [n for n, (s, e) in SESSIONS.items() if s <= utc_hour < e]
    return " / ".join(active) if active else "Asia"

# ── State management ──────────────────────────────────────────────────────────
def load_state() -> dict:
    # Try local /data first
    try:
        with open(SIGNAL_COUNT_FILE) as f:
            data = json.load(f)
        if data.get("date") == str(date.today()):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Fallback to GitHub
    content_gh, _ = github_get_file("signal_count.json")
    if content_gh:
        try:
            data = json.loads(content_gh)
            if data.get("date") == str(date.today()):
                print("[STATE] Loaded from GitHub fallback.", flush=True)
                with open(SIGNAL_COUNT_FILE, "w") as f:
                    json.dump(data, f)
                return data
        except:
            pass

    return {"date": str(date.today()), "count": 0, "last_signal_utc": None}

def save_state(state: dict):
    with open(SIGNAL_COUNT_FILE, "w") as f:
        json.dump(state, f)
    github_push_file("signal_count.json", json.dumps(state), "update signal state")

# ── GitHub sync (backup only, not primary storage) ────────────────────────────
def github_get_file(filename: str) -> tuple:
    if not GITHUB_TOKEN:
        return None, None
    try:
        import base64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
        r = requests.get(url, headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept":        "application/vnd.github.v3+json",
        }, timeout=10)
        if r.status_code == 200:
            data    = r.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data["sha"]
        return None, None
    except Exception as e:
        print(f"[GH] get failed: {e}", flush=True)
        return None, None

def github_push_file(filename: str, content: str, msg: str = "update"):
    if not GITHUB_TOKEN:
        return
    try:
        import base64
        _, sha = github_get_file(filename)
        url    = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
        payload = {
            "message": msg,
            "content": base64.b64encode(content.encode()).decode(),
        }
        if sha:
            payload["sha"] = sha
        r = requests.put(url, headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept":        "application/vnd.github.v3+json",
        }, json=payload, timeout=10)
        if r.status_code in (200, 201):
            print(f"[GH] {filename} synced ✅", flush=True)
        else:
            print(f"[GH] push failed: {r.status_code}", flush=True)
    except Exception as e:
        print(f"[GH] push error: {e}", flush=True)

def cooldown_ok(state: dict) -> bool:
    last = state.get("last_signal_utc")
    if not last:
        return True
    diff = (datetime.now(timezone.utc) -
            datetime.fromisoformat(last)).total_seconds() / 60
    return diff >= COOLDOWN_MINUTES

# ── Open signals ──────────────────────────────────────────────────────────────
def load_open_signals() -> list:
    try:
        with open(OPEN_SIGNALS_FILE) as f:
            data = json.load(f)
        if data:
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # Fallback to GitHub
    content, _ = github_get_file("open_signals.json")
    if content:
        try:
            return json.loads(content)
        except:
            pass
    return []

def save_open_signals(signals: list):
    with open(OPEN_SIGNALS_FILE, "w") as f:
        json.dump(signals, f, indent=2)
    github_push_file("open_signals.json", json.dumps(signals, indent=2), "update open signals")

# ── Twelve Data fetcher ───────────────────────────────────────────────────────
def td_get(endpoint: str, **params) -> dict:
    url = f"https://api.twelvedata.com/{endpoint}"
    params.update({"symbol": SYMBOL, "interval": INTERVAL,
                   "apikey": TWELVEDATA_API_KEY})
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("status") == "error" or ("code" in data and data["code"] != 200):
        raise ValueError(f"TwelveData /{endpoint}: {data.get('message', data)}")
    return data

# ── Local indicator calculations ──────────────────────────────────────────────
def compute_ema(closes: list, period: int) -> list:
    k   = 2 / (period + 1)
    ema = closes[0]
    result = [ema]
    for price in closes[1:]:
        ema = price * k + ema * (1 - k)
        result.append(ema)
    return result

def compute_rsi(closes: list, period: int = 14) -> list:
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
            rs = avg_gain / avg_loss
            rsi_vals.append(round(100 - (100 / (1 + rs)), 2))
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    return rsi_vals if rsi_vals else [50.0]

def compute_atr(candles_asc: list, period: int = 14) -> list:
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
    return result

def compute_macd(closes: list, fast=12, slow=26, signal=9):
    ema_fast    = compute_ema(closes, fast)
    ema_slow    = compute_ema(closes, slow)
    macd_line   = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = compute_ema(macd_line, signal)
    return macd_line, signal_line

def get_h1_trend() -> str:
    try:
        data    = td_get("time_series", interval="1h", outputsize=20)
        candles = data.get("values", [])
        if len(candles) < 10:
            return "neutral"
        closes     = [float(c["close"]) for c in candles[:10]]
        avg_recent = sum(closes[:5]) / 5
        avg_older  = sum(closes[5:]) / 5
        diff       = avg_recent - avg_older
        atr_h1     = abs(float(candles[0]["high"]) - float(candles[0]["low"]))
        if diff > atr_h1 * 0.3:
            return "bullish"
        elif diff < -atr_h1 * 0.3:
            return "bearish"
        return "neutral"
    except Exception as e:
        print(f"[H1] trend fetch failed: {e}", flush=True)
        return "neutral"

def fetch_market_data() -> dict:
    print("[DATA] Fetching time_series + H1 trend...", flush=True)
    price_data = td_get("time_series", outputsize=60)

    candles = []
    for v in price_data["values"]:
        candles.append({
            "open":  float(v["open"]),
            "high":  float(v["high"]),
            "low":   float(v["low"]),
            "close": float(v["close"]),
            "dt":    v["datetime"],
        })

    candles_asc = list(reversed(candles))
    closes_asc  = [c["close"] for c in candles_asc]

    ema9_asc           = compute_ema(closes_asc, 9)
    ema21_asc          = compute_ema(closes_asc, 21)
    rsi_asc            = compute_rsi(closes_asc, 14)
    atr_asc            = compute_atr(candles_asc, 14)
    macd_asc, macd_sig = compute_macd(closes_asc)

    h1_trend    = get_h1_trend()
    entry_price = candles[0]["close"]

    print(f"[DATA] Price: {entry_price} | H1: {h1_trend.upper()} | RSI: {round(rsi_asc[-1], 2)}", flush=True)

    return {
        "candles":       candles,
        "price":         entry_price,
        "candle_close":  candles[0]["close"],
        "prev_close":    candles[1]["close"],
        "open":          candles[0]["open"],
        "high":          candles[0]["high"],
        "low":           candles[0]["low"],
        "rsi":           round(rsi_asc[-1], 2),
        "rsi_prev":      round(rsi_asc[-2], 2) if len(rsi_asc) >= 2 else round(rsi_asc[-1], 2),
        "ema9":          round(ema9_asc[-1], 2),
        "ema9_prev":     round(ema9_asc[-2], 2),
        "ema21":         round(ema21_asc[-1], 2),
        "ema21_prev":    round(ema21_asc[-2], 2),
        "macd":          round(macd_asc[-1], 4),
        "macd_signal":   round(macd_sig[-1], 4),
        "macd_prev":     round(macd_asc[-2], 4),
        "macd_sig_prev": round(macd_sig[-2], 4),
        "atr":           round(atr_asc[-1], 2),
        "avg_atr":       round(sum(atr_asc[-20:]) / min(20, len(atr_asc)), 2),
        "timestamp":     candles[0]["dt"],
        "h1_trend":      h1_trend,
    }

# ══════════════════════════════════════════════════════════════════════════════
# PRICE ACTION ANALYSIS ENGINE
# ══════════════════════════════════════════════════════════════════════════════
def detect_market_structure(candles: list) -> str:
    highs       = [c["high"] for c in candles[:20]]
    lows        = [c["low"]  for c in candles[:20]]
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
    highs       = [c["high"] for c in candles]
    lows        = [c["low"]  for c in candles]
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

    res_levels  = [l for l in cluster(swing_highs) if l > price]
    sup_levels  = [l for l in cluster(swing_lows)  if l < price]
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
    spike_filter = atr * 3.0

    for i in range(2, len(candles) - 1):
        c    = candles[i]
        body = abs(c["close"] - c["open"])
        if body > spike_filter:
            continue
        if body > threshold:
            if c["close"] > c["open"]:
                demand_zones.append((round(candles[i+1]["low"],  2),
                                     round(c["open"],             2)))
            else:
                supply_zones.append((round(c["open"],             2),
                                     round(candles[i+1]["high"],  2)))

    in_demand      = any(zl <= price <= zh for zl, zh in demand_zones[-5:])
    in_supply      = any(zl <= price <= zh for zl, zh in supply_zones[-5:])
    nearest_demand = next(((zl, zh) for zl, zh in reversed(demand_zones) if zh < price), None)
    nearest_supply = next(((zl, zh) for zl, zh in reversed(supply_zones) if zl > price), None)
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

    body0       = abs(c0["close"] - c0["open"])
    body1       = abs(c1["close"] - c1["open"])
    range0      = c0["high"] - c0["low"]
    upper_wick0 = c0["high"] - max(c0["open"], c0["close"])
    lower_wick0 = min(c0["open"], c0["close"]) - c0["low"]
    is_bull0    = c0["close"] > c0["open"]
    is_bull1    = c1["close"] > c1["open"]
    is_bear0    = c0["close"] < c0["open"]

    bullish_engulfing = (
        not is_bull1 and is_bull0 and
        c0["open"] <= c1["close"] and
        c0["close"] >= c1["open"] and
        body0 > body1
    )
    bearish_engulfing = (
        is_bull1 and is_bear0 and
        c0["open"] >= c1["close"] and
        c0["close"] <= c1["open"] and
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
    double_bottom = (abs(c0["low"]  - c2["low"])  < atr * 0.3 and c1["low"]  > c0["low"]  and is_bull0)
    double_top    = (abs(c0["high"] - c2["high"]) < atr * 0.3 and c1["high"] < c0["high"] and is_bear0)

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
        "bearish_patterns":  [p for p in patterns if "Bearish" in p or "Top"    in p],
        "bullish_engulfing": bullish_engulfing,
        "bearish_engulfing": bearish_engulfing,
        "bullish_pin":       bullish_pin,
        "bearish_pin":       bearish_pin,
        "double_bottom":     double_bottom,
        "double_top":        double_top,
        "inside_bar":        inside_bar,
        "inside_bar_bull":   inside_bar and is_bull0,
        "inside_bar_bear":   inside_bar and is_bear0,
    }

def check_conditions(d: dict) -> tuple:
    candles = d["candles"]
    price   = d["price"]
    rsi     = d["rsi"]
    atr     = d["atr"]
    avg_atr = d["avg_atr"]

    if atr < avg_atr * 0.85:
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

    if structure == "ranging" and not (
        sr.get("near_support") or sr.get("near_resistance") or
        sd.get("in_demand")   or sd.get("in_supply")
    ):
        return None, None, None, 0, {}

    # ── BUY score ─────────────────────────────────────────────────────────────
    buy_score   = 0
    buy_reasons = []
    buy_data    = {}

    if structure == "bullish":
        buy_score += 2; buy_reasons.append("Bullish market structure (HH+HL)")
        buy_data["structure"] = "bullish"
    if sr["near_support"]:
        buy_score += 1; buy_reasons.append(f"Price at key support {sr['support']}")
        buy_data["support"] = sr["support"]
    if sd["in_demand"]:
        buy_score += 2; buy_reasons.append("Price inside demand zone")
        buy_data["in_demand"] = True
    if cp["bullish_engulfing"] or cp["bullish_pin"]:
        buy_score += 1
        for p in cp["bullish_patterns"]: buy_reasons.append(p)
        buy_data["candle_pattern"] = cp["bullish_patterns"]
    if cp.get("inside_bar_bull"):
        buy_score += 1; buy_reasons.append("Inside Bar (bullish close)")
    if cp["double_bottom"]:
        buy_score += 1; buy_reasons.append("Double Bottom confirmed")
        buy_data["double_bottom"] = True
    if rsi < 45:
        buy_score += 1; buy_reasons.append(f"RSI {rsi:.1f} oversold")
        buy_data["rsi"] = rsi
    if ema_cross_up or ema_bull:
        label = "EMA9 crossed above EMA21" if ema_cross_up else "EMA9 above EMA21"
        buy_score += 1; buy_reasons.append(label)
        buy_data["ema"] = label
    if macd_bull:
        buy_score += 1; buy_reasons.append("MACD bullish crossover")
        buy_data["macd"] = "bullish"

    # ── SELL score ────────────────────────────────────────────────────────────
    sell_score   = 0
    sell_reasons = []
    sell_data    = {}

    if structure == "bearish":
        sell_score += 2; sell_reasons.append("Bearish market structure (LL+LH)")
        sell_data["structure"] = "bearish"
    if sr["near_resistance"]:
        sell_score += 1; sell_reasons.append(f"Price at key resistance {sr['resistance']}")
        sell_data["resistance"] = sr["resistance"]
    if sd["in_supply"]:
        sell_score += 2; sell_reasons.append("Price inside supply zone")
        sell_data["in_supply"] = True
    if cp["bearish_engulfing"] or cp["bearish_pin"]:
        sell_score += 1
        for p in cp["bearish_patterns"]: sell_reasons.append(p)
        sell_data["candle_pattern"] = cp["bearish_patterns"]
    if cp.get("inside_bar_bear"):
        sell_score += 1; sell_reasons.append("Inside Bar (bearish close)")
    if cp["double_top"]:
        sell_score += 1; sell_reasons.append("Double Top confirmed")
        sell_data["double_top"] = True
    if rsi > 55:
        sell_score += 1; sell_reasons.append(f"RSI {rsi:.1f} overbought")
        sell_data["rsi"] = rsi
    if ema_cross_down or ema_bear:
        label = "EMA9 crossed below EMA21" if ema_cross_down else "EMA9 below EMA21"
        sell_score += 1; sell_reasons.append(label)
        sell_data["ema"] = label
    if macd_bear:
        sell_score += 1; sell_reasons.append("MACD bearish crossover")
        sell_data["macd"] = "bearish"

    # ── H1 trend filter ───────────────────────────────────────────────────────
    h1_trend = d.get("h1_trend", "neutral")
    if h1_trend == "bearish" and buy_score > sell_score:
        print(f"[FILTER] H1 BEARISH -- BUY blocked (score {buy_score})", flush=True)
        buy_score = 0
    elif h1_trend == "bullish" and sell_score > buy_score:
        print(f"[FILTER] H1 BULLISH -- SELL blocked (score {sell_score})", flush=True)
        sell_score = 0

    MIN_SCORE = 5

    if buy_score >= sell_score and buy_score >= MIN_SCORE:
        confidence = "HIGH" if buy_score >= 8 else "MEDIUM"
        analysis   = {**buy_data, "sr": sr, "sd": sd, "score": buy_score}
        return "BUY", buy_reasons, confidence, buy_score, analysis

    if sell_score > buy_score and sell_score >= MIN_SCORE:
        confidence = "HIGH" if sell_score >= 8 else "MEDIUM"
        analysis   = {**sell_data, "sr": sr, "sd": sd, "score": sell_score}
        return "SELL", sell_reasons, confidence, sell_score, analysis

    return None, None, None, 0, {}

# ── Level calculator ──────────────────────────────────────────────────────────
def calculate_levels(signal_type: str, price: float, atr: float, sr: dict) -> dict:
    MAX_SL_PIPS = 50
    MIN_RR      = 2.0
    MIN_TP_GAP  = 0.5

    if signal_type == "BUY":
        entry    = price
        sl_raw   = max(round(sr.get("support", price - atr * 1.2) - atr * 0.3, 2),
                       round(price - atr * 1.2, 2))
        sl       = round(max(sl_raw, round(price - (MAX_SL_PIPS / 10), 2)), 2)
        risk     = round(price - sl, 2)
        tp1      = round(price + risk * 1.0, 2)
        tp2      = round(price + risk * 2.0, 2)
        tp3_base = round(price + risk * 3.0, 2)
        tp3      = round(sr.get("resistance", tp3_base) if sr.get("resistance", tp3_base) > tp2 else tp3_base, 2)
    else:
        entry    = price
        sl_raw   = min(round(sr.get("resistance", price + atr * 1.2) + atr * 0.3, 2),
                       round(price + atr * 1.2, 2))
        sl       = round(min(sl_raw, round(price + (MAX_SL_PIPS / 10), 2)), 2)
        risk     = round(sl - price, 2)
        tp1      = round(price - risk * 1.0, 2)
        tp2      = round(price - risk * 2.0, 2)
        tp3_base = round(price - risk * 3.0, 2)
        tp3      = round(sr.get("support", tp3_base) if sr.get("support", tp3_base) < tp2 else tp3_base, 2)

    actual_risk = round(abs(entry - sl), 2)

    if actual_risk > (MAX_SL_PIPS / 10):
        return {"blocked": True, "reason": f"SL {actual_risk*10:.1f} pips > {MAX_SL_PIPS} pip cap"}

    tp2_rr = round(abs(tp2 - entry) / actual_risk, 2) if actual_risk > 0 else 0
    if tp2_rr < MIN_RR:
        return {"blocked": True, "reason": f"R:R {tp2_rr} at TP2 below 1:{MIN_RR}"}

    if abs(tp2 - tp1) < MIN_TP_GAP or abs(tp3 - tp2) < MIN_TP_GAP:
        return {"blocked": True, "reason": "TP levels too close"}

    return {"blocked": False, "entry": entry, "sl": sl,
            "tp1": tp1, "tp2": tp2, "tp3": tp3, "risk": actual_risk}

# ── AI signal message generator ───────────────────────────────────────────────
def generate_signal_message(signal_type: str, d: dict, confidence: str,
                             session: str, reasons: list,
                             score: int, analysis: dict) -> str:
    price  = d["price"]
    atr    = d["atr"]
    sr     = analysis.get("sr", {})
    sd     = analysis.get("sd", {})
    levels = calculate_levels(signal_type, price, atr, sr)

    if levels["blocked"]:
        print(f"[BLOCK] {levels['reason']}", flush=True)
        return None

    entry = levels["entry"]
    sl    = levels["sl"]
    tp1   = levels["tp1"]
    tp2   = levels["tp2"]
    tp3   = levels["tp3"]

    confidence_emoji = "🔥" if confidence == "HIGH" else "⚡"
    direction_emoji  = "📈" if signal_type == "BUY" else "📉"

    if signal_type == "BUY" and sd.get("in_demand"):
        zone_note = "masuk dalam demand zone"
    elif signal_type == "SELL" and sd.get("in_supply"):
        zone_note = "masuk dalam supply zone"
    elif signal_type == "BUY" and sr.get("near_support"):
        zone_note = f"bounce dari support {sr['support']}"
    elif signal_type == "SELL" and sr.get("near_resistance"):
        zone_note = f"reject dari resistance {sr['resistance']}"
    else:
        zone_note = "confluence kuat"

    prompt = f"""Kau trader Malaysia yang casual. Tulis signal Telegram dalam Manglish/BM pasar.

TEMPLATE WAJIB -- JANGAN UBAH NOMBOR LANGSUNG:

{confidence_emoji} {signal_type} XAUUSD {direction_emoji}

▸ Zone  : {entry}
▸ SL    : {sl}
▸ TP1   : {tp1}
▸ TP2   : {tp2}
▸ TP3   : {tp3}

[TULIS 1 AYAT SAHAJA -- casual, hype, pasal setup ni. Sebut {zone_note} dan RSI {d['rsi']:.0f}. Contoh style: "Setup power gila, {zone_note} confirm! RSI pun sokong 🔥🚀"]

Jom tekan okay! 👌
⚠️ Bukan nasihat kewangan.
🔔 MTU Premium Signal Gold

Output MESEJ SAHAJA. Tiada teks lain."""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      "claude-sonnet-4-20250514",
            "max_tokens": 400,
            "messages":   [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"].strip()

# ── Telegram sender ────────────────────────────────────────────────────────────
def send_to_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r   = requests.post(url, json={
        "chat_id":    TELEGRAM_CHANNEL_ID,
        "text":       message,
        "parse_mode": "HTML",
    }, timeout=15)
    r.raise_for_status()
    print(f"[TG] Sent ✅ status={r.status_code}", flush=True)

# ── Update open signals with current price ────────────────────────────────────
def update_open_signals(current_price: float):
    signals = load_open_signals()
    changed = False

    for sig in signals:
        if sig.get("status") in ("closed", "sl_hit"):
            continue

        direction = sig.get("type", "BUY")
        sl  = sig.get("sl")
        tp3 = sig.get("tp3")
        tp2 = sig.get("tp2")
        tp1 = sig.get("tp1")

        prev_status = sig.get("status", "open")

        if direction == "BUY":
            if current_price <= sl:
                sig["status"] = "sl_hit"; sig["sl_hit"] = True; sig["result"] = "SL"
            elif current_price >= tp3:
                sig["status"] = "closed"; sig["tp3_hit"] = True; sig["tp2_hit"] = True; sig["tp1_hit"] = True
            elif current_price >= tp2:
                sig["tp2_hit"] = True; sig["tp1_hit"] = True
            elif current_price >= tp1:
                sig["tp1_hit"] = True
        else:
            if current_price >= sl:
                sig["status"] = "sl_hit"; sig["sl_hit"] = True; sig["result"] = "SL"
            elif current_price <= tp3:
                sig["status"] = "closed"; sig["tp3_hit"] = True; sig["tp2_hit"] = True; sig["tp1_hit"] = True
            elif current_price <= tp2:
                sig["tp2_hit"] = True; sig["tp1_hit"] = True
            elif current_price <= tp1:
                sig["tp1_hit"] = True

        if sig.get("status") != prev_status:
            changed = True
            print(f"[UPDATE] Signal {sig.get('id','?')} → {sig.get('status')}", flush=True)

    if changed:
        save_open_signals(signals)

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

    structure_label = {
        "bullish": "Bullish (Menaik) ⬆️",
        "bearish": "Bearish (Menurun) ⬇️",
        "ranging": "Ranging (Mendatar) ↔️"
    }.get(structure, "Ranging ↔️")

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
- Structure: {structure_label}
- RSI(14): {d['rsi']:.1f}
- EMA9: {d['ema9']:.2f} | EMA21: {d['ema21']:.2f}
- ATR(14): {d['atr']:.2f}

🗺 Key Levels Today:
- Resistance: {sr['resistance']}
- Support: {sr['support']}
- Supply Zone: {supply_str}
- Demand Zone: {demand_str}

🧭 Bias: {structure_label}
━━━━━━━━━━━━━━━━━━━━━
📝 Today's Outlook:
[Write exactly 3 sharp sentences in English about current structure, key levels, and actionable bias.]

🕐 Sessions Today (MYT):
🌏 Asia: 08:00 - 16:00
🇬🇧 London: 15:00 - 00:00
🇺🇸 New York: 21:00 - 05:00

⚠️ Not financial advice. Trade responsibly.
🔔 MTU Premium | XAUUSD Signals

Output ONLY the message."""

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key":         ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type":      "application/json",
        },
        json={
            "model":      "claude-sonnet-4-20250514",
            "max_tokens": 700,
            "messages":   [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"].strip()

# ── Morning update tracker (Railway persistent) ───────────────────────────────
_MORNING_UPDATE_FILE = str(_DATA_DIR / "morning_update.json")

def morning_already_sent() -> bool:
    try:
        with open(_MORNING_UPDATE_FILE) as f:
            data = json.load(f)
        return data.get("date") == str(date.today())
    except:
        return False

def mark_morning_sent():
    with open(_MORNING_UPDATE_FILE, "w") as f:
        json.dump({"date": str(date.today())}, f)

def maybe_send_morning_update(data: dict):
    """Send morning update once per day at 00:00 UTC (08:00 MYT)."""
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour == 0 and not morning_already_sent():
        print("[MORNING] Sending daily outlook...", flush=True)
        try:
            message = generate_morning_update(data)
            send_to_telegram(message)
            mark_morning_sent()
            print("[MORNING] Sent ✅", flush=True)
        except Exception as e:
            print(f"[MORNING] Failed: {e}", flush=True)

# ── Main signal logic (single run) ───────────────────────────────────────────
def main():
    now_utc     = datetime.now(timezone.utc)
    utc_hour    = now_utc.hour
    utc_weekday = now_utc.weekday()
    session     = get_current_session(utc_hour, utc_weekday)

    print(f"\n[{now_utc.strftime('%Y-%m-%d %H:%M')} UTC] Session: {session}", flush=True)

    if not is_active_hours(utc_hour, utc_weekday):
        reason = "Weekend" if utc_weekday in (5, 6) else "Off-hours"
        print(f"[SKIP] {reason} -- bot resting.", flush=True)
        return

    state = load_state()

    if state["count"] >= MAX_SIGNALS_PER_DAY:
        print(f"[SKIP] Daily limit reached ({MAX_SIGNALS_PER_DAY}).", flush=True)
        return

    if not cooldown_ok(state):
        last = state.get("last_signal_utc", "")
        diff = round((datetime.now(timezone.utc) -
                      datetime.fromisoformat(last)).total_seconds() / 60, 1)
        print(f"[SKIP] Cooldown active ({diff}/{COOLDOWN_MINUTES} min).", flush=True)
        return

    # Consecutive SL protection
    recent_signals = load_open_signals()
    recent_closed  = [s for s in recent_signals
                      if s.get("status") in ("closed", "sl_hit")][-5:]
    consecutive_sl = 0
    for sig in reversed(recent_closed):
        if sig.get("result") == "SL":
            consecutive_sl += 1
        else:
            break
    if consecutive_sl >= 3:
        last_time = recent_closed[-1].get("opened_utc") or recent_closed[-1].get("sent_at", "")
        if last_time:
            try:
                elapsed = (datetime.now(timezone.utc) -
                           datetime.fromisoformat(last_time)).total_seconds() / 3600
                if elapsed < 2.0:
                    print(f"[SKIP] 3 consecutive SL -- cooling down ({elapsed:.1f}h/2h).", flush=True)
                    return
            except:
                pass

    # Fetch data
    try:
        data = fetch_market_data()
    except Exception as e:
        print(f"[ERROR] Market data: {e}", flush=True)
        return

    # Morning update check
    maybe_send_morning_update(data)

    # Update existing open signals
    update_open_signals(data["price"])

    # Check conditions
    signal_type, reasons, confidence, score, analysis = check_conditions(data)

    if not signal_type:
        print(f"[NO SIGNAL] Score too low or no confluence.", flush=True)
        return

    print(f"[SIGNAL] {signal_type} | Score: {score} | Confidence: {confidence}", flush=True)
    print(f"[REASONS] {' | '.join(reasons)}", flush=True)

    # Generate message
    try:
        message = generate_signal_message(
            signal_type, data, confidence, session, reasons, score, analysis
        )
    except Exception as e:
        print(f"[ERROR] AI generation: {e}", flush=True)
        return

    if not message:
        print("[BLOCK] Signal blocked by level validator.", flush=True)
        return

    print("-" * 60, flush=True)
    print(message, flush=True)
    print("-" * 60, flush=True)

    # Send to Telegram
    try:
        send_to_telegram(message)
    except Exception as e:
        print(f"[ERROR] Telegram: {e}", flush=True)
        return

    # Save signal
    sr     = analysis.get("sr", {})
    levels = calculate_levels(signal_type, data["price"], data["atr"], sr)
    if not levels.get("blocked"):
        now_iso  = datetime.now(timezone.utc).isoformat()
        sig_id   = now_utc.strftime("%Y%m%d%H%M")
        new_sig  = {
            "id":                 sig_id,
            "type":               signal_type,
            "entry":              levels["entry"],
            "sl":                 levels["sl"],
            "tp1":                levels["tp1"],
            "tp2":                levels["tp2"],
            "tp3":                levels["tp3"],
            "confidence":         confidence,
            "session":            session,
            "status":             "open",
            "tp1_hit":            False,
            "tp2_hit":            False,
            "tp3_hit":            False,
            "sl_hit":             False,
            "last_notified_profit": 0.0,
            "opened_utc":         now_iso,
            "result":             "open",
        }
        open_signals = load_open_signals()
        open_signals.append(new_sig)
        save_open_signals(open_signals)
        print(f"[SAVED] Signal {sig_id} saved.", flush=True)

    # Update state
    state["count"]          += 1
    state["last_signal_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print(f"[STATE] Signals today: {state['count']}/{MAX_SIGNALS_PER_DAY}", flush=True)

# ── Entry point -- Railway persistent loop ────────────────────────────────────
if __name__ == "__main__":
    # Support CLI args for manual triggers
    if len(sys.argv) > 1:
        if sys.argv[1] == "morning":
            print("[CLI] Manual morning update...", flush=True)
            try:
                data = fetch_market_data()
                msg  = generate_morning_update(data)
                send_to_telegram(msg)
                print("[CLI] Morning update sent.", flush=True)
            except Exception as e:
                print(f"[CLI] Error: {e}", flush=True)
            sys.exit(0)

        elif sys.argv[1] == "weekly":
            print("[CLI] Weekly report -- not yet implemented.", flush=True)
            sys.exit(0)

    # ── Persistent loop (Railway) ─────────────────────────────────────────────
    print("[BOT] MTU Premium Signal Bot starting... 🚀", flush=True)
    print(f"[BOT] Max signals/day: {MAX_SIGNALS_PER_DAY} | Cooldown: {COOLDOWN_MINUTES}min", flush=True)

    while True:
        try:
            main()
        except Exception as e:
            print(f"[CRASH] main() error: {e}", flush=True)

        now_utc    = datetime.now(timezone.utc)
        sleep_secs = get_fetch_interval(now_utc.hour)
        next_check = (datetime.now(timezone.utc) + timedelta(seconds=sleep_secs)).strftime("%H:%M UTC")
        print(f"[SLEEP] {sleep_secs}s -- next check ~{next_check}", flush=True)
        time.sleep(sleep_secs)
