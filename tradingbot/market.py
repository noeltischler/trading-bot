"""Öffentliche Kursdaten einer Börse über ccxt (kein Konto nötig)."""

from __future__ import annotations

import time

import pandas as pd

from tradingbot.storage import CANDLE_COLUMNS


class MarketError(RuntimeError):
    pass


class Market:
    def __init__(self, exchange_id: str = "kraken"):
        import ccxt

        if not hasattr(ccxt, exchange_id):
            raise MarketError(f"Unbekannte Börse '{exchange_id}'.")
        self.ex = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        self.id = exchange_id

    def candles(self, symbol: str, timeframe: str, since_ms: int | None = None,
                limit: int | None = None) -> pd.DataFrame:
        """Kerzen als DataFrame (time in Unix-Sekunden). Mit since_ms wird
        seitenweise bis heute nachgeladen, soweit die Börse das hergibt."""
        rows: list[list] = []
        cursor = since_ms
        while True:
            batch = self.ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=limit)
            if not batch:
                break
            new = [r for r in batch if not rows or r[0] > rows[-1][0]]
            if not new:
                break
            rows.extend(new)
            if since_ms is None or len(batch) < 2:
                break
            cursor = rows[-1][0] + 1
            if cursor > time.time() * 1000:
                break
        if not rows:
            raise MarketError(f"Keine Kursdaten für {symbol} bei {self.id}.")
        df = pd.DataFrame(rows, columns=CANDLE_COLUMNS)
        df["time"] = df["time"] // 1000
        return df

    def closed_candles(self, symbol: str, timeframe: str, limit: int = 400) -> pd.DataFrame:
        """Die letzten abgeschlossenen Kerzen (die laufende Kerze wird entfernt)."""
        df = self.candles(symbol, timeframe, limit=limit)
        period = self.ex.parse_timeframe(timeframe)
        return df[df["time"] + period <= time.time()].reset_index(drop=True)

    def last_price(self, symbol: str) -> float:
        return float(self.ex.fetch_ticker(symbol)["last"])

    def timeframe_seconds(self, timeframe: str) -> int:
        return int(self.ex.parse_timeframe(timeframe))
