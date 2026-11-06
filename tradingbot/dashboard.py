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
from dataclasses import dataclass, field
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
    area = f"{path} L{sx(points[-1][0]):.1f},{height - 8} L{sx(points[0][0]):.1f},{height - 8} Z"
    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="Kontoverlauf">'
        '<defs><linearGradient id="eqfill" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="var(--accent)" stop-opacity=".28"/>'
        '<stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient>'
        '<linearGradient id="eqline" x1="0" y1="0" x2="1" y2="0">'
        '<stop offset="0" stop-color="var(--accent)"/><stop offset="1" stop-color="var(--accent2)"/></linearGradient></defs>'
        f'<path d="{area}" fill="url(#eqfill)"/>'
        f'<line x1="8" x2="{width - 8}" y1="{base:.1f}" y2="{base:.1f}" class="baseline"/>'
        f'<path d="{path}" class="line"/>'
        f'<circle cx="{sx(points[-1][0]):.1f}" cy="{sy(points[-1][1]):.1f}" r="4" class="dot"/></svg>'
    )


TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
RANGES = {"6h": ("6 Std", 6 * 3600), "1d": ("1 Tag", 86400), "1w": ("1 Woche", 7 * 86400),
          "1m": ("1 Monat", 30 * 86400), "3m": ("3 Monate", 91 * 86400), "all": ("Alles", None)}
REASONS = {"stop": "Stop", "signal": "Gegensignal", "target": "Gewinnziel"}

_CSS = """
:root { --bg:#f4f6fb; --card:rgba(255,255,255,.74); --solid:#fff; --text:#0f172a; --muted:#64748b; --line:rgba(15,23,42,.08);
  --up:#059669; --down:#e11d48; --accent:#4f46e5; --accent2:#06b6d4; --glow:rgba(79,70,229,.18);
  --shadow:0 1px 2px rgba(15,23,42,.04), 0 10px 30px -12px rgba(15,23,42,.12); color-scheme:light;
  --edge:rgba(255,255,255,.9); --glass:rgba(255,255,255,.6); --dots:rgba(15,23,42,.07); --hover:rgba(15,23,42,.04); --base:rgba(15,23,42,.18);
  --blob1:rgba(99,102,241,.16); --blob2:rgba(6,182,212,.14); --blob3:rgba(168,85,247,.10); }
:root[data-theme="dark"] { --bg:#070b14; --card:rgba(17,24,39,.62); --solid:#0b1220; --text:#e5e9f2; --muted:#8b97ad; --line:rgba(148,163,184,.14);
  --up:#34d399; --down:#fb7185; --accent:#818cf8; --accent2:#22d3ee; --glow:rgba(129,140,248,.35);
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 12px 32px -12px rgba(0,0,0,.6); color-scheme:dark;
  --edge:rgba(148,163,184,.10); --glass:rgba(17,24,39,.55); --dots:rgba(148,163,184,.10); --hover:rgba(148,163,184,.08); --base:rgba(148,163,184,.3);
  --blob1:rgba(99,102,241,.22); --blob2:rgba(6,182,212,.16); --blob3:rgba(168,85,247,.14); }
* { box-sizing:border-box; -webkit-tap-highlight-color:transparent }
html { background:var(--bg) }
body { margin:0; min-height:100vh; color:var(--text); background:var(--bg);
  font:15px/1.5 "Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; font-feature-settings:"tnum" 1, "cv11" 1; }
body::before { content:""; position:fixed; inset:0; z-index:-2; pointer-events:none;
  background:radial-gradient(900px 520px at 8% -8%, var(--blob1), transparent 60%),
             radial-gradient(760px 480px at 100% 0%, var(--blob2), transparent 60%),
             radial-gradient(700px 600px at 50% 110%, var(--blob3), transparent 60%); }
body::after { content:""; position:fixed; inset:0; z-index:-1; pointer-events:none; opacity:.55;
  background-image:radial-gradient(var(--dots) 1px, transparent 1px); background-size:22px 22px;
  -webkit-mask-image:linear-gradient(180deg,#000,transparent 70%); mask-image:linear-gradient(180deg,#000,transparent 70%); }
main { max-width:820px; margin:0 auto; padding:18px 16px 8px }
header.top { display:flex; align-items:center; gap:12px; margin:4px 0 14px }
header.top > div:nth-child(2) { flex:1; min-width:0 }
.logo { width:38px; height:38px; border-radius:11px; flex:none; display:grid; place-items:center;
  background:linear-gradient(135deg,var(--accent),var(--accent2)); box-shadow:0 6px 18px -4px var(--glow) }
.logo svg { width:20px; height:20px; stroke:#fff; fill:none; stroke-width:2.2; stroke-linecap:round; stroke-linejoin:round }
h1 { font-size:19px; letter-spacing:-.01em; margin:0; line-height:1.2 }
.sub { color:var(--muted); font-size:12.5px; display:flex; align-items:center; gap:6px }
.pulse { flex:none; width:7px; height:7px; border-radius:50%; background:var(--up); box-shadow:0 0 0 0 rgba(5,150,105,.5); animation:pulse 2s infinite }
@keyframes pulse { 70% { box-shadow:0 0 0 7px rgba(5,150,105,0) } 100% { box-shadow:0 0 0 0 rgba(5,150,105,0) } }
h2 { font-size:11.5px; margin:0 0 12px; color:var(--muted); font-weight:650; text-transform:uppercase; letter-spacing:.08em;
  display:flex; align-items:center; gap:8px }
h2::before { content:""; width:6px; height:6px; border-radius:2px; background:linear-gradient(135deg,var(--accent),var(--accent2)) }
h2 a { text-transform:none; letter-spacing:0; font-weight:600 }
.card { position:relative; background:var(--card); -webkit-backdrop-filter:blur(16px) saturate(160%); backdrop-filter:blur(16px) saturate(160%);
  border:1px solid var(--edge); outline:1px solid var(--line); outline-offset:-1px; border-radius:18px; padding:16px;
  margin-bottom:14px; overflow-x:auto; box-shadow:var(--shadow); transition:transform .25s ease, box-shadow .25s ease;
  animation:rise .5s cubic-bezier(.2,.7,.2,1) both }
@media (hover:hover) { .card:hover { transform:translateY(-2px); box-shadow:0 1px 2px rgba(15,23,42,.04), 0 18px 40px -16px rgba(79,70,229,.25) } }
@keyframes rise { from { opacity:0; transform:translateY(8px) } to { opacity:1; transform:none } }
main > :nth-child(3) { animation-delay:.03s } main > :nth-child(4) { animation-delay:.06s } main > :nth-child(5) { animation-delay:.09s }
main > :nth-child(6) { animation-delay:.12s } main > :nth-child(7) { animation-delay:.15s } main > :nth-child(8) { animation-delay:.18s }
.kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:14px }
.kpis .kpi:first-child { grid-column:1 / -1 }
.kpis .kpi:first-child .v { font-size:34px; letter-spacing:-.02em; background:linear-gradient(90deg,var(--text),var(--accent));
  -webkit-background-clip:text; background-clip:text; color:transparent }
@media (max-width:560px) { .kpis { grid-template-columns:repeat(3,1fr) } }
.kpi .v { font-size:20px; font-weight:700; letter-spacing:-.01em } .kpi .l { color:var(--muted); font-size:12.5px }
.kpis .kpi .v.up { color:var(--up) } .kpis .kpi .v.down { color:var(--down) }
table { width:100%; border-collapse:collapse; font-size:13.5px }
td,th { padding:9px 6px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap }
th { color:var(--muted); font-weight:600; font-size:11.5px; text-transform:uppercase; letter-spacing:.05em }
tr:last-child td { border-bottom:0 }
tbody tr, table tr { transition:background .15s } table tr:hover td { background:var(--hover) }
.up { color:var(--up) } .down { color:var(--down) } .muted { color:var(--muted) } .mono { font-family:ui-monospace,"SF Mono",Menlo,monospace }
.chart { width:100%; height:auto; display:block } .line { fill:none; stroke:url(#eqline); stroke-width:2.5; stroke-linejoin:round; stroke-linecap:round }
.dot { fill:var(--solid); stroke:var(--accent); stroke-width:2.5 } .baseline { stroke:var(--base); stroke-dasharray:3 5 }
a { color:var(--accent); text-decoration:none; font-weight:550 } a:hover { text-decoration:underline }
footer { color:var(--muted); font-size:12px; text-align:center; margin:10px 0 28px }
a, button { position:relative; overflow:hidden; transition:transform .12s ease, background .2s, color .2s, box-shadow .2s }
a:active { transform:scale(.96) }
.ripple { position:absolute; border-radius:50%; pointer-events:none; background:currentColor; opacity:.22; transform:scale(0); animation:ripple .55s ease-out forwards }
@keyframes ripple { to { transform:scale(2.6); opacity:0 } }
nav.tabs, .pills { display:flex; gap:4px; flex-wrap:wrap; margin-bottom:14px }
nav.tabs { padding:4px; border-radius:14px; background:var(--glass); -webkit-backdrop-filter:blur(12px); backdrop-filter:blur(12px);
  outline:1px solid var(--line); outline-offset:-1px; box-shadow:var(--shadow); width:max-content; max-width:100% }
nav.tabs a { padding:8px 16px; border-radius:10px; color:var(--muted); font-weight:600; font-size:14px; text-decoration:none }
nav.tabs a.on { color:#fff; background:linear-gradient(135deg,var(--accent),var(--accent2)); box-shadow:0 6px 16px -6px var(--glow) }
nav.tabs a:not(.on):hover { color:var(--text); background:var(--hover) }
nav.bots { margin:0 }
nav.bots a { padding:7px 16px; font-size:14px }
@media (max-width:480px) { header.top { flex-wrap:wrap } nav.bots { margin:4px 0 0; width:100%; order:5 } nav.bots a { flex:1; text-align:center } }
.pills a { padding:6px 13px; border-radius:999px; background:var(--card); color:var(--text); text-decoration:none; font-size:13.5px;
  outline:1px solid var(--line); outline-offset:-1px; font-weight:550 }
.pills a.on { color:#fff; background:linear-gradient(135deg,var(--accent),var(--accent2)); outline-color:transparent }
.pills.small a { padding:4px 10px; font-size:12.5px }
svg.candles text { fill:var(--muted); font-size:15px }
svg.candles .grid { stroke:var(--line) }
svg.candles .c-up { fill:var(--up); stroke:var(--up) } svg.candles .c-down { fill:var(--down); stroke:var(--down) }
svg.candles .stop { stroke:var(--down); stroke-dasharray:5 4; stroke-width:1.5 }
svg.candles .price { stroke:var(--accent); stroke-dasharray:2 3; stroke-width:1.2 }
svg.candles text.price-l { fill:var(--accent); font-weight:600 } svg.candles text.stop-l { fill:var(--down); font-weight:600 }
svg.candles .trade-up { stroke:var(--up); stroke-width:1.5; stroke-dasharray:3 3 } svg.candles .trade-down { stroke:var(--down); stroke-width:1.5; stroke-dasharray:3 3 }
svg.candles .m-long { fill:var(--up); stroke:var(--solid); stroke-width:1 } svg.candles .m-short { fill:var(--down); stroke:var(--solid); stroke-width:1 }
svg.candles .entry { stroke:var(--text); stroke-dasharray:6 3; stroke-width:1; opacity:.6 } svg.candles text.entry-l { fill:var(--text); font-weight:600 }
.live { display:inline-block; padding:1px 8px; border-radius:999px; background:linear-gradient(135deg,var(--accent),var(--accent2)); color:#fff; font-size:12px; font-weight:650; margin-right:6px }
tr.hl td { background:rgba(79,70,229,.08) }
svg.candles .m-exit { fill:var(--solid); stroke:var(--text); stroke-width:2 }
.legend { color:var(--muted); font-size:12.5px; margin-top:8px } .legend b.up, .legend b.down { font-weight:700 }
.periods { display:grid; grid-template-columns:repeat(5,1fr); gap:8px; margin-bottom:14px; animation:rise .5s .05s both }
.periods a { display:block; text-align:center; padding:10px 4px; border-radius:14px; background:var(--card);
  -webkit-backdrop-filter:blur(12px); backdrop-filter:blur(12px); outline:1px solid var(--line); outline-offset:-1px;
  color:var(--text); text-decoration:none; box-shadow:var(--shadow) }
.periods a .l { font-size:11.5px; color:var(--muted); font-weight:600 } .periods a .v { font-size:14.5px; font-weight:700 }
.periods a.on { outline:2px solid var(--accent); outline-offset:-2px; box-shadow:0 8px 22px -10px var(--glow) }
svg.bars text { fill:var(--muted); font-size:16px } .chart.bars { max-width:560px; margin:0 auto } svg.bars .grid { stroke:var(--line) }
svg.bars .b-up { fill:var(--up) } svg.bars .b-down { fill:var(--down) } svg.bars rect { rx:3px }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-top:12px }
@media (max-width:560px) { .stats { grid-template-columns:repeat(2,1fr) } }
.stats > div { padding:10px 12px; border-radius:12px; background:var(--glass); outline:1px solid var(--line); outline-offset:-1px }
.stats div .v { font-weight:700 } .stats div .l { color:var(--muted); font-size:11.5px }
.filters { display:flex; flex-direction:column; gap:8px; margin-bottom:14px } .filters .pills { margin:0 }
.filters .pills span { color:var(--muted); font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.05em; align-self:center; min-width:70px }
code.mono { background:rgba(79,70,229,.07); padding:2px 6px; border-radius:6px; font-size:13px }
.theme { flex:none; width:40px; height:40px; border-radius:12px; border:0; cursor:pointer; display:grid; place-items:center; color:var(--text);
  background:var(--glass); -webkit-backdrop-filter:blur(12px); backdrop-filter:blur(12px); outline:1px solid var(--line); outline-offset:-1px; box-shadow:var(--shadow) }
.theme:active { transform:scale(.92) } .theme svg { width:19px; height:19px; fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round }
.theme .moon { display:none } :root[data-theme="dark"] .theme .moon { display:block } :root[data-theme="dark"] .theme .sun { display:none }
body, .card, nav.tabs, .periods a, .pills a { transition:background-color .3s, color .3s, transform .25s ease, box-shadow .25s ease }
.hit { fill:transparent; cursor:pointer } .hit-g { cursor:pointer } .hit-g:hover line { stroke-width:2.5 }
svg.bars .hit:hover { fill:var(--hover) }
.tip { position:fixed; z-index:50; min-width:220px; max-width:min(320px, calc(100vw - 24px)); padding:12px 14px; border-radius:14px;
  background:rgba(255,255,255,.97); -webkit-backdrop-filter:blur(18px) saturate(160%); backdrop-filter:blur(18px) saturate(160%);
  outline:1px solid var(--line); outline-offset:-1px; box-shadow:0 18px 40px -12px rgba(15,23,42,.35); font-size:13px;
  animation:pop .16s ease-out; pointer-events:auto }
:root[data-theme="dark"] .tip { background:rgba(15,21,36,.97) }
@keyframes pop { from { opacity:0; transform:translateY(4px) scale(.97) } to { opacity:1; transform:none } }
.tip .th { font-weight:700; margin-bottom:6px; font-size:13.5px }
.tip .tr, .tip .tl { display:flex; justify-content:space-between; gap:14px; padding:2px 0 }
.tip .tr span { color:var(--muted) } .tip .tl { font-size:12.5px; border-top:1px solid var(--line); margin-top:4px; padding-top:5px }
.tip .tl + .tl { margin-top:0; border-top:0; padding-top:2px }
@media (prefers-reduced-motion:reduce) { *, *::before { animation:none !important; transition:none !important } }
"""


_LOGO = ('<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 17l5-6 4 4 8-9"/><path d="M15 6h5v5"/></svg>')

_SUN = ('<svg class="sun" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>')
_MOON = '<svg class="moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>'

_JS = """
document.addEventListener('pointerdown', function (e) {
  var a = e.target.closest('a, button'); if (!a) return;
  var r = a.getBoundingClientRect(), d = Math.max(r.width, r.height), s = document.createElement('span');
  s.className = 'ripple'; s.style.width = s.style.height = d + 'px';
  s.style.left = (e.clientX - r.left - d / 2) + 'px'; s.style.top = (e.clientY - r.top - d / 2) + 'px';
  a.appendChild(s); setTimeout(function () { s.remove(); }, 600);
});
var tipEl = null;
function closeTip() { if (tipEl) { tipEl.remove(); tipEl = null; } }
document.addEventListener('click', function (e) {
  var h = e.target.closest('[data-tip]');
  if (tipEl && tipEl.contains(e.target)) return;
  closeTip();
  if (!h) return;
  tipEl = document.createElement('div'); tipEl.className = 'tip'; tipEl.innerHTML = h.getAttribute('data-tip');
  document.body.appendChild(tipEl);
  var w = tipEl.offsetWidth, ht = tipEl.offsetHeight, x = e.clientX - w / 2, y = e.clientY - ht - 14;
  if (y < 8) y = e.clientY + 18;
  x = Math.max(8, Math.min(x, window.innerWidth - w - 8)); y = Math.min(y, window.innerHeight - ht - 8);
  tipEl.style.left = x + 'px'; tipEl.style.top = y + 'px';
});
window.addEventListener('scroll', closeTip, { passive: true });
function toggleTheme() {
  var d = document.documentElement, t = d.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  d.setAttribute('data-theme', t);
  try { localStorage.setItem('theme', t); } catch (e) {}
  var m = document.querySelector('meta[name=theme-color]'); if (m) m.content = t === 'dark' ? '#070b14' : '#f4f6fb';
}
setTimeout(function () { if (document.visibilityState === 'visible') location.reload(); }, 60000);
document.addEventListener('visibilitychange', function () {
  if (document.visibilityState === 'visible' && performance.now() > 60000) location.reload();
});
"""


def _page(title: str, tab: str, body: str) -> str:
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    tabs = "".join(
        f'<a href="{href}" class="{"on" if key == tab else ""}">{label}</a>'
        for key, href, label in (("overview", "/", "Übersicht"), ("trades", "/trades", "Trades"), ("chart", "/chart", "Chart"))
    )
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f4f6fb">
<script>try {{ if (localStorage.getItem('theme') === 'dark') {{ document.documentElement.setAttribute('data-theme', 'dark');
document.querySelector('meta[name=theme-color]').content = '#070b14'; }} }} catch (e) {{}}</script>
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>{_CSS}</style></head><body><main>
<header class="top"><div class="logo">{_LOGO}</div><div><h1>{html.escape(title)}</h1>
<div class="sub"><span class="pulse"></span>Live · Übungskonto · Stand {now}</div></div><!--bots-->
<button class="theme" type="button" onclick="toggleTheme()" aria-label="Hell oder dunkel" title="Hell / Dunkel">{_SUN}{_MOON}</button></header>
<nav class="tabs">{tabs}</nav>
{body}
<footer>Aktualisiert sich jede Minute von selbst</footer>
</main><script>{_JS}</script></body></html>"""


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


def _acct_pct(t: dict) -> float:
    """Ergebnis eines Trades in % vom Kontostand davor."""
    before = t["balance"] - t["pnl"]
    return 100 * t["pnl"] / before if before else 0.0


def _pct(v: float) -> str:
    return f"{v:+.2f} %".replace(".", ",")


def _tip_row(label: str, value: str, cls: str = "") -> str:
    return f'<div class="tr"><span>{label}</span><b class="{cls}">{value}</b></div>'


def trade_tip(t: dict, coin: str | None = None) -> str:
    """Inhalt des Info-Felds für einen Trade (HTML, wird in data-tip escaped)."""
    side = "Long" if t["side"] == LONG else "Short"
    cls = "up" if t["pnl"] > 0 else "down" if t["pnl"] < 0 else ""
    head = f"{html.escape(coin or t.get('coin', ''))} · {side}"
    return (f'<div class="th">{head}</div>'
            + _tip_row("Zeitraum", f"{_fmt_time(t['opened_at'])} → {_fmt_time(t['closed_at'])} UTC")
            + _tip_row("Dauer", _duration(t["closed_at"] - t["opened_at"]))
            + _tip_row("Kurs", f"{_money(t['entry'])} → {_money(t['exit'])} ({_pct(_move_pct(t))})")
            + _tip_row("Ergebnis", f"{_signed(t['pnl'])} $", cls)
            + _tip_row("vom Konto", _pct(_acct_pct(t)), cls)
            + (_tip_row("Strategie", html.escape(t["strategy"])) if t.get("strategy") else ""))


def bucket_tips(trades: list[dict], since: int, now: int, unit: str) -> list[str]:
    """Info-Feld je Balken (gleiche Reihenfolge wie pnl_buckets)."""
    starts = []
    d = _bucket_start(since, unit)
    while d.timestamp() <= now:
        starts.append(d)
        d = _next_bucket(d, unit)
    keys = sorted(set(starts) | {_bucket_start(t["closed_at"], unit) for t in trades if since <= t["closed_at"] <= now})
    tips = []
    for k in keys:
        end = _next_bucket(k, unit)
        inside = [t for t in trades if since <= t["closed_at"] <= now and k.timestamp() <= t["closed_at"] < end.timestamp()]
        pnl = sum(t["pnl"] for t in inside)
        cls = "up" if pnl > 0 else "down" if pnl < 0 else ""
        start_bal = inside[0]["balance"] - inside[0]["pnl"] if inside else 0
        rows = [f'<div class="th">{_fmt_time(int(k.timestamp()))} → {_fmt_time(int(end.timestamp()))} UTC</div>',
                _tip_row("Ergebnis", f"{_signed(pnl)} $", cls),
                _tip_row("vom Konto", _pct(100 * pnl / start_bal) if start_bal else "–", cls),
                _tip_row("Trades", str(len(inside)))]
        for t in inside[:6]:
            c = "up" if t["pnl"] > 0 else "down" if t["pnl"] < 0 else ""
            rows.append(f'<div class="tl"><span>{html.escape(t.get("coin", ""))} {"Long" if t["side"] == LONG else "Short"}'
                        f' {_pct(_move_pct(t))}</span><b class="{c}">{_signed(t["pnl"])} $</b></div>')
        if len(inside) > 6:
            rows.append(f'<div class="tl muted">… und {len(inside) - 6} weitere</div>')
        tips.append("".join(rows))
    return tips


def bars_svg(buckets: list[tuple[str, float, int]], width: int = 520, height: int = 220,
             tips: list[str] | None = None) -> str:
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
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{h:.1f}" class="{"b-up" if v >= 0 else "b-down"}"/>')
        if tips and i < len(tips) and count:  # unsichtbare Tippfläche über die ganze Höhe
            out.append(f'<rect x="{left + i * slot:.1f}" y="{top}" width="{slot:.1f}" height="{ph}" class="hit" '
                       f'data-tip="{html.escape(tips[i], quote=True)}"/>')
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


def _market_html(account: PaperAccount, now: int) -> str:
    """Karte „Marktlage“: Fear & Greed Index und die nächsten Wirtschaftstermine."""
    from tradingbot.events import event_near, next_events

    raw = account.get_state("fear_greed")
    if raw:
        value, label, _ = raw.split(":", 2)
        v = int(value)
        color = "var(--down)" if v <= 25 else "var(--up)" if v >= 75 else "var(--muted)"
        fg = (f'<div class="kpi"><div class="v" style="color:{color}">{v}</div><div class="l">Fear &amp; Greed: {html.escape(label)}</div></div>'
              f'<div style="height:8px;border-radius:4px;margin:6px 0 12px;background:linear-gradient(90deg,var(--down),var(--muted),var(--up));position:relative">'
              f'<span style="position:absolute;left:calc({v}% - 2px);top:-3px;width:4px;height:14px;background:var(--text);border-radius:2px"></span></div>')
    else:
        fg = '<p class="muted">Fear &amp; Greed Index: noch kein Wert (lädt, sobald der Bot läuft).</p>'
    current = event_near(now, 2, 2)
    note = f'<p class="down" style="margin:0 0 8px">Gerade wichtiger Termin: {html.escape(current)}</p>' if current else ""
    rows = "".join(f"<tr><td>{_fmt_time(t)}</td><td>{html.escape(name)}</td></tr>" for t, name in next_events(now, 3)) \
        or "<tr><td colspan='2' class='muted'>Keine Termine eingetragen</td></tr>"
    from tradingbot import news

    items = news.recent(account.conn, now - 86400)
    m = news.mood(items)
    if m is None:
        mood_txt = "noch keine bewerteten Schlagzeilen"
    else:
        word = "eher positiv" if m >= 0.5 else "eher negativ" if m <= -0.5 else "gemischt"
        mood_txt = f'<b class="{"up" if m > 0 else "down" if m < 0 else ""}">{_signed(m, 2)} ({word})</b>'
    heads = "".join(
        f"<tr><td>{_fmt_time(i['published'])}</td><td class='{'up' if i['score'] > 0 else 'down' if i['score'] < 0 else 'muted'}'>{_signed(i['score'], 0)}</td>"
        f"<td><a href='{html.escape(i['link'])}' rel='noopener noreferrer' target='_blank'>{html.escape(i['title'])}</a>"
        f"<div class='muted' style='font-size:12px'>{html.escape(i['source'])}</div></td></tr>"
        for i in items[:8] if i["link"].startswith(("http://", "https://"))
    ) or "<tr><td colspan='3' class='muted'>Noch keine Schlagzeilen (werden alle 15 Minuten geladen)</td></tr>"
    return (f'<section class="card"><h2>Marktlage</h2>{fg}{note}'
            f'<table><tr><th>Nächste Termine (UTC)</th><th></th></tr>{rows}</table></section>'
            f'<section class="card"><h2>Nachrichtenlage (nur zur Beobachtung, kein Einfluss auf Trades)</h2>'
            f'<p style="margin:0 0 8px">Stimmung der letzten 24 Stunden: {mood_txt} · {len(items)} Schlagzeilen</p>'
            f'<table><tr><th>Zeit (UTC)</th><th>Wert</th><th>Schlagzeile</th></tr>{heads}</table></section>')


def _notify_html(account: PaperAccount) -> str:
    """Karte mit dem ntfy-Kanal zum Abonnieren auf dem Handy."""
    from tradingbot import notify

    t = html.escape(notify.topic(account))
    token, chat_id = notify.telegram_config()
    tg = ("<p style='margin:0 0 8px'><b>Telegram ist eingerichtet</b>: Jeder Trade kommt auch als Telegram-Nachricht.</p>"
          if token and chat_id else "")
    return (f'<section class="card"><h2>Benachrichtigungen aufs Handy</h2>{tg}'
            f'<ol style="margin:0;padding-left:20px;line-height:1.6">'
            f'<li>App <b>ntfy</b> installieren (App Store oder Google Play, kostenlos).</li>'
            f'<li>In der App auf <b>+</b> tippen und diesen Kanal eintragen:<br>'
            f'<code class="mono" style="user-select:all;word-break:break-all">{t}</code></li>'
            f'<li>Server bleibt <b>ntfy.sh</b>, dann „Abonnieren“.</li></ol>'
            f'<p class="muted" style="margin:8px 0 0">Der Kanalname ist wie ein Passwort: nicht weitergeben.</p></section>')


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
        f"<td>{_money(p.entry)}</td><td>{_money(p.stop)}</td><td>{_fmt_time(p.opened_at)}</td>"
        f"<td>{html.escape(p.strategy or '–')}</td></tr>"
        for c, p in positions.items()
    ) or "<tr><td colspan='6' class='muted'>Keine offene Position</td></tr>"

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
<section class="card"><h2>Kontoverlauf · {label}</h2>{equity_svg(points)}</section>
<section class="card"><h2>Zeitraum {label} · Ergebnis je {unit_name}</h2>{bars_svg(pnl_buckets(trades, since, now, unit), tips=bucket_tips(trades, since, now, unit))}
<p class="legend">Tipp auf einen Balken für Details.</p>
{_stats_html(_stats(in_period))}
<p style="margin:10px 0 0"><a href="/trades?p={period}">Diese Trades ansehen →</a></p></section>
<section class="card"><h2>Offene Positionen</h2><table><tr><th>Coin</th><th>Seite</th><th>Einstieg</th><th>Stop</th><th>Seit</th><th>Strategie</th></tr>{pos_rows}</table></section>
<section class="card"><h2>Letzte Trades</h2>{_trade_table(trades[-10:])}<p style="margin:10px 0 0"><a href="/trades">Alle Trades →</a></p></section>
{_market_html(account, now)}
{_notify_html(account)}
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
        title = f"<title>{label}</title>" if label else ""
        return f'<polygon points="{pts}" class="{"m-long" if side == LONG else "m-short"}">{title}</polygon>'

    for t in trades:
        cls = "trade-up" if t["pnl"] > 0 else "trade-down"
        tip = html.escape(trade_tip(t), quote=True)
        x0, y0, x1, y1 = sx(t["opened_at"]), sy(t["entry"]), sx(t["closed_at"]), sy(t["exit"])
        out.append(f'<g class="hit-g" data-tip="{tip}">'
                   f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x1:.1f}" y2="{y1:.1f}" class="{cls}"/>'
                   + entry_mark(t["opened_at"], t["entry"], t["side"], "")
                   + f'<circle cx="{x1:.1f}" cy="{y1:.1f}" r="5" class="m-exit"/>'
                   f'<path d="M{min(x0, x1) - 8:.1f},{min(y0, y1) - 12:.1f} h{abs(x1 - x0) + 16:.1f} '
                   f'v{abs(y1 - y0) + 24:.1f} h-{abs(x1 - x0) + 16:.1f} Z" class="hit"/></g>')

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
        ("Strategie", html.escape(t.get("strategy") or "–")),
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
        f"<td>{REASONS.get(t['reason'], html.escape(t['reason']))}</td><td>{html.escape(t.get('strategy') or '–')}</td>"
        f"<td>{_money(t['balance'])}</td></tr>"
        for t in reversed(shown)
    ) or "<tr><td colspan='13' class='muted'>Keine Trades für diese Auswahl</td></tr>"
    head = ("<tr><th>Chart</th><th>Coin</th><th>Seite</th><th>Ergebnis $</th><th>Bewegung</th><th>Eröffnet</th><th>Geschlossen</th>"
            "<th>Dauer</th><th>Einstieg</th><th>Ausstieg</th><th>Grund</th><th>Strategie</th><th>Kontostand</th></tr>")
    body = f"""<section class="card"><div class="filters">{filters}</div>{_stats_html(_stats(shown))}</section>
<section class="card"><h2>{len(shown)} Trades · Zeiten in UTC · <a href="{url("/trades.csv")}">als CSV herunterladen</a></h2>
<table>{head}{rows}</table></section>"""
    return _page(title, "trades", body)


def trades_csv(trades: list[dict]) -> str:
    """Trades als CSV (Semikolon, Dezimalkomma) für Excel/Numbers."""
    lines = ["Nr;Eröffnet (UTC);Geschlossen (UTC);Coin;Seite;Menge;Einstieg;Ausstieg;Bewegung %;Ergebnis $;Grund;Kontostand;Strategie"]
    for t in trades:
        cells = [t["id"], datetime.fromtimestamp(t["opened_at"], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                 datetime.fromtimestamp(t["closed_at"], timezone.utc).strftime("%Y-%m-%d %H:%M"), t["coin"],
                 "Long" if t["side"] == LONG else "Short", f"{t['qty']:.8g}", f"{t['entry']:.8g}", f"{t['exit']:.8g}",
                 f"{_move_pct(t):.3f}", f"{t['pnl']:.2f}", REASONS.get(t["reason"], t["reason"]), f"{t['balance']:.2f}"]
        cells = [str(c).replace(".", ",") if i >= 5 else str(c) for i, c in enumerate(cells)]
        lines.append(";".join(cells + [t.get("strategy") or ""]))
    return "\n".join(lines) + "\n"


@dataclass
class Bot:
    """Ein Konto im Dashboard. Der erste Bot liegt unter /, jeder weitere unter /<slug>/."""
    slug: str
    label: str           # Name im Umschalter, z. B. "Krypto" oder "Gold"
    title: str           # Seitentitel, z. B. "Gold-Bot"
    db_path: str
    start_equity: float
    fee_pct: float
    coins: list[str] = field(default_factory=list)
    timeframe: str = "1h"

    def prefix(self, first: bool) -> str:
        return "" if first else f"/{self.slug}"


PAGES = ("/", "/index.html", "/chart", "/trades", "/trades.csv")


def route(bots: list[Bot], path: str) -> tuple[Bot, str] | None:
    """Welcher Bot und welche Seite zu einem Pfad gehört, None = gibt es nicht."""
    for bot in bots[1:]:
        p = bot.prefix(False)
        if path == p or path.startswith(p + "/"):
            sub = path[len(p):] or "/"
            return (bot, sub) if sub in PAGES else None
    return (bots[0], path) if path in PAGES else None


def bot_switch(bots: list[Bot], current: Bot) -> str:
    if len(bots) < 2:
        return ""
    links = "".join(
        f'<a href="{b.prefix(i == 0) or "/"}" class="{"on" if b is current else ""}">{html.escape(b.label)}</a>'
        for i, b in enumerate(bots))
    return f'<nav class="tabs bots">{links}</nav>'


def page_for(bots: list[Bot], bot: Bot, path: str, query: dict) -> tuple[str, str]:
    """(Inhalt, Content-Type) einer Seite. Links zeigen beim zweiten Bot auf /<slug>/…"""

    def arg(name):
        return query.get(name, [None])[0]

    account = PaperAccount(bot.db_path, bot.start_equity, bot.fee_pct)
    try:
        if path == "/chart":
            candles = CandleStore(bot.db_path)
            try:
                trade = arg("trade")
                page = render_chart(account, candles, bot.coins, bot.timeframe, arg("coin"), arg("range"), bot.title,
                                    trade_id=int(trade) if trade and trade.isdigit() else None)
            finally:
                candles.close()
        elif path == "/trades":
            page = render_trades(account, bot.title, arg("coin"), arg("side"), arg("result"), arg("p"))
        elif path == "/trades.csv":
            return (trades_csv(filter_trades(account.trades(), arg("coin"), arg("side"), arg("result"), arg("p"))),
                    "text/csv; charset=utf-8")
        else:
            page = render(account, bot.title, arg("p"))
    finally:
        account.close_db()
    prefix = bot.prefix(bot is bots[0])
    if prefix:
        page = page.replace('href="/', f'href="{prefix}/').replace("href='/", f"href='{prefix}/")
    page = page.replace("<!--bots-->", bot_switch(bots, bot), 1)
    return page, "text/html; charset=utf-8"


def serve(db_path: str | None = None, start_equity: float = 10_000.0, fee_pct: float = 0.1, port: int = 8080,
          password: str | None = None, title: str = "Trading-Bot", host: str = "127.0.0.1",
          coins: list[str] | None = None, timeframe: str = "1h", bots: list[Bot] | None = None) -> None:
    """Mit bots: mehrere Konten in einem Dashboard, oben umschaltbar."""
    password = password if password is not None else os.getenv("DASHBOARD_PASSWORD")
    bots = bots or [Bot("main", title, title, db_path, start_equity, fee_pct, coins or [], timeframe)]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if password and not self._authorized():
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Trading-Bot"')
                self.end_headers()
                return
            url = urlparse(self.path)
            found = route(bots, url.path)
            if found is None:
                self.send_response(404)
                self.end_headers()
                return
            bot, path = found
            page, content_type = page_for(bots, bot, path, parse_qs(url.query))
            csv = path == "/trades.csv"
            body = page.encode("utf-8-sig" if csv else "utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            if csv:
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
