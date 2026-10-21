"""Eine offene Position mit Anfangs-Stop und nachgezogenem Stop-Loss.

Wird vom Backtest und vom Live-Übungskonto gleichermaßen genutzt, damit beide
exakt dieselben Regeln anwenden.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from tradingbot.risk import initial_stop, position_size, trail_stop
from tradingbot.strategy import LONG


@dataclass
class Position:
    coin: str
    side: int
    entry: float
    qty: float
    stop: float
    best: float
    opened_at: int

    @classmethod
    def open(cls, coin: str, side: int, price: float, equity: float,
             risk_pct: float, stop_pct: float, time: int) -> "Position | None":
        stop = initial_stop(price, side, stop_pct)
        qty = position_size(equity, risk_pct, price, stop)
        if qty <= 0:
            return None
        return cls(coin, side, price, qty, stop, price, time)

    def on_candle(self, open_: float, high: float, low: float, trail_pct: float) -> float | None:
        """Prüft eine abgeschlossene Kerze. Gibt den Ausführungskurs zurück, wenn
        der Stop getroffen wurde (bei Kurslücke den schlechteren Eröffnungskurs),
        sonst wird der Stop nachgezogen und None zurückgegeben."""
        if self.side == LONG and low <= self.stop:
            return min(open_, self.stop)
        if self.side != LONG and high >= self.stop:
            return max(open_, self.stop)
        self.best = max(self.best, high) if self.side == LONG else min(self.best, low)
        self.stop = trail_stop(self.stop, self.side, self.best, trail_pct)
        return None

    def pnl(self, price: float, fee_pct: float) -> float:
        gross = (price - self.entry) * self.qty * self.side
        cost = (self.entry + price) * self.qty * fee_pct / 100
        return gross - cost

    def to_dict(self) -> dict:
        return asdict(self)
