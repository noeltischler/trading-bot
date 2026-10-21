"""Dashboard: eine Handy-taugliche Webseite mit Kontostand, Positionen, Trades,
Kontoverlauf und Blockchain-Nachweisen. Ohne externe Bibliotheken.

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

from tradingbot.paper import PaperAccount
from tradingbot.strategy import LONG


def _fmt_time(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%d.%m. %H:%M")


def _money(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


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
        f"<tr><td>{html.escape(c)}</td><td class='{'up' if p.side == LONG else 'down'}'>{'Long' if p.side == LONG else 'Short'}</td>"
        f"<td>{_money(p.entry)}</td><td>{_money(p.stop)}</td><td>{_fmt_time(p.opened_at)}</td></tr>"
        for c, p in positions.items()
    ) or "<tr><td colspan='5' class='muted'>Keine offene Position</td></tr>"

    trade_rows = "".join(
        f"<tr><td>{_fmt_time(t['closed_at'])}</td><td>{html.escape(t['coin'])}</td>"
        f"<td>{'Long' if t['side'] == LONG else 'Short'}</td>"
        f"<td>{_money(t['entry'])} → {_money(t['exit'])}</td>"
        f"<td class='{'up' if t['pnl'] > 0 else 'down'}'>{t['pnl']:+.2f}".replace(".", ",") + "</td></tr>"
        for t in reversed(trades[-30:])
    ) or "<tr><td colspan='5' class='muted'>Noch keine Trades</td></tr>"

    anchor_rows = "".join(
        f"<tr><td>{html.escape(a['day'])}</td><td class='mono'>{a['hash'][:16]}…</td>"
        f"<td>{('<a href=\"' + html.escape(a['tx_url']) + '\">ansehen</a>') if a.get('tx_url') else 'nur lokal'}</td></tr>"
        for a in reversed(anchors)
    ) or "<tr><td colspan='3' class='muted'>Noch kein Nachweis</td></tr>"

    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>{html.escape(title)}</title>
<style>
:root {{ --bg:#f6f7f9; --card:#fff; --text:#1a1d23; --muted:#6b7280; --line:#e5e7eb; --up:#15803d; --down:#b91c1c; --accent:#2563eb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1115; --card:#181b21; --text:#e8eaee; --muted:#9aa1ac; --line:#2a2f37; --up:#4ade80; --down:#f87171; --accent:#60a5fa; }} }}
* {{ box-sizing:border-box }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
main {{ max-width:760px; margin:0 auto; padding:16px; }}
h1 {{ font-size:20px; margin:4px 0 12px }} h2 {{ font-size:15px; margin:0 0 8px; color:var(--muted); font-weight:600 }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:12px; overflow-x:auto }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px }}
.kpi .v {{ font-size:22px; font-weight:650 }} .kpi .l {{ color:var(--muted); font-size:13px }}
table {{ width:100%; border-collapse:collapse; font-size:14px }} td,th {{ padding:6px 4px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap }}
.up {{ color:var(--up) }} .down {{ color:var(--down) }} .muted {{ color:var(--muted) }} .mono {{ font-family:ui-monospace,Menlo,monospace }}
.chart {{ width:100%; height:auto }} .line {{ fill:none; stroke:var(--accent); stroke-width:2 }} .baseline {{ stroke:var(--line); stroke-dasharray:4 4 }}
a {{ color:var(--accent) }} footer {{ color:var(--muted); font-size:12px; text-align:center; margin:8px 0 24px }}
</style></head><body><main>
<h1>{html.escape(title)} <span class="muted" style="font-size:13px;font-weight:400">Übungskonto</span></h1>
<section class="card kpis">
  <div class="kpi"><div class="v">{_money(balance)} $</div><div class="l">Kontostand</div></div>
  <div class="kpi"><div class="v {'up' if change >= 0 else 'down'}">{f'{change:+.2f}'.replace('.', ',')} %</div><div class="l">seit Start ({_money(start)} $)</div></div>
  <div class="kpi"><div class="v">{len(trades)}</div><div class="l">Trades</div></div>
  <div class="kpi"><div class="v">{win_rate}</div><div class="l">Trefferquote</div></div>
</section>
<section class="card"><h2>Kontoverlauf</h2>{equity_svg(points)}</section>
<section class="card"><h2>Offene Positionen</h2><table><tr><th>Coin</th><th>Seite</th><th>Einstieg</th><th>Stop</th><th>Seit</th></tr>{pos_rows}</table></section>
<section class="card"><h2>Letzte Trades</h2><table><tr><th>Zeit (UTC)</th><th>Coin</th><th>Seite</th><th>Kurs</th><th>Ergebnis $</th></tr>{trade_rows}</table></section>
<section class="card"><h2>Blockchain-Nachweise</h2><table><tr><th>Tag</th><th>Fingerabdruck</th><th>Transaktion</th></tr>{anchor_rows}</table></section>
<footer>Stand {now} · aktualisiert sich jede Minute</footer>
</main></body></html>"""


def serve(db_path: str, start_equity: float, fee_pct: float, port: int = 8080,
          password: str | None = None, title: str = "Trading-Bot", host: str = "127.0.0.1") -> None:
    password = password if password is not None else os.getenv("DASHBOARD_PASSWORD")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if password and not self._authorized():
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Trading-Bot"')
                self.end_headers()
                return
            if self.path not in ("/", "/index.html"):
                self.send_response(404)
                self.end_headers()
                return
            account = PaperAccount(db_path, start_equity, fee_pct)
            try:
                body = render(account, title).encode("utf-8")
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
