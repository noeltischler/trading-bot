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
    target: float = 0.0      # Gewinnziel (0 = keins)
    trail_pct: float = 0.0   # eigener Trailing-Abstand dieser Position (0 = Wert aus der Config)

    @classmethod
    def open(cls, coin: str, side: int, price: float, equity: float,
             risk_pct: float, stop_pct: float, time: int,
             take_profit_r: float = 0.0, trail_pct: float = 0.0,
             max_position_pct: float = 0.0) -> "Position | None":
        """take_profit_r: Gewinnziel als Vielfaches des Stop-Abstands (2 = doppelt so
        weit wie der Stop). max_position_pct: Positionswert höchstens so viel Prozent
        vom Konto, auch wenn der Stop sehr eng ist (0 = keine Grenze)."""
        stop = initial_stop(price, side, stop_pct)
        qty = position_size(equity, risk_pct, price, stop)
        if max_position_pct > 0:
            qty = min(qty, equity * max_position_pct / 100 / price)
        if qty <= 0:
            return None
        target = price + side * take_profit_r * abs(price - stop) if take_profit_r > 0 else 0.0
        return cls(coin, side, price, qty, stop, price, time, target, trail_pct)

    def on_candle(self, open_: float, high: float, low: float, trail_pct: float) -> float | None:
        """Prüft eine abgeschlossene Kerze. Gibt den Ausführungskurs zurück, wenn
        der Stop getroffen wurde (bei Kurslücke den schlechteren Eröffnungskurs)
        oder das Gewinnziel. Sonst wird der Stop nachgezogen und None zurückgegeben.
        Liegen Stop und Ziel in derselben Kerze, zählt vorsichtshalber der Stop."""
        if self.side == LONG and low <= self.stop:
            return min(open_, self.stop)
        if self.side != LONG and high >= self.stop:
            return max(open_, self.stop)
        if self.target:
            if self.side == LONG and high >= self.target:
                return max(open_, self.target)
            if self.side != LONG and low <= self.target:
                return min(open_, self.target)
        self.best = max(self.best, high) if self.side == LONG else min(self.best, low)
        self.stop = trail_stop(self.stop, self.side, self.best, self.trail_pct or trail_pct)
        return None

    def hit_reason(self, price: float) -> str:
        """'target', wenn ein Ausstieg bei price das Gewinnziel war, sonst 'stop'."""
        if self.target and (price - self.entry) * self.side > 0 and (price - self.target) * self.side >= 0:
            return "target"
        return "stop"

    def pnl(self, price: float, fee_pct: float) -> float:
        gross = (price - self.entry) * self.qty * self.side
        cost = (self.entry + price) * self.qty * fee_pct / 100
        return gross - cost

    def to_dict(self) -> dict:
        return asdict(self)
