"""Kommandozeile:  python -m tradingbot fetch | backtest | optimize | run | status | dashboard | verify"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from tradingbot.backtest import run as run_backtest
from tradingbot.config import load_config
from tradingbot.storage import CandleStore


def _market(exchange_id: str):
    from tradingbot.market import Market

    return Market(exchange_id)


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M")


def cmd_fetch(args) -> int:
    cfg = load_config(args.config)
    market = _market(cfg.history_exchange)
    store = CandleStore(cfg.database)
    since_ms = int((time.time() - cfg.history_days * 86400) * 1000)
    try:
        for coin, symbol in cfg.symbols.items():
            df = market.candles(symbol, cfg.timeframe, since_ms=since_ms)
            n = store.upsert(coin, cfg.timeframe, df)
            print(f"{coin} ({symbol} @ {market.id}, {cfg.timeframe}): {n} Kerzen, {_fmt(df.time.iloc[0])} bis {_fmt(df.time.iloc[-1])} UTC")
    finally:
        store.close()
    return 0


def cmd_backtest(args) -> int:
    cfg = load_config(args.config)
    bp = cfg.backtest_params()
    if args.csv:
        sources = {"CSV": pd.read_csv(args.csv)}
    else:
        store = CandleStore(cfg.database)
        sources = {coin: store.load(coin, cfg.timeframe) for coin in cfg.symbols}
        store.close()

    print(f"Strategie {cfg.strategy.label()}, Risiko {bp.risk_pct} %/Trade, Stop {bp.stop_pct} %, Trailing {bp.trail_pct} %, Kosten {bp.fee_pct} %/Seite\n")
    print(f"{'Coin':<8}{'Trades':>8}{'Treffer %':>11}{'Rendite %':>11}{'Max DD %':>10}{'PF':>7}{'Kelly %':>9}")
    for name, df in sources.items():
        if len(df) < 250:
            print(f"{name:<8} zu wenige Kerzen ({len(df)}). Erst 'fetch' ausführen.")
            continue
        s = run_backtest(df, bp, cfg.strategy).summary()
        pf = "-" if s["profit_factor"] is None else s["profit_factor"]
        kelly = "-" if s["kelly_pct"] is None else s["kelly_pct"]
        print(f"{name:<8}{s['trades']:>8}{s['win_rate_pct']:>11}{s['return_pct']:>11}{s['max_drawdown_pct']:>10}{pf:>7}{kelly:>9}")
    print("\nKelly % = rechnerisch bester Einsatz pro Trade. Negativ: kein Vorteil, nicht handeln."
          "\nPositiv: Ein Viertel davon ist eine vorsichtige Obergrenze für risk_per_trade_pct.")
    return 0


def cmd_optimize(args) -> int:
    from tradingbot.optimize import EXIT_VARIANTS, GRID, best, evaluate

    cfg = load_config(args.config)
    store = CandleStore(cfg.database)
    data = {coin: store.load(coin, cfg.timeframe) for coin in cfg.symbols}
    store.close()
    if any(len(df) < 500 for df in data.values()):
        print("Zu wenige Kerzen. Erst 'fetch' ausführen.")
        return 1

    fee = cfg.fee_pct if args.fee is None else args.fee
    bp = cfg.backtest_params(fee)
    print(f"Kosten {fee} % pro Seite. Teste Kombinationen auf den ersten 2/3 der Daten, prüfe auf dem letzten 1/3 ...")
    print("(Rendite = Summe über alle Coins, dauert 1 bis 3 Minuten)\n")
    grid = GRID
    if args.kind:
        grid = {**GRID, "strategy": [s for s in GRID["strategy"] if s["kind"] == args.kind]}
        if not grid["strategy"]:
            print(f"Unbekannte Strategie '{args.kind}'.")
            return 1
    if args.exits:
        grid = {**grid, "exits": EXIT_VARIANTS}
    # Länge des Testteils (letztes Drittel) in Tagen, für "Trades pro Tag"
    test_days = max(min((df.time.iloc[-1] - df.time.iloc[0]) / 86400 for df in data.values()) * 0.33, 1.0)
    # Mit --kind alle Varianten zeigen, auch solche mit wenigen Trades
    min_trades = 0 if args.kind else 15
    if args.per_day:
        min_trades = max(min_trades, math.ceil(args.per_day * test_days))
        print(f"Nur Kombinationen mit mindestens {args.per_day:g} Trades pro Tag (alle Coins zusammen).\n")
    ranking = best(evaluate(data, cfg.timeframe, bp, cfg.strategy, grid=grid), min_trades=min_trades)
    if not ranking:
        print("Keine Kombination mit genug Trades gefunden.")
        return 0

    print(f"{'Kombination':<62}{'Training %':>11}{'Test %':>9}{'Test PF':>9}{'Trades':>8}{'pro Tag':>9}{'Coins +':>9}")
    for r in ranking:
        pf = "-" if r.test_pf is None else r.test_pf
        print(f"{r.combo.label():<62}{r.train_return:>11}{r.test_return:>9}{pf:>9}{r.test_trades:>8}"
              f"{r.test_trades / test_days:>9.1f}{r.coins_positive:>7}/{len(data)}")
    print("\nGut ist eine Kombination nur, wenn sie AUCH im Test im Plus ist (Test % > 0, PF > 1).")
    return 0


def cmd_run(args) -> int:
    from tradingbot.live import run_forever
    from tradingbot.paper import PaperAccount

    cfg = load_config(args.config)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", encoding="utf-8")],
    )
    account = PaperAccount(cfg.paper_database, cfg.start_equity, cfg.fee_pct)
    run_forever(cfg, _market(cfg.exchange), account, chart=CandleStore(cfg.paper_database))
    return 0


def cmd_status(args) -> int:
    from tradingbot.paper import PaperAccount
    from tradingbot.strategy import LONG

    cfg = load_config(args.config)
    acc = PaperAccount(cfg.paper_database, cfg.start_equity, cfg.fee_pct)
    trades = acc.trades()
    change = 100 * (acc.balance / acc.start_equity - 1)
    print(f"Kontostand: {acc.balance:.2f} USD ({change:+.2f} %), {len(trades)} abgeschlossene Trades")
    for coin, p in acc.positions().items():
        side = "LONG" if p.side == LONG else "SHORT"
        print(f"  offen: {coin} {side} seit {_fmt(p.opened_at)}, Einstieg {p.entry:.2f}, Stop {p.stop:.2f}")
    for t in trades[-10:]:
        side = "LONG" if t["side"] == LONG else "SHORT"
        print(f"  {_fmt(t['closed_at'])} {t['coin']:<5}{side:<6} {t['entry']:.2f} → {t['exit']:.2f}  {t['pnl']:+.2f} ({t['reason']})")
    return 0


def cmd_dashboard(args) -> int:
    from tradingbot.dashboard import serve

    cfg = load_config(args.config)
    serve(cfg.paper_database, cfg.start_equity, cfg.fee_pct, port=args.port, host=args.host,
          coins=list(cfg.symbols), timeframe=cfg.timeframe)
    return 0


def cmd_verify(args) -> int:
    from tradingbot.anchor import verify
    from tradingbot.paper import PaperAccount

    cfg = load_config(args.config)
    acc = PaperAccount(cfg.paper_database, cfg.start_equity, cfg.fee_pct)
    anchors = acc.anchors()
    problems = verify(acc)
    if problems:
        print("ACHTUNG, das Trade-Protokoll stimmt nicht mit den Nachweisen überein:")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"Alles in Ordnung: {len(anchors)} Tagesnachweise, {len(acc.trades())} Trades unverändert.")
    for a in anchors[-5:]:
        print(f"  {a['day']}  {a['hash'][:16]}…  {a['tx_url'] or 'nur lokal'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradingbot")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("fetch", help="Kursverlauf für den Backtest laden")
    bt = sub.add_parser("backtest", help="Strategie an gespeicherten Kursdaten testen")
    bt.add_argument("--csv", help="statt der Datenbank eine CSV mit time,open,high,low,close,volume nutzen")
    opt = sub.add_parser("optimize", help="Strategien, Zeitrahmen, Stops und Trailing automatisch durchtesten")
    opt.add_argument("--fee", type=float, help="Kosten pro Seite in %% statt fee_pct aus config.yaml")
    opt.add_argument("--kind", help="nur eine Strategie testen: ema, breakout, meanrev, wedge, fvg, supertrend, fib oder sweep")
    opt.add_argument("--exits", action="store_true", help="zusätzlich Gewinnziele und ATR-Stops ausprobieren")
    opt.add_argument("--per-day", type=float, help="nur Kombinationen mit mindestens so vielen Trades pro Tag zeigen")
    sub.add_parser("run", help="Bot dauerhaft auf dem Übungskonto laufen lassen")
    sub.add_parser("status", help="Kontostand, offene Positionen und letzte Trades anzeigen")
    db = sub.add_parser("dashboard", help="Dashboard als Webseite starten")
    db.add_argument("--port", type=int, default=8080)
    db.add_argument("--host", default="127.0.0.1", help="0.0.0.0 = auch aus dem Netzwerk erreichbar")
    sub.add_parser("verify", help="Trade-Protokoll gegen die Tagesnachweise prüfen")
    args = parser.parse_args(argv)

    commands = {"fetch": cmd_fetch, "backtest": cmd_backtest, "optimize": cmd_optimize, "run": cmd_run, "status": cmd_status,
                "dashboard": cmd_dashboard, "verify": cmd_verify}
    try:
        return commands[args.command](args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        if type(exc).__module__.startswith("ccxt"):
            print(f"Fehler bei der Börse: {exc}", file=sys.stderr)
            return 1
        raise
    except KeyboardInterrupt:
        print("Beendet.")
        return 0
