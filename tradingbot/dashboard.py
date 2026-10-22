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
from datetime import datetime, timedelta, timezone
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
RANGES = {"6h": ("6 Std", 6 * 3600), "1d": ("1 Tag", 86400), "1w": ("1 Woche", 7 * 86400),
          "1m": ("1 Monat", 30 * 86400), "3m": ("3 Monate", 91 * 86400), "all": ("Alles", None)}
REASONS = {"stop": "Stop", "signal": "Gegensignal", "target": "Gewinnziel"}

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
svg.candles text { fill:var(--muted); font-size:15px }
svg.candles .grid { stroke:var(--line) }
svg.candles .c-up { fill:var(--up); stroke:var(--up) } svg.candles .c-down { fill:var(--down); stroke:var(--down) }
svg.candles .stop { stroke:var(--down); stroke-dasharray:5 4; stroke-width:1.5 }
svg.candles .price { stroke:var(--accent); stroke-dasharray:2 3; stroke-width:1.2 }
svg.candles text.price-l { fill:var(--accent); font-weight:600 } svg.candles text.stop-l { fill:var(--down); font-weight:600 }
svg.candles .trade-up { stroke:var(--up); stroke-width:1.5; stroke-dasharray:3 3 } svg.candles .trade-down { stroke:var(--down); stroke-width:1.5; stroke-dasharray:3 3 }
svg.candles .m-long { fill:var(--up); stroke:var(--card); stroke-width:1 } svg.candles .m-short { fill:var(--down); stroke:var(--card); stroke-width:1 }
svg.candles .entry { stroke:var(--text); stroke-dasharray:6 3; stroke-width:1; opacity:.6 } svg.candles text.entry-l { fill:var(--text); font-weight:600 }
.live { display:inline-block; padding:1px 8px; border-radius:999px; background:var(--accent); color:#fff; font-size:12px; font-weight:650; margin-right:6px }
tr.hl td { background:color-mix(in srgb, var(--accent) 14%, transparent) }
svg.candles .m-exit { fill:var(--card); stroke:var(--text); stroke-width:2 }
.legend { color:var(--muted); font-size:13px; margin-top:6px } .legend b.up, .legend b.down { font-weight:700 }
.periods { display:grid; grid-template-columns:repeat(5,1fr); gap:6px; margin-bottom:12px }
.periods a { display:block; text-align:center; padding:8px 4px; border-radius:10px; border:1px solid var(--line); background:var(--card); color:var(--text); text-decoration:none }
.periods a .l { font-size:12px; color:var(--muted) } .periods a .v { font-size:14px; font-weight:650 }
.periods a.on { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent) inset }
svg.bars text { fill:var(--muted); font-size:16px } svg.bars .grid { stroke:var(--line) }
svg.bars .b-up { fill:var(--up) } svg.bars .b-down { fill:var(--down) }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(110px,1fr)); gap:10px }
.stats div .v { font-weight:650 } .stats div .l { color:var(--muted); font-size:12px }
.filters { display:flex; flex-direction:column; gap:6px; margin-bottom:12px } .filters .pills { margin:0 }
.filters .pills span { color:var(--muted); font-size:13px; align-self:center; min-width:62px }
"""


def _page(title: str, tab: str, body: str) -> str:
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    tabs = "".join(
        f'<a href="{href}" class="{"on" if key == tab else ""}">{label}</a>'
        for key, href, label in (("overview", "/", "Übersicht"), ("trades", "/trades", "Trades"), ("chart", "/chart", "Chart"))
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


PERIODS = {
    "1d": ("Tag", 86400, "hour"),
    "1w": ("Woche", 7 * 86400, "day"),
    "1m": ("Monat", 30 * 86400, "day"),
    "3m": ("3 Monate", 91 * 86400, "week"),
    "1y": ("Jahr", 365 * 86400, "month"),
}
_MONTHS = ("Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez")


def _now(now: int | None) -> int:
    return int(now if now is not None else datetime.now(timezone.utc).timestamp())


def _signed(v: float, digits: int = 2) -> str:
    return f"{v:+,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _bucket_start(ts: int, unit: str) -> datetime:
    d = datetime.fromtimestamp(int(ts), timezone.utc)
    if unit == "hour":
        return d.replace(minute=0, second=0, microsecond=0)
    d = d.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "week":
        return d - timedelta(days=d.weekday())
    if unit == "month":
        return d.replace(day=1)
    return d


def _next_bucket(d: datetime, unit: str) -> datetime:
    if unit == "hour":
        return d + timedelta(hours=1)
    if unit == "day":
        return d + timedelta(days=1)
    if unit == "week":
        return d + timedelta(days=7)
    return d.replace(year=d.year + (d.month == 12), month=d.month % 12 + 1)


def _bucket_label(d: datetime, unit: str) -> str:
    if unit == "hour":
        return d.strftime("%H:00")
    if unit == "month":
        return f"{_MONTHS[d.month - 1]} {d:%y}"
    return d.strftime("%d.%m.")


def pnl_buckets(trades: list[dict], since: int, now: int, unit: str) -> list[tuple[str, float, int]]:
    """Ergebnis je Stunde/Tag/Woche/Monat (nach Schlusszeit), auch leere Abschnitte.
    Liefert (Beschriftung, Ergebnis $, Anzahl Trades)."""
    buckets: dict[datetime, list] = {}
    d = _bucket_start(since, unit)
    while d.timestamp() <= now:
        buckets[d] = [0.0, 0]
        d = _next_bucket(d, unit)
    for t in trades:
        if since <= t["closed_at"] <= now:
            b = buckets.setdefault(_bucket_start(t["closed_at"], unit), [0.0, 0])
            b[0] += t["pnl"]
            b[1] += 1
    return [(_bucket_label(k, unit), v[0], v[1]) for k, v in sorted(buckets.items())]


def bars_svg(buckets: list[tuple[str, float, int]], width: int = 520, height: int = 220) -> str:
    """Balkendiagramm: grün = Gewinn, rot = Verlust je Abschnitt."""
    if not buckets:
        return ""
    left, right, top, bottom = 4, 78, 12, 28
    pw, ph = width - left - right, height - top - bottom
    vals = [b[1] for b in buckets]
    hi, lo = max(max(vals), 0.0), min(min(vals), 0.0)
    if hi == lo:
        hi, lo = 1.0, -1.0
    pad = (hi - lo) * 0.08
    hi, lo = hi + (pad if hi > 0 else 0), lo - (pad if lo < 0 else 0)

    def sy(v):
        return top + (hi - v) / (hi - lo) * ph

    n = len(buckets)
    slot = pw / n
    bw = max(slot * 0.7, 1.0)
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart bars" role="img" aria-label="Ergebnis je Zeitabschnitt">']
    for v in (hi, 0.0, lo):
        if v == 0.0 or abs(v) > 0:
            y = sy(v)
            out.append(f'<line x1="{left}" x2="{left + pw}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                       f'<text x="{left + pw + 4}" y="{y + 4:.1f}">{_signed(v, 0)} $</text>')
    zero = sy(0.0)
    for i, (label, v, count) in enumerate(buckets):
        x = left + i * slot + (slot - bw) / 2
        y = sy(max(v, 0.0))
        h = max(abs(sy(v) - zero), 1.0 if count else 0.0)
        if h:
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" class="{"b-up" if v >= 0 else "b-down"}">'
                       f'<title>{label}: {_signed(v)} $, {count} Trades</title></rect>')
    for i, anchor in ((0, "start"), (n // 2, "middle"), (n - 1, "end")):
        x = {"start": left, "middle": left + i * slot + slot / 2, "end": left + pw}[anchor]
        out.append(f'<text x="{x:.1f}" y="{height - 6}" text-anchor="{anchor}">{buckets[i][0]}</text>')
    out.append("</svg>")
    return "".join(out)


def _stats(trades: list[dict]) -> dict:
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    return {
        "count": len(trades),
        "pnl": sum(t["pnl"] for t in trades),
        "win_rate": 100 * len(wins) / len(trades) if trades else None,
        "avg_win": sum(wins) / len(wins) if wins else None,
        "avg_loss": sum(losses) / len(losses) if losses else None,
        "pf": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "best": max((t["pnl"] for t in trades), default=None),
        "worst": min((t["pnl"] for t in trades), default=None),
    }


def _stats_html(st: dict) -> str:
    def money(v):
        return "–" if v is None else f"<span class='{'up' if v > 0 else 'down' if v < 0 else ''}'>{_signed(v)} $</span>"

    items = [
        ("Ergebnis", money(st["pnl"])), ("Trades", str(st["count"])),
        ("Trefferquote", "–" if st["win_rate"] is None else f"{st['win_rate']:.0f} %"),
        ("Profitfaktor", "–" if st["pf"] is None else f"{st['pf']:.2f}".replace(".", ",")),
        ("Ø Gewinn", money(st["avg_win"])), ("Ø Verlust", money(st["avg_loss"])),
        ("Bester Trade", money(st["best"])), ("Schlechtester", money(st["worst"])),
    ]
    return '<div class="stats">' + "".join(f'<div><div class="v">{v}</div><div class="l">{l}</div></div>' for l, v in items) + "</div>"


def render(account: PaperAccount, title: str = "Trading-Bot", period: str | None = None, now: int | None = None) -> str:
    balance, start = account.balance, account.start_equity
    change = 100 * (balance / start - 1)
    trades = account.trades()
    positions = account.positions()
    wins = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = f"{100 * wins / len(trades):.0f} %" if trades else "–"
    anchors = account.anchors()[-5:] if hasattr(account, "anchors") else []
    now = _now(now)
    period = period if period in PERIODS else "1m"
    label, seconds, unit = PERIODS[period]
    since = now - seconds
    in_period = [t for t in trades if since <= t["closed_at"] <= now]

    tiles = ""
    for key, (name, secs, _) in PERIODS.items():
        pnl = sum(t["pnl"] for t in trades if now - secs <= t["closed_at"] <= now)
        tiles += (f'<a href="/?p={key}" class="{"on" if key == period else ""}"><div class="l">{name}</div>'
                  f'<div class="v {"up" if pnl > 0 else "down" if pnl < 0 else ""}">{_signed(pnl, 0)} $</div></a>')

    before = sum(t["pnl"] for t in trades if t["closed_at"] < since)
    points = [(since, start + before)] + [(t["closed_at"], t["balance"]) for t in in_period]
    if in_period:
        points.append((now, in_period[-1]["balance"]))

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

    unit_name = {"hour": "Stunde", "day": "Tag", "week": "Woche", "month": "Monat"}[unit]
    body = f"""<section class="card kpis">
  <div class="kpi"><div class="v">{_money(balance)} $</div><div class="l">Kontostand</div></div>
  <div class="kpi"><div class="v {'up' if change >= 0 else 'down'}">{f'{change:+.2f}'.replace('.', ',')} %</div><div class="l">seit Start ({_money(start)} $)</div></div>
  <div class="kpi"><div class="v">{len(trades)}</div><div class="l">Trades</div></div>
  <div class="kpi"><div class="v">{win_rate}</div><div class="l">Trefferquote</div></div>
</section>
<div class="periods">{tiles}</div>
<section class="card"><h2>Zeitraum {label} · Ergebnis je {unit_name}</h2>{bars_svg(pnl_buckets(trades, since, now, unit))}
{_stats_html(_stats(in_period))}
<p style="margin:10px 0 0"><a href="/trades?p={period}">Diese Trades ansehen →</a></p></section>
<section class="card"><h2>Kontoverlauf · {label}</h2>{equity_svg(points)}</section>
<section class="card"><h2>Offene Positionen</h2><table><tr><th>Coin</th><th>Seite</th><th>Einstieg</th><th>Stop</th><th>Seit</th></tr>{pos_rows}</table></section>
<section class="card"><h2>Letzte Trades</h2>{_trade_table(trades[-10:])}<p style="margin:10px 0 0"><a href="/trades">Alle Trades →</a></p></section>
<section class="card"><h2>Blockchain-Nachweise</h2><table><tr><th>Tag</th><th>Fingerabdruck</th><th>Transaktion</th></tr>{anchor_rows}</table></section>"""
    return _page(title, "overview", body)


def _trade_table(trades: list[dict], with_coin: bool = True, highlight: int | None = None) -> str:
    rows = "".join(
        f"<tr class='{'hl' if t.get('id') == highlight else ''}'><td><a href='/chart?trade={t.get('id')}'>{_fmt_time(t['opened_at'])} → {_fmt_time(t['closed_at'])}</a></td>"
        + (f"<td>{html.escape(t['coin'])}</td>" if with_coin else "")
        + f"<td>{'Long' if t['side'] == LONG else 'Short'}</td>"
        f"<td>{_money(t['entry'])} → {_money(t['exit'])}</td>"
        f"<td class='{'up' if t['pnl'] > 0 else 'down'}'>{_signed(t['pnl'])}</td></tr>"
        for t in reversed(trades)
    ) or f"<tr><td colspan='{5 if with_coin else 4}' class='muted'>Noch keine Trades</td></tr>"
    head = "<tr><th>Zeit (UTC)</th>" + ("<th>Coin</th>" if with_coin else "") + "<th>Seite</th><th>Kurs</th><th>Ergebnis $</th></tr>"
    return f"<table>{head}{rows}</table>"


def candle_svg(df: pd.DataFrame, tf_seconds: int, trades: list[dict], position=None,
               price: float | None = None, width: int = 520, height: int = 380) -> str:
    """Kerzenchart als Inline-SVG mit Ein- und Ausstiegen, Stop und aktuellem Kurs."""
    if df.empty:
        return '<p class="muted">Für diesen Coin sind noch keine Kurse gespeichert. Sie erscheinen, sobald der Bot läuft.</p>'
    left, right, top, bottom = 4, 76, 12, 28
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
    grid = []
    for i in range(5):  # Preisraster
        v = lo + (hi - lo) * (i + 0.5) / 5
        grid.append((sy(v), _price(v)))
        out.append(f'<line x1="{left}" x2="{left + pw}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>')
    labels: list[tuple[float, str, str]] = []  # Beschriftungen rechts: Stop, Einstieg, Kurs
    for i, anchor in ((0, "start"), (1, "middle"), (2, "end")):  # Zeitachse
        t = t0 + (t1 - t0) * i / 2
        out.append(f'<text x="{sx(t):.1f}" y="{height - 6}" text-anchor="{anchor}">{_fmt_time(t)}</text>')

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
        out.append(f'<line x1="{sx(position.opened_at):.1f}" x2="{left + pw}" y1="{y:.1f}" y2="{y:.1f}" class="stop"/>')
        labels.append((y, "stop-l", "Stop"))
        ye = sy(position.entry)
        out.append(f'<line x1="{sx(position.opened_at):.1f}" x2="{left + pw}" y1="{ye:.1f}" y2="{ye:.1f}" class="entry"/>')
        labels.append((ye, "entry-l", "Einstieg"))
        side = "Long" if position.side == LONG else "Short"
        out.append(entry_mark(position.opened_at, position.entry, position.side, f"{side} Einstieg {_money(position.entry)}"))
    if price:
        y = sy(price)
        out.append(f'<line x1="{left}" x2="{left + pw}" y1="{y:.1f}" y2="{y:.1f}" class="price"/>')
        labels.append((y, "price-l", _price(price)))
    # Rechte Beschriftungen so verschieben, dass sie sich nicht überdecken
    gap, placed = 16.0, []
    for y, cls, text in sorted(labels):
        y = max(y, placed[-1][0] + gap) if placed else y
        placed.append((y, cls, text))
    for y, text in grid:
        if all(abs(y - p[0]) > gap for p in placed):
            out.append(f'<text x="{left + pw + 4}" y="{y + 5:.1f}">{text}</text>')
    for y, cls, text in placed:
        out.append(f'<text x="{left + pw + 4}" y="{y + 5:.1f}" class="{cls}">{text}</text>')
    out.append("</svg>")
    return "".join(out)


def _default_range(tf_seconds: int) -> str:
    """Größter Zeitraum mit höchstens ~200 Kerzen, damit sie am Handy lesbar bleiben."""
    best = "6h"
    for key, (_, seconds) in RANGES.items():
        if seconds and seconds / tf_seconds <= 200:
            best = key
    return best


def render_chart(account: PaperAccount, candles: CandleStore, coins: list[str], timeframe: str,
                 coin: str | None = None, range_key: str | None = None, title: str = "Trading-Bot",
                 now: int | None = None, trade_id: int | None = None) -> str:
    all_trades = account.trades()
    focus = next((t for t in all_trades if t["id"] == trade_id), None) if trade_id is not None else None
    coins = coins or sorted({t["coin"] for t in all_trades} | set(account.positions()))
    if focus and focus["coin"] not in coins:
        coins = [*coins, focus["coin"]]
    if not coins:
        return _page(title, "chart", '<section class="card muted">Noch keine Coins konfiguriert.</section>')
    coin = focus["coin"] if focus else coin if coin in coins else coins[0]
    tf = TIMEFRAME_SECONDS.get(timeframe, 3600)
    now = _now(now)
    until = None
    if focus:  # Ausschnitt um einen einzelnen Trade
        span = max(focus["closed_at"] - focus["opened_at"], 20 * tf)
        since, until = focus["opened_at"] - span, focus["closed_at"] + span
        range_key = None
    else:
        range_key = range_key if range_key in RANGES else _default_range(tf)
        seconds = RANGES[range_key][1]
        since = now - seconds if seconds else 0

    df = candles.load(coin, timeframe, since=since)
    if until is not None:
        df = df[df.time <= until].reset_index(drop=True)
    trades = [t for t in all_trades if t["coin"] == coin]
    first = int(df.time.iloc[0]) if not df.empty else since
    visible = [t for t in trades if t["closed_at"] >= first and (until is None or t["opened_at"] <= until)]
    position = account.position(coin)
    price = None
    raw = account.get_state(f"price:{coin}")
    if raw:
        price = float(raw.split(":")[0])

    def link(c, r):
        return f"/chart?coin={quote(c)}&amp;range={r}"

    coin_pills = "".join(f'<a href="{link(c, range_key or "all")}" class="{"on" if c == coin else ""}">{html.escape(c)}</a>' for c in coins)
    range_pills = "".join(f'<a href="{link(coin, k)}" class="{"on" if k == range_key else ""}">{label}</a>'
                          for k, (label, _) in RANGES.items())

    if position is not None:
        side = "Long" if position.side == LONG else "Short"
        open_pnl = position.pnl(price, account.fee_pct) if price else None
        pnl_html = (f"<span class='{'up' if open_pnl > 0 else 'down'}'>{_signed(open_pnl)} $</span>"
                    if open_pnl is not None else "–")
        status = (f"<span class='live'>LIVE</span><b class='{'up' if position.side == LONG else 'down'}'>{side}</b> seit {_fmt_time(position.opened_at)} UTC · "
                  f"Einstieg {_money(position.entry)} · Stop {_money(position.stop)} · aktuell {_money(price) if price else '–'} · Ergebnis jetzt {pnl_html}")
    else:
        status = "<span class='muted'>Keine offene Position, der Bot wartet auf ein Signal.</span>"

    focus_html = ""
    if focus:
        focus_html = f"""<section class="card"><h2>Trade #{focus['id']}</h2>{_trade_detail(focus)}
<p style="margin:10px 0 0"><a href="{link(coin, _default_range(tf))}">← Zurück zum ganzen Chart</a> · <a href="/trades?coin={quote(coin)}">Alle Trades {html.escape(coin)}</a></p></section>"""

    coin_pnl = sum(t["pnl"] for t in trades)
    note = "" if df.empty or not trades or trades[0]["opened_at"] >= first else \
        f"<p class='muted' style='margin:6px 0 0;font-size:13px'>Kurse sind ab {_fmt_time(first)} gespeichert, ältere Trades stehen in der Liste.</p>"
    live_html = "" if focus else f'<section class="card"><h2>Live-Position {html.escape(coin)}</h2>{status}</section>'
    # Beim Blick auf einen alten Trade keine Live-Linien, die gehören nicht in den Ausschnitt
    chart = candle_svg(df, tf, visible, None if focus else position, None if focus else price)
    body = f"""<div class="pills">{coin_pills}</div>
{live_html}
<section class="card">
  <h2>{html.escape(coin)} · {html.escape(timeframe)}-Kerzen{f" · aktuell {_money(price)} $" if price and not focus else ""}</h2>
  <div class="pills small">{range_pills}</div>
  {chart}
  <div class="legend"><b class="up">▲</b> Einstieg Long · <b class="down">▼</b> Einstieg Short · ○ Ausstieg · <span class="down">- - -</span> Stop · Zeiten in UTC</div>{note}
</section>
{focus_html}
<section class="card"><h2>Trades {html.escape(coin)} ({len(trades)}, zusammen <span class='{'up' if coin_pnl > 0 else 'down'}'>{_signed(coin_pnl)} $</span>)</h2>{_trade_table(trades[-30:], with_coin=False, highlight=focus["id"] if focus else None)}</section>"""
    return _page(title, "chart", body)


def _duration(seconds: int) -> str:
    h = int(seconds) // 3600
    return f"{h // 24} T {h % 24} Std" if h >= 24 else f"{h} Std {int(seconds) % 3600 // 60} Min"


def _move_pct(t: dict) -> float:
    """Kursbewegung in Handelsrichtung, in %."""
    return t["side"] * (t["exit"] / t["entry"] - 1) * 100


def _trade_detail(t: dict) -> str:
    side = "Long" if t["side"] == LONG else "Short"
    rows = [
        ("Coin", html.escape(t["coin"])), ("Seite", side),
        ("Eröffnet", f"{_fmt_time(t['opened_at'])} UTC"), ("Geschlossen", f"{_fmt_time(t['closed_at'])} UTC"),
        ("Dauer", _duration(t["closed_at"] - t["opened_at"])), ("Menge", f"{t['qty']:.6g}"),
        ("Einstieg", f"{_money(t['entry'])} $"), ("Ausstieg", f"{_money(t['exit'])} $"),
        ("Bewegung", f"{_signed(_move_pct(t))} %"), ("Grund", REASONS.get(t["reason"], html.escape(t["reason"]))),
        ("Ergebnis", f"<span class='{'up' if t['pnl'] > 0 else 'down'}'>{_signed(t['pnl'])} $</span>"),
        ("Kontostand danach", f"{_money(t['balance'])} $"),
    ]
    return '<div class="stats">' + "".join(f'<div><div class="v">{v}</div><div class="l">{l}</div></div>' for l, v in rows) + "</div>"


TRADE_FILTERS = {
    "side": {"long": ("Long", lambda t: t["side"] == LONG), "short": ("Short", lambda t: t["side"] != LONG)},
    "result": {"win": ("Gewinn", lambda t: t["pnl"] > 0), "loss": ("Verlust", lambda t: t["pnl"] <= 0)},
}


def filter_trades(trades: list[dict], coin: str | None = None, side: str | None = None, result: str | None = None,
                  period: str | None = None, now: int | None = None) -> list[dict]:
    now = _now(now)
    out = trades
    if coin:
        out = [t for t in out if t["coin"] == coin]
    for key, value in (("side", side), ("result", result)):
        if value in TRADE_FILTERS[key]:
            out = [t for t in out if TRADE_FILTERS[key][value][1](t)]
    if period in PERIODS:
        out = [t for t in out if t["closed_at"] >= now - PERIODS[period][1]]
    return out


def render_trades(account: PaperAccount, title: str = "Trading-Bot", coin: str | None = None, side: str | None = None,
                  result: str | None = None, period: str | None = None, now: int | None = None) -> str:
    trades = account.trades()
    sel = {"coin": coin or "", "side": side or "", "result": result or "", "p": period or ""}
    shown = filter_trades(trades, coin, side, result, period, now)

    def url(path: str = "/trades", **change) -> str:
        q = {**sel, **change}
        return path + "?" + "&amp;".join(f"{k}={quote(v)}" for k, v in q.items() if v)

    def pills(name: str, key: str, options: list[tuple[str, str]]) -> str:
        items = [("", "Alle"), *options]
        return (f'<div class="pills small"><span>{name}</span>'
                + "".join(f'<a href="{url(**{key: v})}" class="{"on" if sel[key] == v else ""}">{html.escape(l)}</a>' for v, l in items)
                + "</div>")

    coins = sorted({t["coin"] for t in trades})
    filters = (pills("Zeitraum", "p", [(k, v[0]) for k, v in PERIODS.items()])
               + pills("Coin", "coin", [(c, c) for c in coins])
               + pills("Seite", "side", [(k, v[0]) for k, v in TRADE_FILTERS["side"].items()])
               + pills("Ergebnis", "result", [(k, v[0]) for k, v in TRADE_FILTERS["result"].items()]))

    rows = "".join(
        f"<tr><td><a href='/chart?trade={t['id']}'>#{t['id']}</a></td><td>{html.escape(t['coin'])}</td>"
        f"<td class='{'up' if t['side'] == LONG else 'down'}'>{'Long' if t['side'] == LONG else 'Short'}</td>"
        f"<td class='{'up' if t['pnl'] > 0 else 'down'}'>{_signed(t['pnl'])}</td><td>{_signed(_move_pct(t))} %</td>"
        f"<td>{_fmt_time(t['opened_at'])}</td><td>{_fmt_time(t['closed_at'])}</td><td>{_duration(t['closed_at'] - t['opened_at'])}</td>"
        f"<td>{_money(t['entry'])}</td><td>{_money(t['exit'])}</td>"
        f"<td>{REASONS.get(t['reason'], html.escape(t['reason']))}</td><td>{_money(t['balance'])}</td></tr>"
        for t in reversed(shown)
    ) or "<tr><td colspan='12' class='muted'>Keine Trades für diese Auswahl</td></tr>"
    head = ("<tr><th>Chart</th><th>Coin</th><th>Seite</th><th>Ergebnis $</th><th>Bewegung</th><th>Eröffnet</th><th>Geschlossen</th>"
            "<th>Dauer</th><th>Einstieg</th><th>Ausstieg</th><th>Grund</th><th>Kontostand</th></tr>")
    body = f"""<section class="card"><div class="filters">{filters}</div>{_stats_html(_stats(shown))}</section>
<section class="card"><h2>{len(shown)} Trades · Zeiten in UTC · <a href="{url("/trades.csv")}">als CSV herunterladen</a></h2>
<table>{head}{rows}</table></section>"""
    return _page(title, "trades", body)


def trades_csv(trades: list[dict]) -> str:
    """Trades als CSV (Semikolon, Dezimalkomma) für Excel/Numbers."""
    lines = ["Nr;Eröffnet (UTC);Geschlossen (UTC);Coin;Seite;Menge;Einstieg;Ausstieg;Bewegung %;Ergebnis $;Grund;Kontostand"]
    for t in trades:
        cells = [t["id"], datetime.fromtimestamp(t["opened_at"], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                 datetime.fromtimestamp(t["closed_at"], timezone.utc).strftime("%Y-%m-%d %H:%M"), t["coin"],
                 "Long" if t["side"] == LONG else "Short", f"{t['qty']:.8g}", f"{t['entry']:.8g}", f"{t['exit']:.8g}",
                 f"{_move_pct(t):.3f}", f"{t['pnl']:.2f}", REASONS.get(t["reason"], t["reason"]), f"{t['balance']:.2f}"]
        lines.append(";".join(str(c).replace(".", ",") if i >= 5 else str(c) for i, c in enumerate(cells)))
    return "\n".join(lines) + "\n"


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
            if url.path not in ("/", "/index.html", "/chart", "/trades", "/trades.csv"):
                self.send_response(404)
                self.end_headers()
                return
            query = parse_qs(url.query)

            def arg(name):
                return query.get(name, [None])[0]

            content_type = "text/html; charset=utf-8"
            account = PaperAccount(db_path, start_equity, fee_pct)
            try:
                if url.path == "/chart":
                    candles = CandleStore(db_path)
                    try:
                        trade = arg("trade")
                        page = render_chart(account, candles, coins or [], timeframe, arg("coin"), arg("range"), title,
                                            trade_id=int(trade) if trade and trade.isdigit() else None)
                    finally:
                        candles.close()
                elif url.path == "/trades":
                    page = render_trades(account, title, arg("coin"), arg("side"), arg("result"), arg("p"))
                elif url.path == "/trades.csv":
                    page = trades_csv(filter_trades(account.trades(), arg("coin"), arg("side"), arg("result"), arg("p")))
                    content_type = "text/csv; charset=utf-8"
                else:
                    page = render(account, title, arg("p"))
                body = page.encode("utf-8-sig" if url.path == "/trades.csv" else "utf-8")
            finally:
                account.close_db()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            if url.path == "/trades.csv":
                self.send_header("Content-Disposition", 'attachment; filename="trades.csv"')
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
