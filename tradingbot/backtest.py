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

from tradingbot.position import Position
from tradingbot.strategy import FLAT, StrategyParams, signals


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
        return summarize([t.pnl for t in self.trades], self.equity, self.params.start_equity)


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
    trades: list[Trade] = []
    curve = []
    pos: Position | None = None

    def close(i: int, price: float, reason: str) -> None:
        nonlocal equity, pos
        pnl = pos.pnl(price, bp.fee_pct)
        equity += pnl
        trades.append(Trade(pos.side, int(pos.opened_at), pos.entry, int(t[i]), price, pos.qty, pnl, reason))
        pos = None

    for i in range(1, len(df)):
        prev = sig[i - 1]
        # Signal der Vorkerze zur Eröffnung dieser Kerze umsetzen
        if prev != FLAT:
            if pos and pos.side != prev:
                close(i, o[i], "signal")
            if pos is None:
                pos = Position.open("", int(prev), o[i], equity, bp.risk_pct, bp.stop_pct, int(t[i]))

        if pos:
            hit = pos.on_candle(o[i], h[i], l[i], bp.trail_pct)
            if hit is not None:
                close(i, hit, "stop")

        open_pnl = (c[i] - pos.entry) * pos.qty * pos.side if pos else 0.0
        curve.append((t[i], equity + open_pnl))

    if pos:
        close(len(df) - 1, c[-1], "end")
        curve[-1] = (t[-1], equity)

    eq = pd.Series([v for _, v in curve], index=[k for k, _ in curve], dtype=float)
    return Result(trades, eq, bp)
