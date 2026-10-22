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
- "fvg":      Fair Value Gap (Smart Money Concepts). Eine Lücke entsteht, wenn das
              Tief einer Kerze über dem Hoch der vorletzten liegt (mindestens
              fvg_min_atr × ATR groß). Long, wenn der Kurs später in die Lücke
              zurückläuft und über ihrer Unterkante schließt (höchstens lookback
              Kerzen nach der Lücke). Mit Trend-EMA-Filter, wenn use_trend.
- "supertrend": Trendfolge mit dem Supertrend-Indikator (ATR st_period, Faktor
              st_factor). Long, wenn er auf Aufwärtstrend dreht.
- "fib":      Fibonacci-Rücksetzer („Golden Pocket“). Im Aufwärtstrend vom Tief zum
              Hoch der letzten lookback Kerzen: Long, wenn der Kurs bis unter die
              61,8-%-Marke zurückfällt, aber über der 78,6-%-Marke schließt.
- "sweep":    Liquiditäts-Sweep („Turtle Soup“). Long, wenn eine Kerze unter das
              Tief der letzten lookback Kerzen sticht, aber wieder darüber schließt
              (Stopps abgeholt, Ausbruch gescheitert). Mit Trend-EMA-Filter, wenn use_trend.

Mehrere Strategien lassen sich kombinieren (Liste statt einzelner Parameter): Es zählt
das Signal der ersten Strategie, die auf dieser Kerze eines hat.

Short jeweils spiegelverkehrt. Signale sind Ereignisse (nur auf der Kerze, auf der
die Bedingung neu eintritt). Das Signal gilt beim Schluss einer Kerze; gehandelt
wird zur Eröffnung der nächsten.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from tradingbot.indicators import atr, ema, rsi, supertrend

LONG, FLAT, SHORT = 1, 0, -1
KINDS = ("ema", "breakout", "meanrev", "wedge", "fvg", "supertrend", "fib", "sweep")


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
    use_trend: bool = True       # fvg, fib, sweep: nur mit dem Trend (Kurs über/unter Trend-EMA)
    atr_period: int = 14
    fvg_min_atr: float = 0.5     # fvg: Mindestgröße der Lücke in ATR
    st_period: int = 10          # supertrend: ATR-Länge
    st_factor: float = 3.0       # supertrend: Bandabstand in ATR

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
        if self.kind == "fvg":
            return f"FVG {self.fvg_min_atr:g}"
        if self.kind == "supertrend":
            return f"Supertrend {self.st_factor:g}"
        if self.kind == "fib":
            return f"Fibonacci {self.lookback}"
        if self.kind == "sweep":
            return f"Sweep {self.lookback}"
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


def fvg_retests(df: pd.DataFrame, min_atr: float, max_age: int, atr_period: int = 14) -> tuple[np.ndarray, np.ndarray]:
    """Kerzen, auf denen der Kurs in eine offene Fair Value Gap zurückläuft und hält.

    Bullische Lücke auf Kerze i: Tief[i] > Hoch[i-2]; Zone = Hoch[i-2] bis Tief[i].
    Sie gilt ab der nächsten Kerze, höchstens max_age Kerzen lang. Long-Treffer:
    Tief berührt die Oberkante, Schluss bleibt über der Unterkante. Schließt der Kurs
    unter der Zone, ist sie verbraucht. Jede Zone löst höchstens einmal aus.
    Bärisch spiegelverkehrt. Gibt zwei Bool-Arrays (long, short) zurück.
    """
    high, low, close = (df[k].to_numpy() for k in ("high", "low", "close"))
    a = atr(df, atr_period).to_numpy()
    n = len(df)
    long_, short = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    bulls: list[tuple[int, float, float]] = []  # (Entstehung, unten, oben)
    bears: list[tuple[int, float, float]] = []
    for i in range(n):
        bulls = [z for z in bulls if i - z[0] <= max_age]
        bears = [z for z in bears if i - z[0] <= max_age]
        keep = []
        for z in bulls:
            _, bottom, top = z
            if close[i] < bottom:
                continue  # durchbrochen
            if low[i] <= top:
                long_[i] = True  # Rücklauf in die Lücke, Schluss darüber
                continue
            keep.append(z)
        bulls = keep
        keep = []
        for z in bears:
            _, bottom, top = z
            if close[i] > top:
                continue
            if high[i] >= bottom:
                short[i] = True
                continue
            keep.append(z)
        bears = keep
        if i >= 2 and not np.isnan(a[i]):
            if low[i] - high[i - 2] >= min_atr * a[i] and low[i] > high[i - 2]:
                bulls.append((i, high[i - 2], low[i]))
            if low[i - 2] - high[i] >= min_atr * a[i] and high[i] < low[i - 2]:
                bears.append((i, high[i], low[i - 2]))
    return long_, short


def fib_pullbacks(df: pd.DataFrame, lookback: int) -> tuple[np.ndarray, np.ndarray]:
    """Rücksetzer in den Bereich 61,8 bis 78,6 % der letzten Bewegung.

    Bewegung = Tief und Hoch der lookback Kerzen VOR der aktuellen. Long: das Tief
    lag vor dem Hoch (Aufwärtsbewegung), die aktuelle Kerze sticht unter die
    61,8-%-Marke und schließt über der 78,6-%-Marke. Short spiegelverkehrt.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    high, low, close = (df[k].to_numpy() for k in ("high", "low", "close"))
    n = len(df)
    long_, short = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    if n <= lookback:
        return long_, short
    hw, lw = sliding_window_view(high, lookback), sliding_window_view(low, lookback)
    # Fenster k umfasst Kerzen k .. k+lookback-1 und gilt für Kerze i = k+lookback
    hi_pos, lo_pos = hw.argmax(axis=1), lw.argmin(axis=1)
    hi, lo = hw.max(axis=1), lw.min(axis=1)
    i = np.arange(lookback, n)
    hi, lo, hi_pos, lo_pos = hi[: len(i)], lo[: len(i)], hi_pos[: len(i)], lo_pos[: len(i)]
    rng = hi - lo
    up_move, down_move = lo_pos < hi_pos, hi_pos < lo_pos
    l618, l786 = hi - 0.618 * rng, hi - 0.786 * rng
    long_[i] = up_move & (low[i] <= l618) & (close[i] >= l786) & (close[i] <= hi)
    s618, s786 = lo + 0.618 * rng, lo + 0.786 * rng
    short[i] = down_move & (high[i] >= s618) & (close[i] <= s786) & (close[i] >= lo)
    return long_, short


def label(p: "StrategyParams | tuple | list") -> str:
    """Name einer Strategie oder einer Kombination, z. B. 'Keil 80 + Sweep 50'."""
    return " + ".join(x.label() for x in p) if isinstance(p, (list, tuple)) else p.label()


def signals(df: pd.DataFrame, p: "StrategyParams | tuple | list | None" = None) -> pd.Series:
    if isinstance(p, (list, tuple)):
        out = pd.Series(FLAT, index=df.index)
        for one in reversed(p):  # die erste Strategie in der Liste hat Vorrang
            sig = signals(df, one)
            out = sig.where(sig != FLAT, out)
        return out
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
    elif p.kind == "fvg":
        lo, sh = fvg_retests(df, p.fvg_min_atr, p.lookback, p.atr_period)
        long_, short = pd.Series(lo, index=df.index), pd.Series(sh, index=df.index)
        if p.use_trend:
            long_, short = long_ & up, short & down
    elif p.kind == "supertrend":
        st = supertrend(df, p.st_period, p.st_factor)
        long_ = _rising(st == 1) & (st.shift() == -1)
        short = _rising(st == -1) & (st.shift() == 1)
    elif p.kind == "fib":
        lo, sh = fib_pullbacks(df, p.lookback)
        long_, short = _rising(pd.Series(lo, index=df.index)), _rising(pd.Series(sh, index=df.index))
        if p.use_trend:
            long_, short = long_ & up, short & down
    elif p.kind == "sweep":
        prev_high = df["high"].rolling(p.lookback).max().shift()
        prev_low = df["low"].rolling(p.lookback).min().shift()
        long_ = (df["low"] < prev_low) & (close > prev_low)
        short = (df["high"] > prev_high) & (close < prev_high)
        if p.use_trend:
            long_, short = long_ & up, short & down
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
