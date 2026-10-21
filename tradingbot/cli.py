"""Kommandozeile:  python -m tradingbot check | fetch"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradingbot")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Verbindung zu MT5 prüfen und Krypto-Symbole auflisten")
    sub.add_parser("fetch", help="Kursdaten aller Coins abrufen und speichern")
    args = parser.parse_args(argv)

    try:
        return {"check": cmd_check, "fetch": cmd_fetch}[args.command](args)
    except (MT5Error, FileNotFoundError, ValueError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
