"""Technische Indikatoren auf Basis von pandas."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def supertrend(df: pd.DataFrame, period: int = 10, factor: float = 3.0) -> pd.Series:
    """Supertrend-Richtung je Kerze: 1 = Aufwärtstrend, -1 = Abwärtstrend, 0 = noch unbekannt.

    Bänder im Abstand factor × ATR um die Kerzenmitte. Das untere Band steigt nur,
    das obere fällt nur. Schließt der Kurs jenseits des aktiven Bandes, dreht der Trend.
    """
    a = atr(df, period).to_numpy()
    mid = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    out = np.zeros(n, dtype=int)
    upper = lower = np.nan
    trend = 0
    for i in range(n):
        if np.isnan(a[i]):
            continue
        up, lo = mid[i] + factor * a[i], mid[i] - factor * a[i]
        if trend == 0:
            upper, lower, trend = up, lo, (1 if close[i] > mid[i] else -1)
        else:
            upper = up if (up < upper or close[i - 1] > upper) else upper
            lower = lo if (lo > lower or close[i - 1] < lower) else lower
            if trend == 1 and close[i] < lower:
                trend = -1
            elif trend == -1 and close[i] > upper:
                trend = 1
        out[i] = trend
    return pd.Series(out, index=df.index)


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    """MACD-Histogramm: (EMA fast − EMA slow) minus dessen Signal-EMA. Über 0 = Schwung nach oben."""
    line = ema(close, fast) - ema(close, slow)
    return line - ema(line, signal)


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ADX nach Wilder: Trendstärke von 0 bis 100 (über 20 bis 25 = deutlicher Trend)."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = atr(df, period)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / tr
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
