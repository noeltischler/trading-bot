"""Übungskonto: verwaltet Spielgeld, offene Positionen und abgeschlossene Trades
in SQLite. Echte Orders werden nie gesendet."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from tradingbot.position import Position

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    start_equity REAL NOT NULL,
    balance REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    coin TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    coin       TEXT NOT NULL,
    side       INTEGER NOT NULL,
    qty        REAL NOT NULL,
    entry      REAL NOT NULL,
    exit       REAL NOT NULL,
    opened_at  INTEGER NOT NULL,
    closed_at  INTEGER NOT NULL,
    pnl        REAL NOT NULL,
    reason     TEXT NOT NULL,
    balance    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS anchors (
    day     TEXT PRIMARY KEY,   -- YYYY-MM-DD (UTC), umfasst alle Trades bis Tagesende
    hash    TEXT NOT NULL,      -- SHA-256 über Trades + vorherigen Hash
    prev    TEXT NOT NULL,
    tx_url  TEXT                -- Link zur Blockchain-Transaktion, falls veröffentlicht
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class PaperAccount:
    def __init__(self, path: str | Path, start_equity: float = 10_000.0, fee_pct: float = 0.1):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(_SCHEMA)
        self.fee_pct = fee_pct
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO account VALUES (1, ?, ?)", (start_equity, start_equity)
            )

    # --- Kontostand ---------------------------------------------------------
    @property
    def balance(self) -> float:
        return self.conn.execute("SELECT balance FROM account").fetchone()[0]

    @property
    def start_equity(self) -> float:
        return self.conn.execute("SELECT start_equity FROM account").fetchone()[0]

    # --- Positionen ---------------------------------------------------------
    def positions(self) -> dict[str, Position]:
        rows = self.conn.execute("SELECT coin, data FROM positions").fetchall()
        return {coin: Position(**json.loads(data)) for coin, data in rows}

    def position(self, coin: str) -> Position | None:
        return self.positions().get(coin)

    def save_position(self, pos: Position) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO positions VALUES (?, ?)", (pos.coin, json.dumps(pos.to_dict()))
            )

    def open(self, pos: Position) -> None:
        if self.position(pos.coin):
            raise ValueError(f"Für {pos.coin} ist bereits eine Position offen.")
        self.save_position(pos)

    def close(self, coin: str, price: float, reason: str, when: int | None = None) -> float:
        pos = self.position(coin)
        if pos is None:
            raise ValueError(f"Für {coin} ist keine Position offen.")
        pnl = pos.pnl(price, self.fee_pct)
        when = int(when if when is not None else time.time())
        with self.conn:
            self.conn.execute("UPDATE account SET balance = balance + ?", (pnl,))
            balance = self.balance
            self.conn.execute(
                "INSERT INTO trades (coin, side, qty, entry, exit, opened_at, closed_at, pnl, reason, balance)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (coin, pos.side, pos.qty, pos.entry, price, pos.opened_at, when, pnl, reason, balance),
            )
            self.conn.execute("DELETE FROM positions WHERE coin = ?", (coin,))
        return pnl

    # --- Verlauf und Zustand -----------------------------------------------
    def trades(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM trades ORDER BY id")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def anchors(self) -> list[dict]:
        cur = self.conn.execute("SELECT day, hash, prev, tx_url FROM anchors ORDER BY day")
        return [dict(zip(("day", "hash", "prev", "tx_url"), row)) for row in cur.fetchall()]

    def add_anchor(self, day: str, digest: str, prev: str, tx_url: str | None) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO anchors VALUES (?, ?, ?, ?)", (day, digest, prev, tx_url))

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, value))

    def close_db(self) -> None:
        self.conn.close()
