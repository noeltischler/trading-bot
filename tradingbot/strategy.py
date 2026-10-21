"""Einstiegssignale: Trendfolge mit EMA-Kreuzung, Trendfilter und RSI-Bremse.

Long:  schnelle EMA kreuzt über die langsame, Kurs über der Trend-EMA, RSI nicht überkauft.
Short: spiegelverkehrt.
Das Signal gilt beim Schluss einer Kerze; gehandelt wird zur Eröffnung der nächsten.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from tradingbot.indicators import atr, ema, rsi

LONG, FLAT, SHORT = 1, 0, -1


@dataclass
class StrategyParams:
    fast: int = 20
    slow: int = 50
    trend: int = 200
    rsi_period: int = 14
    rsi_max_long: float = 70.0   # nicht kaufen, wenn schon überkauft
    rsi_min_short: float = 30.0  # nicht shorten, wenn schon überverkauft
    allow_short: bool = True


def add_indicators(df: pd.DataFrame, p: StrategyParams) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = ema(out["close"], p.fast)
    out["ema_slow"] = ema(out["close"], p.slow)
    out["ema_trend"] = ema(out["close"], p.trend)
    out["rsi"] = rsi(out["close"], p.rsi_period)
    out["atr"] = atr(out)
    return out


def signals(df: pd.DataFrame, p: StrategyParams | None = None) -> pd.Series:
    p = p or StrategyParams()
    d = add_indicators(df, p)
    above = d["ema_fast"] > d["ema_slow"]
    cross_up = above & ~above.shift(fill_value=False)
    cross_down = ~above & above.shift(fill_value=False)
    ready = d[["ema_fast", "ema_slow", "ema_trend", "rsi"]].notna().all(axis=1)

    long_ = ready & cross_up & (d["close"] > d["ema_trend"]) & (d["rsi"] < p.rsi_max_long)
    short = ready & cross_down & (d["close"] < d["ema_trend"]) & (d["rsi"] > p.rsi_min_short)

    sig = pd.Series(FLAT, index=df.index)
    sig[long_] = LONG
    if p.allow_short:
        sig[short] = SHORT
    return sig
