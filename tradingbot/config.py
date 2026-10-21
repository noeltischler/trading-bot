"""Lädt config.yaml und die MT5-Zugangsdaten aus .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 0.75
    stop_pct: float = 1.0
    trailing_stop_pct: float = 1.0
    max_open_positions: int = 3


@dataclass
class Credentials:
    login: int | None
    password: str | None
    server: str | None
    path: str | None


@dataclass
class Config:
    symbols: dict[str, str]
    timeframe: str = "M15"
    history_bars: int = 5000
    database: str = "data/market.db"
    demo_only: bool = True
    fee_pct: float = 0.05
    risk: RiskConfig = field(default_factory=RiskConfig)


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} fehlt. Kopiere config.example.yaml nach config.yaml."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    symbols = raw.get("symbols") or {}
    if not symbols:
        raise ValueError("In config.yaml ist kein Coin unter 'symbols' eingetragen.")

    timeframe = str(raw.get("timeframe", "M15")).upper()
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unbekannter Zeitrahmen {timeframe}. Erlaubt: {TIMEFRAMES}")

    risk = RiskConfig(**(raw.get("risk") or {}))
    if not 0 < risk.risk_per_trade_pct <= 1.0:
        raise ValueError("risk_per_trade_pct muss zwischen 0 und 1 % liegen.")

    return Config(
        symbols={str(k): str(v) for k, v in symbols.items()},
        timeframe=timeframe,
        history_bars=int(raw.get("history_bars", 5000)),
        database=str(raw.get("database", "data/market.db")),
        demo_only=bool(raw.get("demo_only", True)),
        fee_pct=float(raw.get("fee_pct", 0.05)),
        risk=risk,
    )


def load_credentials() -> Credentials:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    login = os.getenv("MT5_LOGIN")
    return Credentials(
        login=int(login) if login else None,
        password=os.getenv("MT5_PASSWORD") or None,
        server=os.getenv("MT5_SERVER") or None,
        path=os.getenv("MT5_PATH") or None,
    )
