"""
XAUUSD AI Scalping Signal Bot — MTU Premium
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Strategy (score-based, fires at 4+ out of 8):
  1. Market Structure  — HH+HL (bullish) or LL+LH (bearish)         +2
  2. S&R Levels        — Price near key support or resistance         +1
  3. S&D Zones         — Price inside supply or demand zone           +2
  4. Chart Patterns    — Double top/bottom, pin bar, inside bar       +1
  5. Engulfing Candle  — Bullish or bearish engulfing                 +1
  6. RSI               — Oversold (<40) or overbought (>60)           +1
  7. EMA               — EMA9 cross or alignment                      +1
  8. MACD              — Bullish or bearish crossover                 +1

Scalping timeframe: 15-min candles
Sessions: Asia (00–08 UTC), London (07–16 UTC), New York (13–21 UTC)
"""

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

# ── Config ────────────────────────────────────────────────────────────────────
MAX_SIGNALS_PER_DAY = 10
COOLDOWN_MINUTES    = 90
SIGNAL_COUNT_FILE   = "signal_count.json"
SYMBOL              = "XAU/USD"
INTERVAL            = "15min"


# ── Session helpers ───────────────────────────────────────────────────────────

SESSIONS = {
    "Asia":     (0,  8),
    "London":   (7,  16),
    "New York": (13, 21),
}

def get_current_session(utc_hour: int) -> str:
    active = [n for n, (s, e) in SESSIONS.items() if s <= utc_hour < e]
    return " / ".join(active) if active else "Off-hours"


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


def fetch_market_data() -> dict:
    print("  -> candles (50)")
    price = td_get("time_series", outputsize=50)

    print("  -> RSI(14)")
    rsi   = td_get("rsi",  time_period=14, outputsize=5)

    print("  -> EMA(9)")
    ema9  = td_get("ema",  time_period=9,  outputsize=5)

    print("  -> EMA(21)")
    ema21 = td_get("ema",  time_period=21, outputsize=5)

    print("  -> MACD")
    macd  = td_get("macd", fast_period=12, slow_period=26,
                   signal_period=9, outputsize=5)

    print("  -> ATR(14)")
    atr   = td_get("atr",  time_period=14, outputsize=20)

    candles = []
    for v in price["values"]:
        candles.append({
            "open":  float(v["open"]),
            "high":  float(v["high"]),
            "low":   float(v["low"]),
            "close": float(v["close"]),
            "dt":    v["datetime"],
        })

    latest   = candles[0]
    atr_vals = [float(v["atr"]) for v in atr["values"]]

    return {
        "candles":       candles,
        "price":         latest["close"],
        "prev_close":    candles[1]["close"],
        "open":          latest["open"],
        "high":          latest["high"],
        "low":           latest["low"],
        "rsi":           float(rsi["values"][0]["rsi"]),
        "rsi_prev":      float(rsi["values"][1]["rsi"]),
        "ema9":          float(ema9["values"][0]["ema"]),
        "ema9_prev":     float(ema9["values"][1]["ema"]),
        "ema21":         float(ema21["values"][0]["ema"]),
        "ema21_prev":    float(ema21["values"][1]["ema"]),
        "macd":          float(macd["values"][0]["macd"]),
        "macd_signal":   float(macd["values"][0]["macd_signal"]),
        "macd_prev":     float(macd["values"][1]["macd"]),
        "macd_sig_prev": float(macd["values"][1]["macd_signal"]),
        "atr":           atr_vals[0],
        "avg_atr":       sum(atr_vals) / len(atr_vals),
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
    if atr < avg_atr * 0.75:
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

    # ── BUY score ─────────────────────────────────────────────────────────────
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

    if rsi < 40:
        buy_score += 1
        buy_reasons.append(f"RSI {rsi:.1f} — oversold")
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

    if rsi > 60:
        sell_score += 1
        sell_reasons.append(f"RSI {rsi:.1f} — overbought")
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
    MIN_SCORE = 4

    if buy_score >= sell_score and buy_score >= MIN_SCORE:
        confidence = "HIGH" if buy_score >= 6 else "MEDIUM"
        analysis   = {**buy_data, "sr": sr, "sd": sd, "score": buy_score}
        return "BUY", buy_reasons, confidence, buy_score, analysis

    if sell_score > buy_score and sell_score >= MIN_SCORE:
        confidence = "HIGH" if sell_score >= 6 else "MEDIUM"
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

    # ── Max risk cap: 30–50 pips ─────────────────────────────────────────────
    MAX_RISK_PIPS = 40   # target middle of 30–50 range
    MAX_SL_PIPS   = 50   # hard cap — signal blocked if SL > 50 pips

    if signal_type == "BUY":
        entry  = price
        sl_sr  = round(sr.get("support", price - atr * 1.2) - atr * 0.3, 2)
        sl_atr = round(price - atr * 1.2, 2)
        sl_raw = max(sl_sr, sl_atr)
        # Cap SL to MAX_RISK_PIPS below entry
        sl     = round(max(sl_raw, price - MAX_RISK_PIPS), 2)
        # TP levels scale from actual risk so R:R stays intact
        actual_risk = round(price - sl, 2)
        tp1    = round(price + actual_risk * 0.9,  2)
        tp2    = round(price + actual_risk * 1.8,  2)
        tp3_sr = sr.get("resistance", round(price + actual_risk * 3.0, 2))
        tp3    = round(min(tp3_sr, price + actual_risk * 3.0), 2)
    else:
        entry  = price
        sl_sr  = round(sr.get("resistance", price + atr * 1.2) + atr * 0.3, 2)
        sl_atr = round(price + atr * 1.2, 2)
        sl_raw = min(sl_sr, sl_atr)
        # Cap SL to MAX_RISK_PIPS above entry
        sl     = round(min(sl_raw, price + MAX_RISK_PIPS), 2)
        actual_risk = round(sl - price, 2)
        tp1    = round(price - actual_risk * 0.9,  2)
        tp2    = round(price - actual_risk * 1.8,  2)
        tp3_sr = sr.get("support", round(price - actual_risk * 3.0, 2))
        # For SELL: TP3 must be LOWER than TP2 (price going down), use min()
        tp3    = round(min(tp3_sr, price - actual_risk * 3.0), 2)

    # Block signal if risk still exceeds hard cap (e.g. gap open)
    actual_risk = round(abs(entry - sl), 2)
    if actual_risk > MAX_SL_PIPS:
        print(f"Signal blocked — risk {actual_risk} pips exceeds {MAX_SL_PIPS} pip hard cap.")
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
        zone_note = "Price is currently inside a demand zone — high-probability long area."
    elif signal_type == "SELL" and sd.get("in_supply"):
        zone_note = "Price is currently inside a supply zone — high-probability short area."
    elif signal_type == "BUY" and sd.get("nearest_demand"):
        z = sd["nearest_demand"]
        zone_note = f"Nearest demand zone sits at {z[0]}–{z[1]}."
    elif signal_type == "SELL" and sd.get("nearest_supply"):
        z = sd["nearest_supply"]
        zone_note = f"Nearest supply zone sits at {z[0]}–{z[1]}."

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
• Close 50% at TP1 — move SL to breakeven
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

    demand_str = (f"{sd['nearest_demand'][0]}–{sd['nearest_demand'][1]}"
                  if sd.get("nearest_demand") else "No nearby zone")
    supply_str = (f"{sd['nearest_supply'][0]}–{sd['nearest_supply'][1]}"
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
 3. Give a clear actionable bias — buy dips, sell rallies, or wait for breakout confirmation.
 Keep it professional and concise.]

🕐 Sessions Today (MYT):
🌏 Asia: 08:00 – 16:00
🇬🇧 London: 15:00 – 00:00
🇺🇸 New York: 21:00 – 05:00

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

    if session == "Off-hours":
        print("Di luar sesi dagangan. Dilangkau.")
        return

    state = load_state()
    if state["count"] >= MAX_SIGNALS_PER_DAY:
        print(f"Had harian dicapai ({MAX_SIGNALS_PER_DAY}). Selesai untuk hari ini.")
        return
    if not cooldown_ok(state):
        print(f"Cooldown aktif — {COOLDOWN_MINUTES} minit antara isyarat.")
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
#  SIGNAL TRACKER — monitors open signals and posts live updates
# ══════════════════════════════════════════════════════════════════════════════

OPEN_SIGNALS_FILE = "open_signals.json"

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
        title   = "TP3 HIT — FULL CLOSE!"
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
        f"{emoji} SIGNAL UPDATE — {direction} {direction_emoji}\n"
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
            # Don't continue — also check running profit below

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


# ── Updated main — now also saves signal for tracking ─────────────────────────

def main():
    now_utc  = datetime.now(timezone.utc)
    utc_hour = now_utc.hour
    session  = get_current_session(utc_hour)

    print(f"[{now_utc.strftime('%Y-%m-%d %H:%M')} UTC] Bot running...")
    print(f"Session: {session}")

    if session == "Off-hours":
        print("Di luar sesi dagangan. Dilangkau.")
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
        print(f"Cooldown aktif — {COOLDOWN_MINUTES} minit antara isyarat.")
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

    # ── Calculate levels (same logic as generate_signal_message) ─────────────
    price = data["price"]
    atr   = data["atr"]
    MAX_RISK_PIPS = 40
    if signal_type == "BUY":
        entry  = price
        sl_sr  = round(sr.get("support", price - atr * 1.2) - atr * 0.3, 2)
        sl_atr = round(price - atr * 1.2, 2)
        sl_raw = max(sl_sr, sl_atr)
        sl     = round(max(sl_raw, price - MAX_RISK_PIPS), 2)
        actual_risk = round(price - sl, 2)
        tp1    = round(price + actual_risk * 0.9,  2)
        tp2    = round(price + actual_risk * 1.8,  2)
        tp3_sr = sr.get("resistance", round(price + actual_risk * 3.0, 2))
        tp3    = round(min(tp3_sr, price + actual_risk * 3.0), 2)
    else:
        entry  = price
        sl_sr  = round(sr.get("resistance", price + atr * 1.2) + atr * 0.3, 2)
        sl_atr = round(price + atr * 1.2, 2)
        sl_raw = min(sl_sr, sl_atr)
        sl     = round(min(sl_raw, price + MAX_RISK_PIPS), 2)
        actual_risk = round(sl - price, 2)
        tp1    = round(price - actual_risk * 0.9,  2)
        tp2    = round(price - actual_risk * 1.8,  2)
        tp3_sr = sr.get("support", round(price - actual_risk * 3.0, 2))
        # For SELL: TP3 must be LOWER than TP2 (price going down), use min()
        tp3    = round(min(tp3_sr, price - actual_risk * 3.0), 2)

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

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "morning":
        morning_update()
    else:
        main()
