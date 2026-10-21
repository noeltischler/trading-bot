"""Live-Schleife: holt nach jedem Kerzenschluss die Kurse, prüft Stops,
zieht sie nach und handelt neue Signale auf dem Übungskonto.

Gleiche Regeln wie im Backtest: Signal auf Kerzenschluss, Ausführung zum
aktuellen Kurs (≈ Eröffnung der nächsten Kerze), Stop-Prüfung je Kerze.
"""

from __future__ import annotations

import logging
import time

from tradingbot.anchor import anchor_due_days
from tradingbot.config import Config
from tradingbot.paper import PaperAccount
from tradingbot.position import Position
from tradingbot.storage import CandleStore
from tradingbot.strategy import FLAT, LONG, signals

log = logging.getLogger("tradingbot")

SIDE_NAME = {LONG: "LONG", -LONG: "SHORT"}


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
                pnl = account.close(coin, hit, "stop")
                log.info("%s %s Stop bei %.2f, Ergebnis %+.2f", coin, SIDE_NAME[pos.side], hit, pnl)
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
            if pos is None and len(account.positions()) < cfg.risk.max_open_positions:
                new = Position.open(coin, sig, price, account.balance, cfg.risk.risk_per_trade_pct,
                                    cfg.risk.stop_pct, int(time.time()))
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
