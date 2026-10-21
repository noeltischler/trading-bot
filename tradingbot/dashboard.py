"""Dashboard: eine Handy-taugliche Webseite mit Kontostand, Positionen, Trades,
Kontoverlauf und Blockchain-Nachweisen, dazu ein Reiter "Chart" mit Kerzen je Coin
und den Ein- und Ausstiegen des Bots. Ohne externe Bibliotheken.

python -m tradingbot dashboard   startet einen kleinen Webserver (Standard Port 8080).
Mit DASHBOARD_PASSWORD (Umgebungsvariable) ist die Seite passwortgeschützt.
"""

from __future__ import annotations

import base64
import hmac
import html
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import pandas as pd

from tradingbot.paper import PaperAccount
from tradingbot.storage import CandleStore
from tradingbot.strategy import LONG


def _fmt_time(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%d.%m. %H:%M")


def _money(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _price(v: float) -> str:
    """Kurs fürs Chart: große Kurse ohne Nachkommastellen, damit die Achse schmal bleibt."""
    return _money(v)[:-3] if abs(v) >= 1000 else _money(v)


def equity_svg(points: list[tuple[int, float]], width: int = 640, height: int = 180) -> str:
    """Kontoverlauf als Inline-SVG-Linie."""
    if len(points) < 2:
        return '<p class="muted">Der Verlauf erscheint nach den ersten abgeschlossenen Trades.</p>'
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs) or 1
    y0, y1 = min(ys), max(ys)
    pad = (y1 - y0) * 0.1 or 1.0
    y0, y1 = y0 - pad, y1 + pad

    def sx(x):
        return 8 + (x - x0) / ((x1 - x0) or 1) * (width - 16)

    def sy(y):
        return 8 + (1 - (y - y0) / (y1 - y0)) * (height - 16)

    path = " ".join(f"{'M' if i == 0 else 'L'}{sx(x):.1f},{sy(y):.1f}" for i, (x, y) in enumerate(points))
    base = sy(points[0][1])
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="Kontoverlauf">'
        f'<line x1="8" x2="{width - 8}" y1="{base:.1f}" y2="{base:.1f}" class="baseline"/>'
        f'<path d="{path}" class="line"/></svg>'
    )


TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
RANGES = {"6h": ("6 Std", 6 * 3600), "1d": ("1 Tag", 86400), "1w": ("1 Woche", 7 * 86400), "1m": ("1 Monat", 30 * 86400)}

_CSS = """
:root { --bg:#f6f7f9; --card:#fff; --text:#1a1d23; --muted:#6b7280; --line:#e5e7eb; --up:#15803d; --down:#b91c1c; --accent:#2563eb; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f1115; --card:#181b21; --text:#e8eaee; --muted:#9aa1ac; --line:#2a2f37; --up:#4ade80; --down:#f87171; --accent:#60a5fa; } }
* { box-sizing:border-box }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
main { max-width:760px; margin:0 auto; padding:16px; }
h1 { font-size:20px; margin:4px 0 12px } h2 { font-size:15px; margin:0 0 8px; color:var(--muted); font-weight:600 }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:12px; overflow-x:auto }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px }
.kpi .v { font-size:22px; font-weight:650 } .kpi .l { color:var(--muted); font-size:13px }
table { width:100%; border-collapse:collapse; font-size:14px } td,th { padding:6px 4px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap }
.up { color:var(--up) } .down { color:var(--down) } .muted { color:var(--muted) } .mono { font-family:ui-monospace,Menlo,monospace }
.chart { width:100%; height:auto } .line { fill:none; stroke:var(--accent); stroke-width:2 } .baseline { stroke:var(--line); stroke-dasharray:4 4 }
a { color:var(--accent) } footer { color:var(--muted); font-size:12px; text-align:center; margin:8px 0 24px }
nav.tabs, .pills { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px }
nav.tabs a, .pills a { padding:6px 14px; border-radius:999px; border:1px solid var(--line); background:var(--card); color:var(--text); text-decoration:none; font-size:14px }
nav.tabs a.on, .pills a.on { background:var(--accent); border-color:var(--accent); color:#fff }
.pills.small a { padding:4px 10px; font-size:13px }
svg.candles text { fill:var(--muted); font-size:12px }
svg.candles .grid { stroke:var(--line) }
svg.candles .c-up { fill:var(--up); stroke:var(--up) } svg.candles .c-down { fill:var(--down); stroke:var(--down) }
svg.candles .stop { stroke:var(--down); stroke-dasharray:5 4; stroke-width:1.5 }
svg.candles .price { stroke:var(--accent); stroke-dasharray:2 3; stroke-width:1.2 }
svg.candles text.price-l { fill:var(--accent); font-weight:600 } svg.candles text.stop-l { fill:var(--down); font-weight:600 }
svg.candles .trade-up { stroke:var(--up); stroke-width:1.5; stroke-dasharray:3 3 } svg.candles .trade-down { stroke:var(--down); stroke-width:1.5; stroke-dasharray:3 3 }
svg.candles .m-long { fill:var(--up); stroke:var(--card); stroke-width:1 } svg.candles .m-short { fill:var(--down); stroke:var(--card); stroke-width:1 }
svg.candles .m-exit { fill:var(--card); stroke:var(--text); stroke-width:2 }
.legend { color:var(--muted); font-size:13px; margin-top:6px } .legend b.up, .legend b.down { font-weight:700 }
"""


def _page(title: str, tab: str, body: str) -> str:
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    tabs = "".join(
        f'<a href="{href}" class="{"on" if key == tab else ""}">{label}</a>'
        for key, href, label in (("overview", "/", "Übersicht"), ("chart", "/chart", "Chart"))
    )
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>{html.escape(title)}</title>
<style>{_CSS}</style></head><body><main>
<h1>{html.escape(title)} <span class="muted" style="font-size:13px;font-weight:400">Übungskonto</span></h1>
<nav class="tabs">{tabs}</nav>
{body}
<footer>Stand {now} · aktualisiert sich jede Minute</footer>
</main></body></html>"""


def render(account: PaperAccount, title: str = "Trading-Bot") -> str:
    balance, start = account.balance, account.start_equity
    change = 100 * (balance / start - 1)
    trades = account.trades()
    positions = account.positions()
    wins = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = f"{100 * wins / len(trades):.0f} %" if trades else "–"
    anchors = account.anchors()[-5:] if hasattr(account, "anchors") else []

    points = [(trades[0]["opened_at"], start)] if trades else []
    points += [(t["closed_at"], t["balance"]) for t in trades]

    pos_rows = "".join(
        f"<tr><td><a href='/chart?coin={quote(c)}'>{html.escape(c)}</a></td><td class='{'up' if p.side == LONG else 'down'}'>{'Long' if p.side == LONG else 'Short'}</td>"
        f"<td>{_money(p.entry)}</td><td>{_money(p.stop)}</td><td>{_fmt_time(p.opened_at)}</td></tr>"
        for c, p in positions.items()
    ) or "<tr><td colspan='5' class='muted'>Keine offene Position</td></tr>"

    anchor_rows = "".join(
        f"<tr><td>{html.escape(a['day'])}</td><td class='mono'>{a['hash'][:16]}…</td>"
        f"<td>{('<a href=\"' + html.escape(a['tx_url']) + '\">ansehen</a>') if a.get('tx_url') else 'nur lokal'}</td></tr>"
        for a in reversed(anchors)
    ) or "<tr><td colspan='3' class='muted'>Noch kein Nachweis</td></tr>"

    body = f"""<section class="card kpis">
  <div class="kpi"><div class="v">{_money(balance)} $</div><div class="l">Kontostand</div></div>
  <div class="kpi"><div class="v {'up' if change >= 0 else 'down'}">{f'{change:+.2f}'.replace('.', ',')} %</div><div class="l">seit Start ({_money(start)} $)</div></div>
  <div class="kpi"><div class="v">{len(trades)}</div><div class="l">Trades</div></div>
  <div class="kpi"><div class="v">{win_rate}</div><div class="l">Trefferquote</div></div>
</section>
<section class="card"><h2>Kontoverlauf</h2>{equity_svg(points)}</section>
<section class="card"><h2>Offene Positionen</h2><table><tr><th>Coin</th><th>Seite</th><th>Einstieg</th><th>Stop</th><th>Seit</th></tr>{pos_rows}</table></section>
<section class="card"><h2>Letzte Trades</h2>{_trade_table(trades[-30:])}</section>
<section class="card"><h2>Blockchain-Nachweise</h2><table><tr><th>Tag</th><th>Fingerabdruck</th><th>Transaktion</th></tr>{anchor_rows}</table></section>"""
    return _page(title, "overview", body)


def _trade_table(trades: list[dict], with_coin: bool = True) -> str:
    rows = "".join(
        f"<tr><td>{_fmt_time(t['opened_at'])} → {_fmt_time(t['closed_at'])}</td>"
        + (f"<td>{html.escape(t['coin'])}</td>" if with_coin else "")
        + f"<td>{'Long' if t['side'] == LONG else 'Short'}</td>"
        f"<td>{_money(t['entry'])} → {_money(t['exit'])}</td>"
        f"<td class='{'up' if t['pnl'] > 0 else 'down'}'>{t['pnl']:+.2f}".replace(".", ",") + "</td></tr>"
        for t in reversed(trades)
    ) or f"<tr><td colspan='{5 if with_coin else 4}' class='muted'>Noch keine Trades</td></tr>"
    head = "<tr><th>Zeit (UTC)</th>" + ("<th>Coin</th>" if with_coin else "") + "<th>Seite</th><th>Kurs</th><th>Ergebnis $</th></tr>"
    return f"<table>{head}{rows}</table>"


def candle_svg(df: pd.DataFrame, tf_seconds: int, trades: list[dict], position=None,
               price: float | None = None, width: int = 520, height: int = 380) -> str:
    """Kerzenchart als Inline-SVG mit Ein- und Ausstiegen, Stop und aktuellem Kurs."""
    if df.empty:
        return '<p class="muted">Für diesen Coin sind noch keine Kurse gespeichert. Sie erscheinen, sobald der Bot läuft.</p>'
    left, right, top, bottom = 4, 70, 10, 24
    pw, ph = width - left - right, height - top - bottom
    t0 = int(df.time.iloc[0])
    t1 = int(df.time.iloc[-1]) + 2 * tf_seconds  # Platz für die laufende Kerze

    levels = list(df.high) + list(df.low)
    for t in trades:
        levels += [t["entry"], t["exit"]]
    if position is not None:
        levels += [position.entry, position.stop]
    if price:
        levels.append(price)
    lo, hi = min(levels), max(levels)
    pad = (hi - lo) * 0.05 or hi * 0.01 or 1.0
    lo, hi = lo - pad, hi + pad

    def sx(t):
        return left + min(max((t - t0) / (t1 - t0), 0.0), 1.0) * pw

    def sy(v):
        return top + (1 - (v - lo) / (hi - lo)) * ph

    out = [f'<svg viewBox="0 0 {width} {height}" class="chart candles" role="img" aria-label="Kerzenchart">']
    for i in range(5):  # Preisraster
        v = lo + (hi - lo) * (i + 0.5) / 5
        y = sy(v)
        out.append(f'<line x1="{left}" x2="{left + pw}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                   f'<text x="{left + pw + 4}" y="{y + 4:.1f}">{_price(v)}</text>')
    for i in range(3):  # Zeitachse
        t = t0 + (t1 - t0) * (i + 0.5) / 3
        out.append(f'<text x="{sx(t):.1f}" y="{height - 6}" text-anchor="middle">{_fmt_time(t)}</text>')

    bw = max(pw * tf_seconds / (t1 - t0) * 0.7, 1.0)
    for r in df.itertuples(index=False):
        cls = "c-up" if r.close >= r.open else "c-down"
        x = sx(r.time + tf_seconds / 2)
        y_top, y_bot = sy(max(r.open, r.close)), sy(min(r.open, r.close))
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{sy(r.high):.1f}" y2="{sy(r.low):.1f}" class="{cls}"/>'
                   f'<rect x="{x - bw / 2:.1f}" y="{y_top:.1f}" width="{bw:.1f}" height="{max(y_bot - y_top, 0.8):.1f}" class="{cls}"/>')

    def entry_mark(t, v, side, label):
        x, y = sx(t), sy(v)
        pts = (f"{x:.1f},{y - 2:.1f} {x - 7:.1f},{y + 11:.1f} {x + 7:.1f},{y + 11:.1f}" if side == LONG
               else f"{x:.1f},{y + 2:.1f} {x - 7:.1f},{y - 11:.1f} {x + 7:.1f},{y - 11:.1f}")
        return f'<polygon points="{pts}" class="{"m-long" if side == LONG else "m-short"}"><title>{label}</title></polygon>'

    for t in trades:
        side = "Long" if t["side"] == LONG else "Short"
        cls = "trade-up" if t["pnl"] > 0 else "trade-down"
        out.append(f'<line x1="{sx(t["opened_at"]):.1f}" y1="{sy(t["entry"]):.1f}" x2="{sx(t["closed_at"]):.1f}" y2="{sy(t["exit"]):.1f}" class="{cls}"/>')
        out.append(entry_mark(t["opened_at"], t["entry"], t["side"], f"{side} Einstieg {_money(t['entry'])}"))
        out.append(f'<circle cx="{sx(t["closed_at"]):.1f}" cy="{sy(t["exit"]):.1f}" r="5" class="m-exit">'
                   f'<title>Ausstieg {_money(t["exit"])}, Ergebnis {t["pnl"]:+.2f} $</title></circle>')

    if position is not None:
        y = sy(position.stop)
        out.append(f'<line x1="{sx(position.opened_at):.1f}" x2="{left + pw}" y1="{y:.1f}" y2="{y:.1f}" class="stop"/>'
                   f'<text x="{left + pw + 4}" y="{y + 4:.1f}" class="stop-l">Stop</text>')
        side = "Long" if position.side == LONG else "Short"
        out.append(entry_mark(position.opened_at, position.entry, position.side, f"{side} Einstieg {_money(position.entry)}"))
    if price:
        y = sy(price)
        out.append(f'<line x1="{left}" x2="{left + pw}" y1="{y:.1f}" y2="{y:.1f}" class="price"/>'
                   f'<text x="{left + pw + 4}" y="{y + 4:.1f}" class="price-l">{_price(price)}</text>')
    out.append("</svg>")
    return "".join(out)


def _default_range(tf_seconds: int) -> str:
    """Größter Zeitraum mit höchstens ~200 Kerzen, damit sie am Handy lesbar bleiben."""
    best = "6h"
    for key, (_, seconds) in RANGES.items():
        if seconds / tf_seconds <= 200:
            best = key
    return best


def render_chart(account: PaperAccount, candles: CandleStore, coins: list[str], timeframe: str,
                 coin: str | None = None, range_key: str | None = None, title: str = "Trading-Bot",
                 now: int | None = None) -> str:
    coins = coins or sorted({t["coin"] for t in account.trades()} | set(account.positions()))
    if not coins:
        return _page(title, "chart", '<section class="card muted">Noch keine Coins konfiguriert.</section>')
    coin = coin if coin in coins else coins[0]
    tf = TIMEFRAME_SECONDS.get(timeframe, 3600)
    range_key = range_key if range_key in RANGES else _default_range(tf)
    now = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    since = now - RANGES[range_key][1]

    df = candles.load(coin, timeframe, since=since)
    trades = [t for t in account.trades() if t["coin"] == coin]
    visible = [t for t in trades if t["closed_at"] >= since]
    position = account.position(coin)
    price = None
    raw = account.get_state(f"price:{coin}")
    if raw:
        price = float(raw.split(":")[0])

    def link(c, r):
        return f"/chart?coin={quote(c)}&amp;range={r}"

    coin_pills = "".join(f'<a href="{link(c, range_key)}" class="{"on" if c == coin else ""}">{html.escape(c)}</a>' for c in coins)
    range_pills = "".join(f'<a href="{link(coin, k)}" class="{"on" if k == range_key else ""}">{label}</a>'
                          for k, (label, _) in RANGES.items())

    if position is not None:
        side = "Long" if position.side == LONG else "Short"
        open_pnl = position.pnl(price, account.fee_pct) if price else None
        pnl_html = (f"<span class='{'up' if open_pnl > 0 else 'down'}'>{open_pnl:+.2f} $</span>".replace(".", ",")
                    if open_pnl is not None else "–")
        status = (f"<b class='{'up' if position.side == LONG else 'down'}'>{side}</b> seit {_fmt_time(position.opened_at)} UTC · "
                  f"Einstieg {_money(position.entry)} · Stop {_money(position.stop)} · gerade {pnl_html}")
    else:
        status = "<span class='muted'>Keine offene Position, der Bot wartet auf ein Signal.</span>"

    coin_pnl = sum(t["pnl"] for t in trades)
    body = f"""<div class="pills">{coin_pills}</div>
<section class="card">
  <h2>{html.escape(coin)} · {html.escape(timeframe)}-Kerzen{f" · aktuell {_money(price)} $" if price else ""}</h2>
  <div class="pills small">{range_pills}</div>
  {candle_svg(df, tf, visible, position, price)}
  <div class="legend"><b class="up">▲</b> Einstieg Long · <b class="down">▼</b> Einstieg Short · ○ Ausstieg · <span class="down">- - -</span> Stop · Zeiten in UTC</div>
</section>
<section class="card"><h2>Position</h2>{status}</section>
<section class="card"><h2>Trades {html.escape(coin)} ({len(trades)}, zusammen <span class='{'up' if coin_pnl > 0 else 'down'}'>{f'{coin_pnl:+.2f}'.replace('.', ',')} $</span>)</h2>{_trade_table(trades[-30:], with_coin=False)}</section>"""
    return _page(title, "chart", body)


def serve(db_path: str, start_equity: float, fee_pct: float, port: int = 8080,
          password: str | None = None, title: str = "Trading-Bot", host: str = "127.0.0.1",
          coins: list[str] | None = None, timeframe: str = "1h") -> None:
    password = password if password is not None else os.getenv("DASHBOARD_PASSWORD")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if password and not self._authorized():
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Trading-Bot"')
                self.end_headers()
                return
            url = urlparse(self.path)
            if url.path not in ("/", "/index.html", "/chart"):
                self.send_response(404)
                self.end_headers()
                return
            account = PaperAccount(db_path, start_equity, fee_pct)
            try:
                if url.path == "/chart":
                    query = parse_qs(url.query)
                    candles = CandleStore(db_path)
                    try:
                        page = render_chart(account, candles, coins or [], timeframe,
                                            query.get("coin", [None])[0], query.get("range", [None])[0], title)
                    finally:
                        candles.close()
                else:
                    page = render(account, title)
                body = page.encode("utf-8")
            finally:
                account.close_db()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return False
            try:
                _, _, given = base64.b64decode(header[6:]).decode().partition(":")
            except Exception:
                return False
            return hmac.compare_digest(given, password)

        def log_message(self, *args):
            pass

    print(f"Dashboard läuft auf http://localhost:{port}  (Strg+C beendet)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
