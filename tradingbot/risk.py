"""Risikoregeln: Positionsgröße nach Kontorisiko und nachgezogener Stop-Loss."""

from __future__ import annotations

from tradingbot.strategy import LONG


def initial_stop(entry: float, side: int, stop_pct: float) -> float:
    """Stop-Loss stop_pct Prozent vom Einstieg entfernt."""
    dist = entry * stop_pct / 100
    return entry - dist if side == LONG else entry + dist


def position_size(equity: float, risk_pct: float, entry: float, stop: float) -> float:
    """Menge so, dass ein Treffer des Stops höchstens risk_pct Prozent des Kontos kostet."""
    per_unit = abs(entry - stop)
    if per_unit <= 0 or equity <= 0:
        return 0.0
    return equity * risk_pct / 100 / per_unit


def trail_stop(stop: float, side: int, best_price: float, trail_pct: float) -> float:
    """Zieht den Stop hinter dem besten Kurs seit Einstieg her, aber nie zurück."""
    if side == LONG:
        return max(stop, best_price * (1 - trail_pct / 100))
    return min(stop, best_price * (1 + trail_pct / 100))
