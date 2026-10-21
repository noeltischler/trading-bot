"""Dünne Hülle um das MetaTrader5-Python-Paket (läuft nur unter Windows)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tradingbot.config import Credentials
from tradingbot.storage import CANDLE_COLUMNS


class MT5Error(RuntimeError):
    pass


@dataclass
class AccountInfo:
    login: int
    server: str
    balance: float
    equity: float
    currency: str
    is_demo: bool


def _mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise MT5Error(
            "Das Paket 'MetaTrader5' fehlt. Es läuft nur unter Windows: pip install MetaTrader5"
        ) from exc
    return mt5


class MT5Client:
    def __init__(self, creds: Credentials, demo_only: bool = True):
        self.creds = creds
        self.demo_only = demo_only
        self.mt5 = _mt5()

    def connect(self) -> AccountInfo:
        kwargs = {}
        if self.creds.path:
            kwargs["path"] = self.creds.path
        if self.creds.login:
            kwargs.update(login=self.creds.login, password=self.creds.password,
                          server=self.creds.server)
        if not self.mt5.initialize(**kwargs):
            raise MT5Error(f"Verbindung zu MT5 fehlgeschlagen: {self.mt5.last_error()}")

        account = self.account()
        if self.demo_only and not account.is_demo:
            self.shutdown()
            raise MT5Error(
                "Sicherheitsstopp: Das verbundene Konto ist KEIN Demokonto. "
                "Der Bot startet nur auf Demokonten (demo_only: true)."
            )
        return account

    def shutdown(self) -> None:
        self.mt5.shutdown()

    def account(self) -> AccountInfo:
        info = self.mt5.account_info()
        if info is None:
            raise MT5Error(f"Kontodaten nicht verfügbar: {self.mt5.last_error()}")
        return AccountInfo(
            login=info.login,
            server=info.server,
            balance=info.balance,
            equity=info.equity,
            currency=info.currency,
            is_demo=info.trade_mode == self.mt5.ACCOUNT_TRADE_MODE_DEMO,
        )

    def find_crypto_symbols(self, coins=("BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE")) -> list[str]:
        """Listet Broker-Symbole, die nach den gewünschten Coins aussehen."""
        symbols = self.mt5.symbols_get() or []
        names = [s.name for s in symbols]
        return sorted(n for n in names if any(n.upper().startswith(c) for c in coins))

    def candles(self, symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
        if not self.mt5.symbol_select(symbol, True):
            raise MT5Error(f"Symbol {symbol} gibt es bei diesem Broker nicht.")
        tf = getattr(self.mt5, f"TIMEFRAME_{timeframe}")
        rates = self.mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        if rates is None or len(rates) == 0:
            raise MT5Error(f"Keine Kursdaten für {symbol}: {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        df = df.rename(columns={"tick_volume": "volume"})
        return df[CANDLE_COLUMNS]
