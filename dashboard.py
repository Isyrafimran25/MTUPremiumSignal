# -*- coding: utf-8 -*-
# MTU Premium Signal Dashboard
# Runs as a simple web server on Railway alongside the bot

import os
import json
import requests
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "Isyrafimran25/MTUPremiumSignal")
PORT = int(os.environ.get("PORT", os.environ.get("RAILWAY_PORT", "8080")))


def github_get_file(filename: str) -> str:
    if not GITHUB_TOKEN:
        return None
    try:
        import base64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
        r = requests.get(url, headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }, timeout=10)
        if r.status_code == 200:
            return base64.b64decode(r.json()["content"]).decode("utf-8")
    except:
        pass
    return None


def load_signals() -> list:
    content = github_get_file("open_signals.json")
    if content:
        try:
            return json.loads(content)
        except:
            pass
    return []


def calc_stats(signals: list) -> dict:
    total = len(signals)
    wins = losses = open_count = 0
    win_pips = loss_pips = 0.0
    best_trade = worst_trade = None
    best_pips = worst_pips = 0.0

    weekly = []
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    for s in signals:
        try:
            opened = datetime.fromisoformat(s.get("opened_utc", ""))
            if opened >= week_ago:
                weekly.append(s)
        except:
            pass

        status    = s.get("status", "open")
        direction = s.get("type", "BUY")
        entry     = s.get("entry", 0)

        if status == "closed":
            pips = round(abs(s.get("tp3", entry) - entry) * 10, 1)
            wins += 1; win_pips += pips
            if pips > best_pips:
                best_pips = pips
                best_trade = f"{direction} +{pips} pips (TP3)"
        elif status == "tp2_hit":
            pips = round(abs(s.get("tp2", entry) - entry) * 10, 1)
            wins += 1; win_pips += pips
            if pips > best_pips:
                best_pips = pips
                best_trade = f"{direction} +{pips} pips (TP2)"
        elif status == "tp1_hit":
            pips = round(abs(s.get("tp1", entry) - entry) * 10, 1)
            wins += 1; win_pips += pips
            if pips > best_pips:
                best_pips = pips
                best_trade = f"{direction} +{pips} pips (TP1)"
        elif status == "sl_hit":
            pips = round(abs(s.get("sl", entry) - entry) * 10, 1)
            losses += 1; loss_pips += pips
            if worst_trade is None or pips > worst_pips:
                worst_pips = pips
                worst_trade = f"{direction} -{pips} pips (SL)"
        else:
            open_count += 1

    closed = wins + losses
    win_rate = round(wins / closed * 100, 1) if closed > 0 else 0
    net_pips = round(win_pips - loss_pips, 1)

    return {
        "total": total, "wins": wins, "losses": losses,
        "open": open_count, "win_rate": win_rate,
        "win_pips": round(win_pips, 1), "loss_pips": round(loss_pips, 1),
        "net_pips": net_pips, "weekly_count": len(weekly),
        "best_trade": best_trade or "N/A",
        "worst_trade": worst_trade or "N/A",
    }


def render_signal_row(s: dict) -> str:
    status    = s.get("status", "open")
    direction = s.get("type", "BUY")
    entry     = s.get("entry", 0)
    opened    = s.get("opened_utc", "")[:16].replace("T", " ")
    conf      = s.get("confidence", "")

    # MYT time
    try:
        dt_utc = datetime.fromisoformat(s.get("opened_utc", ""))
        dt_myt = dt_utc + timedelta(hours=8)
        opened = dt_myt.strftime("%d %b %H:%M MYT")
    except:
        pass

    status_map = {
        "closed":   ('<span class="badge win">TP3</span>', "win-row"),
        "tp2_hit":  ('<span class="badge win">TP2</span>', "win-row"),
        "tp1_hit":  ('<span class="badge win">TP1</span>', "win-row"),
        "sl_hit":   ('<span class="badge loss">SL</span>', "loss-row"),
        "open":     ('<span class="badge open">Open</span>', ""),
    }
    badge, row_class = status_map.get(status, ('', ''))

    dir_badge = f'<span class="badge {"buy" if direction=="BUY" else "sell"}">{direction}</span>'
    conf_badge = f'<span class="badge {"high" if conf=="HIGH" else "med"}">{conf}</span>' if conf else ""

    # Calculate pips result
    if status == "closed":
        result_pips = f'+{round(abs(s.get("tp3",entry)-entry)*10,1)}'
    elif status == "tp2_hit":
        result_pips = f'+{round(abs(s.get("tp2",entry)-entry)*10,1)}'
    elif status == "tp1_hit":
        result_pips = f'+{round(abs(s.get("tp1",entry)-entry)*10,1)}'
    elif status == "sl_hit":
        result_pips = f'-{round(abs(s.get("sl",entry)-entry)*10,1)}'
    else:
        result_pips = "Running..."

    return f'''
    <tr class="{row_class}">
        <td>{opened}</td>
        <td>{dir_badge} {conf_badge}</td>
        <td>{entry}</td>
        <td>{s.get("sl","")}</td>
        <td>{s.get("tp1","")} / {s.get("tp2","")} / {s.get("tp3","")}</td>
        <td><strong>{result_pips}</strong></td>
        <td>{badge}</td>
    </tr>'''


def build_html() -> str:
    signals = load_signals()
    stats   = calc_stats(signals)
    now_myt = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%d %b %Y %H:%M MYT")

    # Color for net pips
    net_color = "#22c55e" if stats["net_pips"] >= 0 else "#ef4444"
    net_sign  = "+" if stats["net_pips"] >= 0 else ""

    # Win rate color
    wr = stats["win_rate"]
    wr_color = "#22c55e" if wr >= 60 else "#f59e0b" if wr >= 45 else "#ef4444"

    # Signal rows -- latest first
    rows = "".join(render_signal_row(s) for s in reversed(signals[-50:]))
    if not rows:
        rows = '<tr><td colspan="7" style="text-align:center;padding:2rem;color:#888">No signals yet</td></tr>'

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MTU Premium | Signal Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0f1117; color: #e2e8f0; min-height: 100vh; }}
  .header {{ background: linear-gradient(135deg, #1a1f2e, #16213e);
             padding: 1.5rem 2rem; border-bottom: 1px solid #2d3748;
             display: flex; align-items: center; gap: 1rem; }}
  .header h1 {{ font-size: 1.4rem; font-weight: 700; color: #f6c90e; }}
  .header p  {{ font-size: 0.8rem; color: #94a3b8; }}
  .gold {{ color: #f6c90e; }}
  .container {{ max-width: 1100px; margin: 0 auto; padding: 1.5rem; }}
  .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                 gap: 1rem; margin-bottom: 1.5rem; }}
  .stat-card {{ background: #1a1f2e; border: 1px solid #2d3748;
                border-radius: 12px; padding: 1.2rem; text-align: center; }}
  .stat-card .label {{ font-size: 0.75rem; color: #94a3b8; text-transform: uppercase;
                       letter-spacing: 0.05em; margin-bottom: 0.5rem; }}
  .stat-card .value {{ font-size: 1.8rem; font-weight: 700; }}
  .table-wrap {{ background: #1a1f2e; border: 1px solid #2d3748;
                 border-radius: 12px; overflow: hidden; }}
  .table-wrap h2 {{ padding: 1rem 1.5rem; font-size: 1rem; font-weight: 600;
                    border-bottom: 1px solid #2d3748; color: #f6c90e; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ padding: 0.75rem 1rem; text-align: left; color: #94a3b8;
        font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;
        border-bottom: 1px solid #2d3748; }}
  td {{ padding: 0.75rem 1rem; border-bottom: 1px solid #1e2535; }}
  tr:last-child td {{ border-bottom: none; }}
  .win-row  {{ background: rgba(34,197,94,0.04); }}
  .loss-row {{ background: rgba(239,68,68,0.04); }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 99px;
            font-size: 0.72rem; font-weight: 600; }}
  .badge.win  {{ background: rgba(34,197,94,0.15);  color: #22c55e; }}
  .badge.loss {{ background: rgba(239,68,68,0.15);  color: #ef4444; }}
  .badge.open {{ background: rgba(251,191,36,0.15); color: #fbbf24; }}
  .badge.buy  {{ background: rgba(34,197,94,0.15);  color: #22c55e; }}
  .badge.sell {{ background: rgba(239,68,68,0.15);  color: #ef4444; }}
  .badge.high {{ background: rgba(249,115,22,0.15); color: #f97316; }}
  .badge.med  {{ background: rgba(99,102,241,0.15); color: #818cf8; }}
  .footer {{ text-align: center; padding: 1.5rem; color: #4a5568; font-size: 0.75rem; }}
  @media (max-width: 600px) {{ th:nth-child(3), td:nth-child(3),
    th:nth-child(4), td:nth-child(4) {{ display: none; }} }}
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>MTU Premium Signal Dashboard</h1>
    <p>XAUUSD Scalping Signals | Updated: {now_myt}</p>
  </div>
</div>
<div class="container">

  <div class="stats-grid">
    <div class="stat-card">
      <div class="label">Total Signals</div>
      <div class="value gold">{stats["total"]}</div>
    </div>
    <div class="stat-card">
      <div class="label">Win Rate</div>
      <div class="value" style="color:{wr_color}">{stats["win_rate"]}%</div>
    </div>
    <div class="stat-card">
      <div class="label">Wins</div>
      <div class="value" style="color:#22c55e">{stats["wins"]}</div>
    </div>
    <div class="stat-card">
      <div class="label">Losses</div>
      <div class="value" style="color:#ef4444">{stats["losses"]}</div>
    </div>
    <div class="stat-card">
      <div class="label">Net Pips</div>
      <div class="value" style="color:{net_color}">{net_sign}{stats["net_pips"]}</div>
    </div>
    <div class="stat-card">
      <div class="label">Open Signals</div>
      <div class="value" style="color:#fbbf24">{stats["open"]}</div>
    </div>
    <div class="stat-card">
      <div class="label">Best Trade</div>
      <div class="value" style="font-size:0.9rem;color:#22c55e">{stats["best_trade"]}</div>
    </div>
    <div class="stat-card">
      <div class="label">Worst Trade</div>
      <div class="value" style="font-size:0.9rem;color:#ef4444">{stats["worst_trade"]}</div>
    </div>
  </div>

  <div class="table-wrap">
    <h2>Signal History (Latest 50)</h2>
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Direction</th>
          <th>Entry</th>
          <th>SL</th>
          <th>TP1 / TP2 / TP3</th>
          <th>Result</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

</div>
<div class="footer">
  MTU Premium | XAUUSD Signals | Not financial advice
</div>
</body>
</html>'''


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = build_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # suppress access logs


if __name__ == "__main__":
    import sys
    print(f"Dashboard starting on port {PORT}...", flush=True)
    print(f"Python version: {sys.version}", flush=True)
    try:
        server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
        print(f"Dashboard live at http://0.0.0.0:{PORT}", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"Server error: {e}", flush=True)
        raise
