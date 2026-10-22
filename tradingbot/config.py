"""Lädt config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from tradingbot.strategy import StrategyParams

TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h", "1d")


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 0.75
    stop_pct: float = 1.0
    trailing_stop_pct: float = 1.0
    max_open_positions: int = 3
    # Sicherheitsregeln (0 = aus)
    stop_atr: float = 0.0             # Stop-Abstand in ATR statt in % (passt sich der Schwankung an)
    trail_atr: float = 0.0            # Trailing-Abstand in ATR statt in %
    take_profit_r: float = 0.0        # Gewinnziel als Vielfaches des Stop-Abstands
    max_position_pct: float = 0.0     # Positionswert höchstens so viel % vom Konto
    daily_loss_limit_pct: float = 0.0  # nach so viel % Tagesverlust keine neuen Trades bis Mitternacht (UTC)
    drawdown_brake_pct: float = 0.0   # Risiko halbieren, solange das Konto so viel % unter dem Höchststand ist


@dataclass
class Config:
    symbols: dict[str, str]
    exchange: str = "kraken"
    history_exchange: str = "coinbaseexchange"
    timeframe: str = "15m"
    history_days: int = 90
    live_bars: int = 400
    database: str = "data/market.db"
    paper_database: str = "data/paper.db"
    start_equity: float = 10_000.0
    fee_pct: float = 0.1
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyParams = field(default_factory=StrategyParams)

    def backtest_params(self, fee_pct: float | None = None):
        """Risikoregeln aus der Config als BacktestParams (gleich für Backtest und live)."""
        from tradingbot.backtest import BacktestParams

        r = self.risk
        return BacktestParams(
            start_equity=self.start_equity, risk_pct=r.risk_per_trade_pct, stop_pct=r.stop_pct,
            trail_pct=r.trailing_stop_pct, fee_pct=self.fee_pct if fee_pct is None else fee_pct,
            stop_atr=r.stop_atr, trail_atr=r.trail_atr, take_profit_r=r.take_profit_r,
            max_position_pct=r.max_position_pct, daily_loss_limit_pct=r.daily_loss_limit_pct,
            drawdown_brake_pct=r.drawdown_brake_pct,
        )


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

    timeframe = str(raw.get("timeframe", "15m"))
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unbekannter Zeitrahmen {timeframe}. Erlaubt: {TIMEFRAMES}")

    risk = RiskConfig(**(raw.get("risk") or {}))
    if not 0 < risk.risk_per_trade_pct <= 1.0:
        raise ValueError("risk_per_trade_pct muss zwischen 0 und 1 % liegen.")

    return Config(
        symbols={str(k): str(v) for k, v in symbols.items()},
        exchange=str(raw.get("exchange", "kraken")),
        history_exchange=str(raw.get("history_exchange") or raw.get("exchange", "kraken")),
        timeframe=timeframe,
        history_days=int(raw.get("history_days", 90)),
        live_bars=int(raw.get("live_bars", 400)),
        database=str(raw.get("database", "data/market.db")),
        paper_database=str(raw.get("paper_database", "data/paper.db")),
        start_equity=float(raw.get("start_equity", 10_000.0)),
        fee_pct=float(raw.get("fee_pct", 0.1)),
        risk=risk,
        strategy=StrategyParams(**(raw.get("strategy") or {})),
    )
