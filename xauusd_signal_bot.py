# -*- coding: utf-8 -*-
# XAUUSD AI Scalping Signal Bot -- MTU Premium
# Strategy: S&R, S&D, Engulfing, Market Structure, RSI, EMA, MACD
# Timeframe: 15-min | Sessions: Asia, London, New York

import os
import json
import sys
import time
import random
import requests
from datetime import datetime, date, timezone

# ── Secrets ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL_ID = os.environ["TELEGRAM_CHANNEL_ID"]
ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
TWELVEDATA_API_KEY  = os.environ["TWELVEDATA_API_KEY"]
NEWSAPI_KEY         = os.environ.get("NEWSAPI_KEY", "")
GITHUB_TOKEN        = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO         = os.environ.get("GITHUB_REPO", "Isyrafimran25/MTUPremiumSignal")
FINNHUB_API_KEY     = os.environ.get("FINNHUB_API_KEY", "")

# ── Config ────────────────────────────────────────────────────────────────────
MAX_SIGNALS_PER_DAY = 10
COOLDOWN_MINUTES    = 90
SYMBOL              = "XAU/USD"
INTERVAL            = "15min"

# ── Persistent storage paths ──────────────────────────────────────────────────
import pathlib as _pathlib
_DATA_DIR        = _pathlib.Path("/data") if _pathlib.Path("/data").exists() else _pathlib.Path(".")
SIGNAL_COUNT_FILE = str(_DATA_DIR / "signal_count.json")
OPEN_SIGNALS_FILE = str(_DATA_DIR / "open_signals.json")
print(f"Storage directory: {_DATA_DIR} ({'persistent' if str(_DATA_DIR) == '/data' else 'non-persistent -- using GitHub as source of truth'})")

# ── Session helpers ───────────────────────────────────────────────────────────
SESSIONS = {
    "Asia":     (0,  8),
    "London":   (7,  16),
    "New York": (13, 21),
}

# ── Active trading hours ──────────────────────────────────────────────────────
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

# ── GitHub persistent storage ─────────────────────────────────────────────────
def github_get_file(filename: str) -> tuple:
    if not GITHUB_TOKEN:
        return None, None
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
        r = requests.get(url, headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }, timeout=10)
        if r.status_code == 200:
            data = r.json()
            import base64
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data["sha"]
        return None, None
    except Exception as e:
        print(f"GitHub get failed: {e}")
        return None, None

def github_push_file(filename: str, content: str, msg: str = "update signal data"):
    if not GITHUB_TOKEN:
        return False
    try:
        import base64
        _, sha = github_get_file(filename)
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
        payload = {
            "message": msg,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        }
        if sha:
            payload["sha"] = sha
        r = requests.put(url, headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }, json=payload, timeout=10)
        if r.status_code in (200, 201):
            print(f"GitHub: {filename} saved ok")
            return True
        else:
            print(f"GitHub push failed: {r.status_code} {r.text[:100]}")
            return False
    except Exception as e:
        print(f"GitHub push error: {e}")
        return False

# ══════════════════════════════════════════════════════════════════════════════
# STATE MANAGEMENT -- GitHub-first (single source of truth)
# Local file just cache, GitHub is the authoritative store.
# ══════════════════════════════════════════════════════════════════════════════
def load_state() -> dict:
    """ALWAYS load from GitHub first (source of truth). Local file is fallback only."""
    if GITHUB_TOKEN:
        content_gh, _ = github_get_file("signal_count.json")
        if content_gh:
            try:
                data = json.loads(content_gh)
                if data.get("date") == str(date.today()):
                    print(f"📥 State loaded from GitHub: count={data.get('count', 0)}, last={data.get('last_signal_utc', 'none')}")
                    # Sync to local cache
                    with open(SIGNAL_COUNT_FILE, "w") as f:
                        json.dump(data, f)
                    return data
                else:
                    print(f"📅 GitHub state stale (date {data.get('date')}), resetting for today")
            except Exception as e:
                print(f"GitHub state parse failed: {e}")

    # Fallback to local file
    try:
        with open(SIGNAL_COUNT_FILE) as f:
            data = json.load(f)
        if data.get("date") == str(date.today()):
            print(f"📁 State loaded from local: count={data.get('count', 0)}")
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    print("🆕 Fresh state for today")
    return {
        "date": str(date.today()),
        "count": 0,
        "last_signal_utc": None,
        "last_signal_hash": None,  # Dedup key
    }

def save_state(state: dict):
    """Push to GitHub FIRST, then save locally. GitHub success is what matters."""
    success = github_push_file("signal_count.json", json.dumps(state), "update signal state")
    if not success:
        print("⚠️ WARNING: GitHub state push failed -- next run may not see this update!")
    with open(SIGNAL_COUNT_FILE, "w") as f:
        json.dump(state, f)

def load_open_signals_github() -> list:
    content, _ = github_get_file("open_signals.json")
    if content:
        try:
            return json.loads(content)
        except:
            pass
    return []

def save_open_signals_github(signals: list):
    with open(OPEN_SIGNALS_FILE, "w") as f:
        json.dump(signals, f, indent=2)
    github_push_file("open_signals.json", json.dumps(signals, indent=2), "update open signals")

def cooldown_ok(state: dict) -> bool:
    last = state.get("last_signal_utc")
    if not last:
        return True
    try:
        diff = (datetime.now(timezone.utc) -
                datetime.fromisoformat(last)).total_seconds() / 60
        return diff >= COOLDOWN_MINUTES
    except Exception as e:
        print(f"⚠️ Cooldown parse error: {e} -- defaulting to ALLOW (fail-open)")
        return True

def signal_hash(signal_type: str, entry: float, sl: float) -> str:
    """Create a fingerprint for a signal to detect duplicates."""
    return f"{signal_type}_{round(entry, 1)}_{round(sl, 1)}"

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
        if diff > atr_h1 * 0.5:
            return "bullish"
        elif diff < -atr_h1 * 0.5:
            return "bearish"
        return "neutral"
    except Exception as e:
        print(f"H1 trend fetch failed: {e}")
        return "neutral"

def fetch_market_data() -> dict:
    print(" -> time_series + H1 trend (2 API calls)")
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

    ema9_asc          = compute_ema(closes_asc, 9)
    ema21_asc         = compute_ema(closes_asc, 21)
    rsi_asc           = compute_rsi(closes_asc, 14)
    atr_asc           = compute_atr(candles_asc, 14)
    macd_asc, macd_sig_asc = compute_macd(closes_asc)

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

    latest   = candles[0]
    h1_trend = get_h1_trend()
    print(f"H1 Trend: {h1_trend.upper()}")

    entry_price = latest["close"]
    print(f"📡 Entry price: {entry_price} (candle close)")

    return {
        "candles":       candles,
        "price":         entry_price,
        "candle_close":  latest["close"],
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
        "support":          nearest_sup,
        "resistance":       nearest_res,
        "near_support":     abs(price - nearest_sup) < atr * 0.5,
        "near_resistance":  abs(price - nearest_res) < atr * 0.5,
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

    body0        = abs(c0["close"] - c0["open"])
    body1        = abs(c1["close"] - c1["open"])
    range0       = c0["high"] - c0["low"]
    upper_wick0  = c0["high"] - max(c0["open"], c0["close"])
    lower_wick0  = min(c0["open"], c0["close"]) - c0["low"]
    is_bull0     = c0["close"] > c0["open"]
    is_bull1     = c1["close"] > c1["open"]
    is_bear0     = c0["close"] < c0["open"]
    is_bear1     = c1["close"] < c1["open"]

    bullish_engulfing = (
        is_bear1 and is_bull0 and
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

    print(f"\n{'='*60}")
    print(f"🔍 SIGNAL ANALYSIS")
    print(f"{'='*60}")
    print(f"Price: {price} | RSI: {rsi} | ATR: {atr} | Avg ATR: {avg_atr}")
    print(f"EMA9: {d['ema9']} | EMA21: {d['ema21']}")
    print(f"H1 Trend: {d.get('h1_trend', 'neutral').upper()}")

    if atr < avg_atr * 0.85:
        print(f"❌ REJECTED: Low volatility (ATR {atr} < {avg_atr * 0.85:.2f})")
        return None, None, None, 0, {}

    structure = detect_market_structure(candles)
    sr        = find_sr_levels(candles, price, atr)
    sd        = find_sd_zones(candles, price, atr)
    cp        = detect_candle_patterns(candles, atr)

    print(f"Structure: {structure.upper()}")
    print(f"Support: {sr['support']} (near: {sr['near_support']}) | Resistance: {sr['resistance']} (near: {sr['near_resistance']})")
    print(f"In Demand: {sd['in_demand']} | In Supply: {sd['in_supply']}")
    print(f"Candle Patterns: {cp['patterns']}")

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
        print(f"❌ REJECTED: Ranging market, no key level nearby")
        return None, None, None, 0, {}

    buy_score   = 0
    buy_reasons = []
    buy_data    = {}

    if structure == "bullish":
        buy_score += 2; buy_reasons.append("Bullish market structure (HH+HL)"); buy_data["structure"] = "bullish"
    if sr["near_support"]:
        buy_score += 1; buy_reasons.append(f"Price at key support {sr['support']}"); buy_data["support"] = sr["support"]
    if sd["in_demand"]:
        buy_score += 2; buy_reasons.append("Price inside demand zone"); buy_data["in_demand"] = True
    if cp["bullish_engulfing"] or cp["bullish_pin"]:
        buy_score += 1
        for p in cp["bullish_patterns"]: buy_reasons.append(p)
        buy_data["candle_pattern"] = cp["bullish_patterns"]
    if cp.get("inside_bar_bull"):
        buy_score += 1; buy_reasons.append("Inside Bar (bullish close)")
    if cp["double_bottom"]:
        buy_score += 1; buy_reasons.append("Double Bottom"); buy_data["double_bottom"] = True
    if rsi < 45:
        buy_score += 1; buy_reasons.append(f"RSI {rsi:.1f} oversold"); buy_data["rsi"] = rsi
    if ema_cross_up or ema_bull:
        buy_score += 1
        label = "EMA9 crossed above EMA21" if ema_cross_up else "EMA9 above EMA21"
        buy_reasons.append(label); buy_data["ema"] = label
    if macd_bull:
        buy_score += 1; buy_reasons.append("MACD bullish crossover"); buy_data["macd"] = "bullish"

    sell_score   = 0
    sell_reasons = []
    sell_data    = {}

    if structure == "bearish":
        sell_score += 2; sell_reasons.append("Bearish market structure (LL+LH)"); sell_data["structure"] = "bearish"
    if sr["near_resistance"]:
        sell_score += 1; sell_reasons.append(f"Price at key resistance {sr['resistance']}"); sell_data["resistance"] = sr["resistance"]
    if sd["in_supply"]:
        sell_score += 2; sell_reasons.append("Price inside supply zone"); sell_data["in_supply"] = True
    if cp["bearish_engulfing"] or cp["bearish_pin"]:
        sell_score += 1
        for p in cp["bearish_patterns"]: sell_reasons.append(p)
        sell_data["candle_pattern"] = cp["bearish_patterns"]
    if cp.get("inside_bar_bear"):
        sell_score += 1; sell_reasons.append("Inside Bar (bearish close)")
    if cp["double_top"]:
        sell_score += 1; sell_reasons.append("Double Top"); sell_data["double_top"] = True
    if rsi > 55:
        sell_score += 1; sell_reasons.append(f"RSI {rsi:.1f} overbought"); sell_data["rsi"] = rsi
    if ema_cross_down or ema_bear:
        sell_score += 1
        label = "EMA9 crossed below EMA21" if ema_cross_down else "EMA9 below EMA21"
        sell_reasons.append(label); sell_data["ema"] = label
    if macd_bear:
        sell_score += 1; sell_reasons.append("MACD bearish crossover"); sell_data["macd"] = "bearish"

    print(f"📊 BUY={buy_score} | SELL={sell_score}")

    # H1 trend SMART filter
    h1_trend = d.get("h1_trend", "neutral")
    if h1_trend == "bearish" and buy_score > sell_score:
        in_strong_zone = sd.get("in_demand") or sr.get("near_support")
        if buy_score >= 6 and in_strong_zone:
            print(f"⚠️ H1 BEARISH but counter-trend BUY allowed (score {buy_score} + strong zone)")
        elif buy_score >= 8:
            print(f"⚠️ H1 BEARISH but counter-trend BUY allowed (score {buy_score})")
        else:
            print(f"⛔ H1 BEARISH -- BUY needs ≥6+zone OR ≥8. Got {buy_score}.")
            buy_score = 0
    elif h1_trend == "bullish" and sell_score > buy_score:
        in_strong_zone = sd.get("in_supply") or sr.get("near_resistance")
        if sell_score >= 6 and in_strong_zone:
            print(f"⚠️ H1 BULLISH but counter-trend SELL allowed (score {sell_score} + strong zone)")
        elif sell_score >= 8:
            print(f"⚠️ H1 BULLISH but counter-trend SELL allowed (score {sell_score})")
        else:
            print(f"⛔ H1 BULLISH -- SELL needs ≥6+zone OR ≥8. Got {sell_score}.")
            sell_score = 0

    MIN_SCORE = 5

    if buy_score >= sell_score and buy_score >= MIN_SCORE:
        confidence = "HIGH" if buy_score >= 8 else "MEDIUM"
        analysis   = {**buy_data, "sr": sr, "sd": sd, "score": buy_score}
        print(f"✅ BUY TRIGGERED (score {buy_score}, {confidence})")
        return "BUY", buy_reasons, confidence, buy_score, analysis

    if sell_score > buy_score and sell_score >= MIN_SCORE:
        confidence = "HIGH" if sell_score >= 8 else "MEDIUM"
        analysis   = {**sell_data, "sr": sr, "sd": sd, "score": sell_score}
        print(f"✅ SELL TRIGGERED (score {sell_score}, {confidence})")
        return "SELL", sell_reasons, confidence, sell_score, analysis

    print(f"❌ REJECTED: Max score ({max(buy_score, sell_score)}) < MIN_SCORE ({MIN_SCORE})")
    return None, None, None, 0, {}

# ── Level calculator ──────────────────────────────────────────────────────────
def calculate_levels(signal_type: str, price: float, atr: float, sr: dict) -> dict:
    MAX_SL_PIPS = 50
    MIN_RR      = 2.0
    MIN_TP_GAP  = 0.5

    if signal_type == "BUY":
        entry      = price
        sl_sr      = round(sr.get("support",    price - atr * 1.2) - atr * 0.3, 2)
        sl_atr     = round(price - atr * 1.2, 2)
        sl_raw     = max(sl_sr, sl_atr)
        sl_cap     = round(price - (MAX_SL_PIPS / 10), 2)
        sl         = round(max(sl_raw, sl_cap), 2)
        risk       = round(price - sl, 2)
        tp1        = round(price + risk * 1.0, 2)
        tp2        = round(price + risk * 2.0, 2)
        tp3_base   = round(price + risk * 3.0, 2)
        tp3_sr     = sr.get("resistance", tp3_base)
        tp3        = round(tp3_sr if tp3_sr > tp2 else tp3_base, 2)
    else:
        entry      = price
        sl_sr      = round(sr.get("resistance", price + atr * 1.2) + atr * 0.3, 2)
        sl_atr     = round(price + atr * 1.2, 2)
        sl_raw     = min(sl_sr, sl_atr)
        sl_cap     = round(price + (MAX_SL_PIPS / 10), 2)
        sl         = round(min(sl_raw, sl_cap), 2)
        risk       = round(sl - price, 2)
        tp1        = round(price - risk * 1.0, 2)
        tp2        = round(price - risk * 2.0, 2)
        tp3_base   = round(price - risk * 3.0, 2)
        tp3_sr     = sr.get("support", tp3_base)
        tp3        = round(tp3_sr if tp3_sr < tp2 else tp3_base, 2)

    actual_risk = round(abs(entry - sl), 2)

    if actual_risk > (MAX_SL_PIPS / 10):
        return {"blocked": True, "reason": f"SL {actual_risk*10:.1f} pips exceeds {MAX_SL_PIPS} pip cap"}

    tp2_rr = round(abs(tp2 - entry) / actual_risk, 2) if actual_risk > 0 else 0
    if tp2_rr < MIN_RR:
        return {"blocked": True, "reason": f"R:R {tp2_rr} at TP2 below 1:{MIN_RR}"}

    if abs(tp2 - tp1) < MIN_TP_GAP or abs(tp3 - tp2) < MIN_TP_GAP:
        return {"blocked": True, "reason": "TP levels too close -- ATR too small"}

    return {
        "blocked": False, "entry": entry, "sl": sl,
        "tp1": tp1, "tp2": tp2, "tp3": tp3, "risk": actual_risk
    }

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
        print(f"Signal blocked -- {levels['reason']}")
        return None, None

    entry = levels["entry"]
    sl    = levels["sl"]
    tp1   = levels["tp1"]
    tp2   = levels["tp2"]
    tp3   = levels["tp3"]

    confidence_emoji = "🔥" if confidence == "HIGH" else "⚡"
    direction_emoji  = "📈" if signal_type == "BUY" else "📉"

    zone_note = ""
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

    rsi_note = f"RSI {d['rsi']:.0f}"

    prompt = f"""Kau trader Malaysia yang casual. Tulis signal Telegram dalam Manglish/BM pasar.

TEMPLATE WAJIB -- JANGAN UBAH NOMBOR LANGSUNG:

{confidence_emoji} {signal_type} XAUUSD {direction_emoji}

▸ Zone  : {entry}
▸ SL    : {sl}
▸ TP1   : {tp1}
▸ TP2   : {tp2}
▸ TP3   : {tp3}

[TULIS 1 AYAT SAHAJA -- casual, hype, pasal setup ni. Sebut {zone_note} dan {rsi_note}. Contoh: "Setup power gila, {zone_note} confirm! {rsi_note} pun sokong 🔥🚀"]

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
    msg = response.json()["content"][0]["text"].strip()
    return msg, levels

# ── Telegram sender ────────────────────────────────────────────────────────────
def send_to_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r   = requests.post(url, json={
        "chat_id":    TELEGRAM_CHANNEL_ID,
        "text":       message,
        "parse_mode": "HTML",
    }, timeout=15)
    print(f"📡 Telegram status: {r.status_code}")
    r.raise_for_status()

# ── Open signals management ───────────────────────────────────────────────────
def load_open_signals() -> list:
    try:
        with open(OPEN_SIGNALS_FILE) as f:
            signals = json.load(f)
        if signals:
            return signals
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return load_open_signals_github()

def save_open_signals(signals: list):
    save_open_signals_github(signals)

def update_open_signals(current_price: float) -> list:
    signals = load_open_signals()
    updated = []
    for sig in signals:
        direction = sig.get("type", "BUY")
        sl        = sig.get("sl")
        tp3       = sig.get("tp3")
        tp2       = sig.get("tp2")
        tp1       = sig.get("tp1")
        result    = sig.get("result", "open")

        if result != "open":
            updated.append(sig)
            continue

        if direction == "BUY":
            if current_price <= sl:    sig["result"] = "SL"
            elif current_price >= tp3: sig["result"] = "TP3"
            elif current_price >= tp2: sig["result"] = "TP2"
            elif current_price >= tp1: sig["result"] = "TP1"
        else:
            if current_price >= sl:    sig["result"] = "SL"
            elif current_price <= tp3: sig["result"] = "TP3"
            elif current_price <= tp2: sig["result"] = "TP2"
            elif current_price <= tp1: sig["result"] = "TP1"

        updated.append(sig)

    save_open_signals(updated)
    return updated

# ── Daily morning update ───────────────────────────────────────────────────────
def generate_morning_update(d: dict) -> str:
    price      = d["price"]
    prev_close = d["prev_close"]
    change     = round(price - prev_close, 2)
    change_pct = round((change / prev_close) * 100, 2)
    direction  = "🟢" if change >= 0 else "🔴"
    sign       = "+" if change >= 0 else ""
    date_str   = datetime.now(timezone.utc).strftime("%A, %d %B %Y")

    candles         = d["candles"]
    structure       = detect_market_structure(candles)
    sr              = find_sr_levels(candles, price, d["atr"])
    sd              = find_sd_zones(candles, price, d["atr"])
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
[Write exactly 3 sharp sentences in English:
1. Comment on current market structure and momentum.
2. Highlight the most important S&R and S&D levels to watch today.
3. Give a clear actionable bias -- buy dips, sell rallies, or wait for breakout.
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
            "model":      "claude-sonnet-4-20250514",
            "max_tokens": 700,
            "messages":   [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["content"][0]["text"].strip()


def morning_update():
    now_utc = datetime.now(timezone.utc)
    print(f"[{now_utc.strftime('%Y-%m-%d %H:%M')} UTC] Morning update running...")

    if now_utc.weekday() in (5, 6):
        print("Weekend -- skip morning update.")
        return

    try:
        data = fetch_market_data()
        print(f"✅ Price: {data['price']} | RSI: {data['rsi']} | ATR: {data['atr']}")
    except Exception as e:
        print(f"❌ Data fetch failed: {e}")
        return

    try:
        message = generate_morning_update(data)
        print(f"✅ Message generated ({len(message)} chars)")
    except Exception as e:
        print(f"❌ AI generation failed: {e}")
        return

    print("-" * 50); print(message); print("-" * 50)

    try:
        send_to_telegram(message)
        print("✅ Morning update sent!")
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN SIGNAL LOOP
# ══════════════════════════════════════════════════════════════════════════════
def main():
    now_utc     = datetime.now(timezone.utc)
    utc_hour    = now_utc.hour
    utc_weekday = now_utc.weekday()
    session     = get_current_session(utc_hour)

    print(f"\n[{now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC] Bot running...")
    print(f"Session: {session}")

    if not is_active_hours(utc_hour, utc_weekday):
        if utc_weekday in (5, 6):
            print("Weekend -- bot resting.")
        else:
            print("Off-hours (2AM-7AM MYT). Bot resting.")
        return

    # ── STEP 1: Load state (GitHub-first) ─────────────────────────────────────
    state = load_state()

    if state["count"] >= MAX_SIGNALS_PER_DAY:
        print(f"⏹️ Daily limit reached ({MAX_SIGNALS_PER_DAY}).")
        return

    # ── STEP 2: Cooldown check (HARD GATE) ────────────────────────────────────
    if not cooldown_ok(state):
        last = state.get("last_signal_utc", "")
        try:
            diff = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 60
            print(f"⏳ Cooldown active -- {diff:.1f}/{COOLDOWN_MINUTES} min elapsed. SKIP.")
        except:
            print(f"⏳ Cooldown active -- SKIP.")
        return

    # ── STEP 3: Consecutive SL protection ─────────────────────────────────────
    recent_signals = load_open_signals()
    recent_closed  = [s for s in recent_signals if s.get("result") != "open"][-5:]
    consecutive_sl = 0
    for sig in reversed(recent_closed):
        if sig.get("result") == "SL":
            consecutive_sl += 1
        else:
            break
    if consecutive_sl >= 3:
        last_sl_time = recent_closed[-1].get("sent_at", "")
        if last_sl_time:
            try:
                elapsed = (datetime.now(timezone.utc) -
                           datetime.fromisoformat(last_sl_time)).total_seconds() / 3600
                if elapsed < 2.0:
                    print(f"⛔ 3 consecutive SL -- pausing 2h (elapsed: {elapsed:.1f}h)")
                    return
            except:
                pass

    # ── STEP 4: Fetch + analyze ───────────────────────────────────────────────
    try:
        data = fetch_market_data()
    except Exception as e:
        print(f"❌ Market data fetch failed: {e}")
        return

    update_open_signals(data["price"])

    signal_type, reasons, confidence, score, analysis = check_conditions(data)

    if not signal_type:
        print("No signal -- conditions not met.")
        return

    print(f"🎯 Signal: {signal_type} | Score: {score} | {confidence}")

    # ── STEP 5: Generate message + levels ─────────────────────────────────────
    try:
        message, levels = generate_signal_message(
            signal_type, data, confidence, session, reasons, score, analysis
        )
    except Exception as e:
        print(f"❌ Message generation failed: {e}")
        return

    if not message or not levels:
        print("Signal blocked by level validator.")
        return

    # ── STEP 6: DEDUP CHECK -- prevent duplicate signals ──────────────────────
    new_hash = signal_hash(signal_type, levels["entry"], levels["sl"])
    last_hash = state.get("last_signal_hash")
    if last_hash == new_hash:
        print(f"⛔ DUPLICATE signal detected (hash {new_hash} matches last). SKIP.")
        return
    print(f"🔑 Signal hash: {new_hash} (prev: {last_hash})")

    # ── STEP 7: ATOMIC STATE UPDATE -- write BEFORE telegram ──────────────────
    # Critical: update state FIRST so concurrent runs see the cooldown.
    # If telegram fails, we'll skip but cooldown protects against duplicate retries.
    state["count"]           += 1
    state["last_signal_utc"]  = datetime.now(timezone.utc).isoformat()
    state["last_signal_hash"] = new_hash
    save_state(state)
    print(f"💾 State saved BEFORE telegram (count {state['count']}/{MAX_SIGNALS_PER_DAY})")

    # ── STEP 8: Print + send ──────────────────────────────────────────────────
    print("-" * 50); print(message); print("-" * 50)

    try:
        send_to_telegram(message)
        print("✅ Signal sent to Telegram!")
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")
        return

    # ── STEP 9: Save signal to open_signals.json ──────────────────────────────
    new_signal = {
        "type":     signal_type,
        "entry":    levels["entry"],
        "sl":       levels["sl"],
        "tp1":      levels["tp1"],
        "tp2":      levels["tp2"],
        "tp3":      levels["tp3"],
        "result":   "open",
        "score":    score,
        "sent_at":  datetime.now(timezone.utc).isoformat(),
        "session":  session,
    }
    open_signals = load_open_signals()
    open_signals.append(new_signal)
    save_open_signals(open_signals)
    print(f"📝 Signal logged. Count today: {state['count']}/{MAX_SIGNALS_PER_DAY}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":

    if len(sys.argv) > 1 and sys.argv[1] == "morning":
        morning_update()
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "weekly":
        sys.exit(0)

    # GitHub Actions mode: run once and exit
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("🤖 GitHub Actions mode -- single run")
        try:
            main()
        except Exception as e:
            print(f"main() crashed: {e}")
            import traceback
            traceback.print_exc()
        sys.exit(0)

    # Railway / local mode: infinite loop
    now_utc = datetime.now(timezone.utc)
    if now_utc.hour == 0 and now_utc.minute < 5:
        morning_update()

    while True:
        try:
            main()
        except Exception as e:
            print(f"main() crashed: {e}")

        now_utc    = datetime.now(timezone.utc)
        sleep_secs = get_fetch_interval(now_utc.hour)
        print(f"Sleeping {sleep_secs}s until next check...")
        time.sleep(sleep_secs)
