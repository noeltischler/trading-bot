"""Marktstimmung: Crypto Fear & Greed Index von alternative.me.

Ein Tageswert von 0 (extreme Angst) bis 100 (extreme Gier), kostenlos und mit
Verlauf seit 2018, deshalb auch im Backtest prüfbar. Der Wert eines Tages wird
erst ab dem Folgetag benutzt, damit der Backtest nicht in die Zukunft schaut.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd

URL = "https://api.alternative.me/fng/?limit={limit}&format=json"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fear_greed (
    day_start INTEGER PRIMARY KEY,  -- Unix-Sekunden, UTC, Tagesbeginn
    value     INTEGER NOT NULL,
    label     TEXT    NOT NULL
);
"""

LABELS_DE = {
    "Extreme Fear": "Extreme Angst", "Fear": "Angst", "Neutral": "Neutral",
    "Greed": "Gier", "Extreme Greed": "Extreme Gier",
}


def fetch_fear_greed(limit: int = 0, timeout: float = 15.0) -> pd.DataFrame:
    """Lädt den Index (limit=0: kompletter Verlauf). Spalten: day_start, value, label."""
    req = urllib.request.Request(URL.format(limit=limit), headers={"User-Agent": "tradingbot"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)["data"]
    rows = [(int(d["timestamp"]) // 86400 * 86400, int(d["value"]), d["value_classification"]) for d in data]
    return pd.DataFrame(rows, columns=["day_start", "value", "label"]).sort_values("day_start").reset_index(drop=True)


def save(path: str | Path, fg: pd.DataFrame) -> int:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_SCHEMA)
        with conn:
            conn.executemany("INSERT OR REPLACE INTO fear_greed VALUES (?,?,?)",
                             [(int(r.day_start), int(r.value), str(r.label)) for r in fg.itertuples(index=False)])
    finally:
        conn.close()
    return len(fg)


def load(path: str | Path) -> pd.DataFrame:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_SCHEMA)
        return pd.read_sql_query("SELECT day_start, value, label FROM fear_greed ORDER BY day_start", conn)
    finally:
        conn.close()


def with_fear_greed(df: pd.DataFrame, fg: pd.DataFrame) -> pd.DataFrame:
    """Hängt je Kerze den Index-Wert des VORTAGS an (Spalte fear_greed)."""
    out = df.copy()
    if fg.empty:
        out["fear_greed"] = float("nan")
        return out
    s = pd.Series(fg["value"].to_numpy(dtype=float), index=fg["day_start"].to_numpy() + 86400)
    day = (df["time"] // 86400 * 86400).to_numpy()
    out["fear_greed"] = s.reindex(sorted(set(s.index) | set(day))).ffill().reindex(day).to_numpy()
    return out
