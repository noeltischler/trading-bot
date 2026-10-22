"""Einstiegssignale. Drei Strategie-Arten, wählbar über StrategyParams.kind:

- "ema":      Trendfolge. Long, wenn die schnelle EMA über die langsame kreuzt, der
              Kurs über der Trend-EMA liegt und der RSI nicht überkauft ist.
- "breakout": Ausbruch. Long, wenn der Schlusskurs das Hoch der letzten
              `lookback` Kerzen überschreitet und der Kurs über der Trend-EMA liegt.
- "meanrev":  Gegenbewegung. Long, wenn der RSI unter `rsi_low` fällt, während der
              Kurs über der Trend-EMA liegt (Rücksetzer im Aufwärtstrend).
- "wedge":    Keil- und Dreieck-Ausbruch. Durch die Hoch- und Tiefpunkte der letzten
              `lookback` Kerzen wird je eine Trendlinie gelegt. Laufen die Linien
              zusammen (Keil oder Dreieck), gilt: Long, wenn der Schlusskurs über die
              fallende obere Linie ausbricht (fallender Keil, absteigendes oder
              symmetrisches Dreieck); Short, wenn er unter die steigende untere Linie
              fällt (steigender Keil, aufsteigendes Dreieck). Ohne Trend-EMA-Filter.

Short jeweils spiegelverkehrt. Signale sind Ereignisse (nur auf der Kerze, auf der
die Bedingung neu eintritt). Das Signal gilt beim Schluss einer Kerze; gehandelt
wird zur Eröffnung der nächsten.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tradingbot.indicators import ema, rsi

LONG, FLAT, SHORT = 1, 0, -1
KINDS = ("ema", "breakout", "meanrev", "wedge")


@dataclass
class StrategyParams:
    kind: str = "ema"
    fast: int = 20
    slow: int = 50
    trend: int = 200
    lookback: int = 20           # breakout: Hoch/Tief der letzten N Kerzen; wedge: Länge des Musters
    pivot: int = 2               # wedge: Hoch-/Tiefpunkt = Extrem über pivot Kerzen links und rechts
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
        if self.kind == "wedge":
            return f"Keil {self.lookback}"
        return f"RSI {self.rsi_low:g}/{self.rsi_high:g}"


def _rising(cond: pd.Series) -> pd.Series:
    """True nur auf der Kerze, auf der cond von False zu True wechselt."""
    return cond & ~cond.shift(fill_value=False)


def _line(xs: list[int], ys: list[float]) -> tuple[float, float]:
    """Ausgleichsgerade y = a + b*x durch die Punkte (kleinste Quadrate)."""
    x, y = np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)
    xm, ym = x.mean(), y.mean()
    b = float(((x - xm) * (y - ym)).sum() / ((x - xm) ** 2).sum())
    return ym - b * xm, b


def wedge_lines(df: pd.DataFrame, lookback: int, pivot: int = 2) -> pd.DataFrame:
    """Obere und untere Trendlinie je Kerze, nur aus Wissen bis zu dieser Kerze.

    Ein Hochpunkt ist eine Kerze, deren Hoch das höchste von pivot Kerzen links
    und rechts ist. Er steht also erst pivot Kerzen später fest und wird erst ab
    dann benutzt (kein Blick in die Zukunft). Pro Kerze zählen die Hoch- und
    Tiefpunkte der letzten lookback Kerzen, mindestens je zwei.
    Spalten: upper, lower (Linienwert auf dieser Kerze), upper_slope, lower_slope.
    """
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    n, win = len(df), 2 * pivot + 1
    is_high = df["high"].rolling(win, center=True).max().to_numpy() == high
    is_low = df["low"].rolling(win, center=True).min().to_numpy() == low
    out = np.full((n, 4), np.nan)
    highs: list[int] = []
    lows: list[int] = []
    last_key, lines = None, None
    for i in range(n):
        j = i - pivot  # Kandidat, der mit Kerze i bestätigt wird
        if j >= pivot:
            if is_high[j]:
                highs.append(j)
            if is_low[j]:
                lows.append(j)
        while highs and highs[0] <= i - lookback:
            highs.pop(0)
        while lows and lows[0] <= i - lookback:
            lows.pop(0)
        hs, ls = highs, lows
        if len(hs) < 2 or len(ls) < 2:
            continue
        key = (hs[0], hs[-1], len(hs), ls[0], ls[-1], len(ls))
        if key != last_key:  # Linien ändern sich nur mit neuen oder wegfallenden Punkten
            last_key, lines = key, (_line(hs, high[hs]), _line(ls, low[ls]))
        (au, bu), (al, bl) = lines
        out[i] = (au + bu * i, al + bl * i, bu, bl)
    return pd.DataFrame(out, columns=["upper", "lower", "upper_slope", "lower_slope"], index=df.index)


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
    elif p.kind == "wedge":
        w = wedge_lines(df, p.lookback, p.pivot)
        converging = (w.upper_slope < w.lower_slope) & (w.upper > w.lower)
        long_ = converging & (w.upper_slope < 0) & _rising(close > w.upper)
        short = converging & (w.lower_slope > 0) & _rising(close < w.lower)
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
