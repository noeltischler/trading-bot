"""Live-Schleife: holt nach jedem Kerzenschluss die Kurse, prüft Stops,
zieht sie nach und handelt neue Signale auf dem Übungskonto.

Gleiche Regeln wie im Backtest: Signal auf Kerzenschluss, Ausführung zum
aktuellen Kurs (≈ Eröffnung der nächsten Kerze), Stop-Prüfung je Kerze.
"""

from __future__ import annotations

import logging
import time

from tradingbot.anchor import anchor_due_days
from tradingbot.backtest import atr_pcts, entry_risk
from tradingbot.config import Config
from tradingbot.indicators import atr
from tradingbot.paper import PaperAccount
from tradingbot.position import Position
from tradingbot.storage import CandleStore
from tradingbot.strategy import FLAT, LONG, signals

log = logging.getLogger("tradingbot")

SIDE_NAME = {LONG: "LONG", -LONG: "SHORT"}


def account_marks(account: PaperAccount, now: float | None = None) -> tuple[float, float]:
    """Höchster Kontostand bisher und Kontostand zu Beginn des heutigen Tages (UTC)."""
    today = int(now if now is not None else time.time()) // 86400
    peak = day_start = account.start_equity
    for t in account.trades():
        peak = max(peak, t["balance"])
        if t["closed_at"] // 86400 < today:
            day_start = t["balance"]
    return peak, day_start


def step(cfg: Config, market, account: PaperAccount, chart: CandleStore | None = None) -> None:
    """Ein Durchlauf über alle Coins. Verarbeitet jede Kerze genau einmal.
    Mit chart werden Kerzen und aktueller Kurs fürs Dashboard gespeichert."""
    for coin, symbol in cfg.symbols.items():
        df = market.closed_candles(symbol, cfg.timeframe, limit=cfg.live_bars)
        if df.empty:
            continue
        if chart is not None:
            chart.upsert(coin, cfg.timeframe, df)
            account.set_state(f"price:{coin}", f"{market.last_price(symbol)}:{int(time.time())}")
        last = df.iloc[-1]
        key = f"last_candle:{coin}"
        if account.get_state(key) == str(int(last.time)):
            continue  # diese Kerze ist schon verarbeitet

        # 1) Offene Position gegen die neue Kerze prüfen (Stop / Nachziehen)
        pos = account.position(coin)
        if pos:
            hit = pos.on_candle(float(last.open), float(last.high), float(last.low), cfg.risk.trailing_stop_pct)
            if hit is not None:
                reason = pos.hit_reason(hit)
                pnl = account.close(coin, hit, reason)
                log.info("%s %s %s bei %.2f, Ergebnis %+.2f", coin, SIDE_NAME[pos.side],
                         "Gewinnziel" if reason == "target" else "Stop", hit, pnl)
                pos = None
            else:
                account.save_position(pos)
                log.info("%s Stop nachgezogen auf %.2f", coin, pos.stop)

        # 2) Signal der gerade geschlossenen Kerze umsetzen
        sig = int(signals(df, cfg.strategy).iloc[-1])
        if sig != FLAT:
            price = market.last_price(symbol)
            if pos and pos.side != sig:
                pnl = account.close(coin, price, "signal")
                log.info("%s %s geschlossen (Gegensignal) bei %.2f, Ergebnis %+.2f",
                         coin, SIDE_NAME[pos.side], price, pnl)
                pos = None
            bp = cfg.backtest_params()
            peak, day_start = account_marks(account)
            risk = entry_risk(bp, account.balance, peak, day_start)
            if pos is None and risk == 0:
                log.info("%s Signal ignoriert: Tagesverlust-Grenze erreicht, Pause bis Mitternacht (UTC)", coin)
            elif pos is None and len(account.positions()) < cfg.risk.max_open_positions:
                stop_pct, trail_pct = atr_pcts(bp, float(atr(df).iloc[-1]), price)
                new = Position.open(coin, sig, price, account.balance, risk, stop_pct, int(time.time()),
                                    bp.take_profit_r, trail_pct, bp.max_position_pct)
                if new:
                    account.open(new)
                    log.info("%s %s eröffnet bei %.2f, Menge %.6f, Stop %.2f",
                             coin, SIDE_NAME[sig], price, new.qty, new.stop)

        account.set_state(key, str(int(last.time)))


def run_forever(cfg: Config, market, account: PaperAccount, poll_seconds: int = 30,
                chart: CandleStore | None = None) -> None:
    log.info("Bot gestartet: %s, %s, Coins: %s, Kontostand %.2f",
             market.id, cfg.timeframe, ", ".join(cfg.symbols), account.balance)
    while True:
        try:
            step(cfg, market, account, chart)
            anchor_due_days(account)
        except Exception:  # Netzwerkfehler usw. dürfen den Dauerbetrieb nicht beenden
            log.exception("Fehler im Durchlauf, neuer Versuch in %s s", poll_seconds)
        time.sleep(poll_seconds)
