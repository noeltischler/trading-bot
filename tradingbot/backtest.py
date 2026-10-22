"""Einfacher Kerzen-Backtest mit Gebühren, Trailing-Stop und Kontorisiko.

Annahmen (bewusst vorsichtig):
- Einstieg zur Eröffnung der Kerze nach dem Signal.
- Liegt der Stop innerhalb einer Kerze, wird er dort ausgeführt; öffnet die Kerze
  schon jenseits des Stops (Kurslücke), zum schlechteren Eröffnungskurs.
- Der Stop wird erst nach Abschluss einer Kerze nachgezogen, nie innerhalb derselben.
- Ein Gegensignal schließt die offene Position und dreht sie.
- Gebühren/Spread werden pro Seite als Prozent vom Handelswert abgezogen.
- Sicherheitsregeln wie live: Tagesverlust-Grenze, Risiko-Bremse nach Verlusten,
  Obergrenze für den Positionswert, Stop und Trailing optional nach ATR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tradingbot.indicators import atr
from tradingbot.position import Position
from tradingbot.strategy import FLAT, StrategyParams, signals


@dataclass
class BacktestParams:
    start_equity: float = 10_000.0
    risk_pct: float = 0.75
    stop_pct: float = 1.0
    trail_pct: float = 1.0
    fee_pct: float = 0.05  # pro Seite, inkl. Spread
    stop_atr: float = 0.0          # >0: Stop-Abstand = stop_atr × ATR statt stop_pct
    trail_atr: float = 0.0         # >0: Trailing-Abstand = trail_atr × ATR statt trail_pct
    take_profit_r: float = 0.0     # >0: Gewinnziel = so viel mal der Stop-Abstand
    max_position_pct: float = 0.0  # >0: Positionswert höchstens so viel % vom Konto
    daily_loss_limit_pct: float = 0.0  # >0: nach so viel % Verlust am Tag keine neuen Trades
    drawdown_brake_pct: float = 0.0    # >0: Risiko halbieren, solange Konto so viel % unter Höchststand


def entry_risk(bp, equity: float, peak: float, day_start: float) -> float:
    """Risiko für einen neuen Trade in % (0 = heute nicht mehr handeln)."""
    if bp.daily_loss_limit_pct > 0 and equity <= day_start * (1 - bp.daily_loss_limit_pct / 100):
        return 0.0
    if bp.drawdown_brake_pct > 0 and equity < peak * (1 - bp.drawdown_brake_pct / 100):
        return bp.risk_pct / 2
    return bp.risk_pct


def atr_pcts(bp, atr_value: float, price: float) -> tuple[float, float]:
    """Stop- und Trailing-Abstand in % für einen neuen Trade. Trailing 0 heißt:
    der Position keinen eigenen Abstand geben, sondern trail_pct der Config nutzen."""
    stop, trail = bp.stop_pct, 0.0
    if atr_value == atr_value and atr_value > 0:  # nicht NaN
        if bp.stop_atr > 0:
            stop = bp.stop_atr * atr_value / price * 100
        if bp.trail_atr > 0:
            trail = bp.trail_atr * atr_value / price * 100
    return stop, trail


@dataclass
class Trade:
    side: int
    entry_time: int
    entry: float
    exit_time: int
    exit: float
    qty: float
    pnl: float
    reason: str


@dataclass
class Result:
    trades: list[Trade]
    equity: pd.Series
    params: BacktestParams = field(repr=False)

    def summary(self) -> dict:
        return summarize([t.pnl for t in self.trades], self.equity, self.params.start_equity)


def kelly_pct(pnls: list[float]) -> float | None:
    """Kelly-Kriterium: f = W - (1 - W) / R mit Trefferquote W und Verhältnis
    R = Ø Gewinn / Ø Verlust. Gibt den Anteil in % zurück, None bei zu wenig Daten.
    Negativ heißt: Die Strategie hat keinen Vorteil."""
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p <= 0]
    if len(pnls) < 10 or not wins or not losses or sum(losses) == 0:
        return None
    w = len(wins) / len(pnls)
    r = (sum(wins) / len(wins)) / (sum(losses) / len(losses))
    return round(100 * (w - (1 - w) / r), 1)


def summarize(pnls: list[float], equity: pd.Series, start_equity: float) -> dict:
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    dd = (equity / equity.cummax() - 1).min() if len(equity) else 0.0
    return {
        "trades": n,
        "win_rate_pct": round(100 * len(wins) / n, 1) if n else 0.0,
        "return_pct": round(100 * (equity.iloc[-1] / start_equity - 1), 2) if len(equity) else 0.0,
        "max_drawdown_pct": round(100 * dd, 2),
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "avg_trade": round(sum(pnls) / n, 2) if n else 0.0,
        "kelly_pct": kelly_pct(pnls),
    }


def run(
    df: pd.DataFrame,
    bp: BacktestParams | None = None,
    sp: StrategyParams | None = None,
    sig: pd.Series | None = None,
) -> Result:
    """sig: optional fertige Signale (1/0/-1) statt der Standardstrategie."""
    bp = bp or BacktestParams()
    df = df.reset_index(drop=True)
    sig = (signals(df, sp) if sig is None else sig).to_numpy()
    o, h, l, c, t = (df[k].to_numpy() for k in ("open", "high", "low", "close", "time"))
    a = atr(df).to_numpy()

    equity = peak = day_start = bp.start_equity
    day = None
    trades: list[Trade] = []
    curve = []
    pos: Position | None = None

    def close(i: int, price: float, reason: str) -> None:
        nonlocal equity, pos, peak
        pnl = pos.pnl(price, bp.fee_pct)
        equity += pnl
        peak = max(peak, equity)
        trades.append(Trade(pos.side, int(pos.opened_at), pos.entry, int(t[i]), price, pos.qty, pnl, reason))
        pos = None

    for i in range(1, len(df)):
        if t[i] // 86400 != day:
            day, day_start = t[i] // 86400, equity
        prev = sig[i - 1]
        # Signal der Vorkerze zur Eröffnung dieser Kerze umsetzen
        if prev != FLAT:
            if pos and pos.side != prev:
                close(i, o[i], "signal")
            risk = entry_risk(bp, equity, peak, day_start)
            if pos is None and risk > 0:
                stop_pct, trail_pct = atr_pcts(bp, a[i - 1], o[i])
                pos = Position.open("", int(prev), o[i], equity, risk, stop_pct, int(t[i]),
                                    bp.take_profit_r, trail_pct, bp.max_position_pct)

        if pos:
            hit = pos.on_candle(o[i], h[i], l[i], bp.trail_pct)
            if hit is not None:
                close(i, hit, pos.hit_reason(hit))

        open_pnl = (c[i] - pos.entry) * pos.qty * pos.side if pos else 0.0
        curve.append((t[i], equity + open_pnl))

    if pos:
        close(len(df) - 1, c[-1], "end")
        curve[-1] = (t[-1], equity)

    eq = pd.Series([v for _, v in curve], index=[k for k, _ in curve], dtype=float)
    return Result(trades, eq, bp)
