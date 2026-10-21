"""Einfacher Kerzen-Backtest mit Gebühren, Trailing-Stop und Kontorisiko.

Annahmen (bewusst vorsichtig):
- Einstieg zur Eröffnung der Kerze nach dem Signal.
- Liegt der Stop innerhalb einer Kerze, wird er dort ausgeführt; öffnet die Kerze
  schon jenseits des Stops (Kurslücke), zum schlechteren Eröffnungskurs.
- Der Stop wird erst nach Abschluss einer Kerze nachgezogen, nie innerhalb derselben.
- Ein Gegensignal schließt die offene Position und dreht sie.
- Gebühren/Spread werden pro Seite als Prozent vom Handelswert abgezogen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from tradingbot.risk import initial_stop, position_size, trail_stop
from tradingbot.strategy import FLAT, LONG, StrategyParams, signals


@dataclass
class BacktestParams:
    start_equity: float = 10_000.0
    risk_pct: float = 0.75
    stop_pct: float = 1.0
    trail_pct: float = 1.0
    fee_pct: float = 0.05  # pro Seite, inkl. Spread


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
        n = len(self.trades)
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        losses = [t.pnl for t in self.trades if t.pnl <= 0]
        eq = self.equity
        dd = (eq / eq.cummax() - 1).min() if len(eq) else 0.0
        return {
            "trades": n,
            "win_rate_pct": round(100 * len(wins) / n, 1) if n else 0.0,
            "return_pct": round(100 * (eq.iloc[-1] / self.params.start_equity - 1), 2) if len(eq) else 0.0,
            "max_drawdown_pct": round(100 * dd, 2),
            "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
            "avg_trade": round(sum(t.pnl for t in self.trades) / n, 2) if n else 0.0,
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

    equity = bp.start_equity
    fee = bp.fee_pct / 100
    trades: list[Trade] = []
    curve = []
    pos = None  # dict(side, entry, qty, stop, best, time)

    def close(i: int, price: float, reason: str) -> None:
        nonlocal equity, pos
        side, qty = pos["side"], pos["qty"]
        gross = (price - pos["entry"]) * qty * side
        cost = (pos["entry"] + price) * qty * fee
        pnl = gross - cost
        equity += pnl
        trades.append(Trade(side, int(pos["time"]), pos["entry"], int(t[i]), price, qty, pnl, reason))
        pos = None

    def open_(i: int, side: int) -> None:
        nonlocal pos
        entry = o[i]
        stop = initial_stop(entry, side, bp.stop_pct)
        qty = position_size(equity, bp.risk_pct, entry, stop)
        if qty > 0:
            pos = dict(side=side, entry=entry, qty=qty, stop=stop, best=entry, time=t[i])

    for i in range(1, len(df)):
        prev = sig[i - 1]
        # Signal der Vorkerze zur Eröffnung dieser Kerze umsetzen
        if prev != FLAT:
            if pos and pos["side"] != prev:
                close(i, o[i], "signal")
            if pos is None:
                open_(i, prev)

        if pos:
            side, stop = pos["side"], pos["stop"]
            if side == LONG and l[i] <= stop:
                close(i, min(o[i], stop), "stop")
            elif side != LONG and h[i] >= stop:
                close(i, max(o[i], stop), "stop")
            else:
                pos["best"] = max(pos["best"], h[i]) if side == LONG else min(pos["best"], l[i])
                pos["stop"] = trail_stop(stop, side, pos["best"], bp.trail_pct)

        open_pnl = (c[i] - pos["entry"]) * pos["qty"] * pos["side"] if pos else 0.0
        curve.append((t[i], equity + open_pnl))

    if pos:
        close(len(df) - 1, c[-1], "end")
        curve[-1] = (t[-1], equity)

    eq = pd.Series([v for _, v in curve], index=[k for k, _ in curve], dtype=float)
    return Result(trades, eq, bp)
