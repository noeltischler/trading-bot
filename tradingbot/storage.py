"""Speichert Kerzen (OHLCV) in einer lokalen SQLite-Datenbank."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

CANDLE_COLUMNS = ["time", "open", "high", "low", "close", "volume"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    coin      TEXT    NOT NULL,
    timeframe TEXT    NOT NULL,
    time      INTEGER NOT NULL,  -- Unix-Sekunden, UTC, Kerzenbeginn
    open      REAL    NOT NULL,
    high      REAL    NOT NULL,
    low       REAL    NOT NULL,
    close     REAL    NOT NULL,
    volume    REAL    NOT NULL,
    PRIMARY KEY (coin, timeframe, time)
);
"""


class CandleStore:
    def __init__(self, path: str | Path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def upsert(self, coin: str, timeframe: str, candles: pd.DataFrame) -> int:
        """Schreibt Kerzen; vorhandene Zeitpunkte werden überschrieben
        (die letzte Kerze ist beim Abruf oft noch nicht abgeschlossen)."""
        if candles.empty:
            return 0
        rows = [
            (coin, timeframe, int(r.time), float(r.open), float(r.high),
             float(r.low), float(r.close), float(r.volume))
            for r in candles[CANDLE_COLUMNS].itertuples(index=False)
        ]
        with self.conn:
            self.conn.executemany(
                "INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?,?)", rows
            )
        return len(rows)

    def load(self, coin: str, timeframe: str, since: int | None = None) -> pd.DataFrame:
        query = "SELECT time, open, high, low, close, volume FROM candles WHERE coin=? AND timeframe=?"
        params: list = [coin, timeframe]
        if since is not None:
            query += " AND time >= ?"
            params.append(since)
        query += " ORDER BY time"
        return pd.read_sql_query(query, self.conn, params=params)

    def last_time(self, coin: str, timeframe: str) -> int | None:
        row = self.conn.execute(
            "SELECT MAX(time) FROM candles WHERE coin=? AND timeframe=?", (coin, timeframe)
        ).fetchone()
        return row[0]
