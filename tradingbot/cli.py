"""Kommandozeile:  python -m tradingbot check | fetch | backtest"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import pandas as pd

from tradingbot.backtest import BacktestParams, run as run_backtest
from tradingbot.config import load_config, load_credentials
from tradingbot.mt5_client import MT5Client, MT5Error
from tradingbot.storage import CandleStore


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    client = MT5Client(load_credentials(), demo_only=cfg.demo_only)
    account = client.connect()
    try:
        kind = "DEMO" if account.is_demo else "ECHTGELD"
        print(f"Verbunden: Konto {account.login} @ {account.server} ({kind})")
        print(f"Kontostand: {account.balance:.2f} {account.currency}")
        print("\nKrypto-Symbole bei diesem Broker:")
        for name in client.find_crypto_symbols():
            print(f"  {name}")
        print("\nTrage die passenden Namen unter 'symbols' in config.yaml ein.")
    finally:
        client.shutdown()
    return 0


def cmd_fetch(args) -> int:
    cfg = load_config(args.config)
    client = MT5Client(load_credentials(), demo_only=cfg.demo_only)
    client.connect()
    store = CandleStore(cfg.database)
    try:
        for coin, symbol in cfg.symbols.items():
            df = client.candles(symbol, cfg.timeframe, cfg.history_bars)
            n = store.upsert(coin, cfg.timeframe, df)
            first = datetime.fromtimestamp(int(df.time.iloc[0]), timezone.utc)
            last = datetime.fromtimestamp(int(df.time.iloc[-1]), timezone.utc)
            print(f"{coin} ({symbol}, {cfg.timeframe}): {n} Kerzen, {first:%Y-%m-%d %H:%M} bis {last:%Y-%m-%d %H:%M} UTC")
    finally:
        store.close()
        client.shutdown()
    return 0


def cmd_backtest(args) -> int:
    cfg = load_config(args.config)
    bp = BacktestParams(
        risk_pct=cfg.risk.risk_per_trade_pct,
        stop_pct=cfg.risk.stop_pct,
        trail_pct=cfg.risk.trailing_stop_pct,
        fee_pct=cfg.fee_pct,
    )
    if args.csv:
        sources = {"CSV": pd.read_csv(args.csv)}
    else:
        store = CandleStore(cfg.database)
        sources = {coin: store.load(coin, cfg.timeframe) for coin in cfg.symbols}
        store.close()

    print(f"Risiko {bp.risk_pct} %/Trade, Stop {bp.stop_pct} %, Trailing {bp.trail_pct} %, Kosten {bp.fee_pct} %/Seite\n")
    print(f"{'Coin':<8}{'Trades':>8}{'Treffer %':>11}{'Rendite %':>11}{'Max DD %':>10}{'PF':>7}")
    for name, df in sources.items():
        if len(df) < 250:
            print(f"{name:<8} zu wenige Kerzen ({len(df)}). Erst 'fetch' ausführen.")
            continue
        s = run_backtest(df, bp).summary()
        pf = "-" if s["profit_factor"] is None else s["profit_factor"]
        print(f"{name:<8}{s['trades']:>8}{s['win_rate_pct']:>11}{s['return_pct']:>11}{s['max_drawdown_pct']:>10}{pf:>7}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradingbot")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Verbindung zu MT5 prüfen und Krypto-Symbole auflisten")
    sub.add_parser("fetch", help="Kursdaten aller Coins abrufen und speichern")
    bt = sub.add_parser("backtest", help="Strategie an gespeicherten Kursdaten testen")
    bt.add_argument("--csv", help="statt der Datenbank eine CSV mit time,open,high,low,close,volume nutzen")
    args = parser.parse_args(argv)

    try:
        return {"check": cmd_check, "fetch": cmd_fetch, "backtest": cmd_backtest}[args.command](args)
    except (MT5Error, FileNotFoundError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
