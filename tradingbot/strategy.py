"""Einstiegssignale. Drei Strategie-Arten, wählbar über StrategyParams.kind:

- "ema":      Trendfolge. Long, wenn die schnelle EMA über die langsame kreuzt, der
              Kurs über der Trend-EMA liegt und der RSI nicht überkauft ist.
- "breakout": Ausbruch. Long, wenn der Schlusskurs das Hoch der letzten
              `lookback` Kerzen überschreitet und der Kurs über der Trend-EMA liegt.
- "meanrev":  Gegenbewegung. Long, wenn der RSI unter `rsi_low` fällt, während der
              Kurs über der Trend-EMA liegt (Rücksetzer im Aufwärtstrend).

Short jeweils spiegelverkehrt. Signale sind Ereignisse (nur auf der Kerze, auf der
die Bedingung neu eintritt). Das Signal gilt beim Schluss einer Kerze; gehandelt
wird zur Eröffnung der nächsten.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tradingbot.indicators import ema, rsi

LONG, FLAT, SHORT = 1, 0, -1
KINDS = ("ema", "breakout", "meanrev")


@dataclass
class StrategyParams:
    kind: str = "ema"
    fast: int = 20
    slow: int = 50
    trend: int = 200
    lookback: int = 20           # breakout: Hoch/Tief der letzten N Kerzen
    rsi_period: int = 14
    rsi_max_long: float = 70.0   # ema: nicht kaufen, wenn schon überkauft
    rsi_min_short: float = 30.0  # ema: nicht shorten, wenn schon überverkauft
    rsi_low: float = 30.0        # meanrev: überverkauft
    rsi_high: float = 70.0       # meanrev: überkauft
    allow_short: bool = True

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"Unbekannte Strategie '{self.kind}'. Erlaubt: {KINDS}")

    def label(self) -> str:
        if self.kind == "ema":
            return f"EMA {self.fast}/{self.slow}"
        if self.kind == "breakout":
            return f"Ausbruch {self.lookback}"
        return f"RSI {self.rsi_low:g}/{self.rsi_high:g}"


def _rising(cond: pd.Series) -> pd.Series:
    """True nur auf der Kerze, auf der cond von False zu True wechselt."""
    return cond & ~cond.shift(fill_value=False)


def signals(df: pd.DataFrame, p: StrategyParams | None = None) -> pd.Series:
    p = p or StrategyParams()
    close = df["close"]
    trend = ema(close, p.trend)
    up, down = close > trend, close < trend

    if p.kind == "ema":
        fast, slow, r = ema(close, p.fast), ema(close, p.slow), rsi(close, p.rsi_period)
        ready = fast.notna() & slow.notna() & trend.notna() & r.notna()
        above = fast > slow
        long_ = ready & _rising(above) & up & (r < p.rsi_max_long)
        short = ready & _rising(~above & fast.notna() & slow.notna()) & down & (r > p.rsi_min_short)
    elif p.kind == "breakout":
        prev_high = df["high"].rolling(p.lookback).max().shift()
        prev_low = df["low"].rolling(p.lookback).min().shift()
        ready = prev_high.notna() & trend.notna()
        long_ = ready & _rising(close > prev_high) & up
        short = ready & _rising(close < prev_low) & down
    else:  # meanrev
        r = rsi(close, p.rsi_period)
        ready = r.notna() & trend.notna()
        long_ = ready & _rising(r < p.rsi_low) & up
        short = ready & _rising(r > p.rsi_high) & down

    sig = pd.Series(FLAT, index=df.index)
    sig[long_.fillna(False)] = LONG
    if p.allow_short:
        sig[short.fillna(False)] = SHORT
    return sig
