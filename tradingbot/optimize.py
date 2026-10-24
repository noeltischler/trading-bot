"""Parametersuche mit Prüfung auf ungesehenen Daten (Walk-Forward light).

Die Daten jedes Coins werden zeitlich geteilt: Auf dem ersten Teil (Training)
wird die beste Kombination gesucht, auf dem letzten Teil (Test) wird nur
geprüft. Eine Kombination taugt erst, wenn sie auch im Test über alle Coins
hinweg Gewinn macht. Sonst hat sie sich nur die Vergangenheit gemerkt.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace

import pandas as pd

from tradingbot.backtest import BacktestParams, run
from tradingbot.sentiment import with_fear_greed
from tradingbot.strategy import StrategyParams, with_btc

TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Fasst Kerzen zu einem gröberen Zeitrahmen zusammen (z. B. 15m → 1h)."""
    seconds = minutes * 60
    bucket = df["time"] // seconds * seconds
    out = df.groupby(bucket).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    )
    out.index.name = "time"
    return out.reset_index()


@dataclass(frozen=True)
class Combo:
    timeframe: str
    strategy: tuple  # Felder für StrategyParams, z. B. (("kind", "ema"), ("fast", 9), ("slow", 21))
    stop_pct: float
    trail_pct: float
    exits: tuple = ()  # zusätzliche Ausstiegsregeln für BacktestParams, z. B. (("take_profit_r", 2.0),)

    def params(self, base: StrategyParams) -> StrategyParams:
        return replace(base, **dict(self.strategy))

    def label(self) -> str:
        name = StrategyParams(**dict(self.strategy)).label()
        ex = dict(self.exits)
        stop = f"Stop {ex['stop_atr']:g} ATR" if ex.get("stop_atr") else f"Stop {self.stop_pct:g} %"
        trail = f"Trail {ex['trail_atr']:g} ATR" if ex.get("trail_atr") else f"Trail {self.trail_pct:g} %"
        target = f"  Ziel {ex['take_profit_r']:g}R" if ex.get("take_profit_r") else ""
        return f"{self.timeframe:>4} {name:<15} {stop}  {trail}{target}"


@dataclass
class ComboResult:
    combo: Combo
    train_return: float   # Summe der Renditen über alle Coins, Trainingsteil
    test_return: float    # dasselbe auf dem Testteil
    test_pf: float | None  # Profitfaktor aller Test-Trades zusammen
    test_trades: int
    coins_positive: int    # wie viele Coins im Test im Plus sind


GRID = {
    "timeframe": ["15m", "1h", "4h"],
    "strategy": [
        {"kind": "ema", "fast": 9, "slow": 21},
        {"kind": "ema", "fast": 20, "slow": 50},
        {"kind": "breakout", "lookback": 20},
        {"kind": "breakout", "lookback": 55},
        {"kind": "meanrev", "rsi_low": 30, "rsi_high": 70},
        {"kind": "meanrev", "rsi_low": 25, "rsi_high": 75},
        {"kind": "wedge", "lookback": 40},
        {"kind": "wedge", "lookback": 80},
        {"kind": "fvg", "lookback": 20, "fvg_min_atr": 0.5},
        {"kind": "fvg", "lookback": 20, "fvg_min_atr": 1.0},
        {"kind": "supertrend", "st_factor": 2.0},
        {"kind": "supertrend", "st_factor": 3.0},
        {"kind": "fib", "lookback": 50},
        {"kind": "sweep", "lookback": 20},
        {"kind": "sweep", "lookback": 50},
        {"kind": "meanrev", "rsi_period": 2, "rsi_low": 10, "rsi_high": 90},
        {"kind": "squeeze", "lookback": 120},
    ],
    "stop_pct": [1.0, 2.0, 3.0],
    "trail_pct": [1.0, 2.0, 3.0],
    "exits": [{}],
}

# Mit 'optimize --exits': zusätzlich Gewinnziel und ATR-Stops ausprobieren.
# ATR-Stops ersetzen Stop/Trail in %, deshalb dort nur eine %-Variante.
EXIT_VARIANTS = [
    {},
    {"take_profit_r": 2.0},
    {"take_profit_r": 3.0},
    {"stop_atr": 2.0, "trail_atr": 3.0},
    {"stop_atr": 2.0, "trail_atr": 3.0, "take_profit_r": 3.0},
]


def combos(base_timeframe: str, grid: dict = GRID) -> list[Combo]:
    base = TIMEFRAME_MINUTES[base_timeframe]
    tfs = [tf for tf in grid["timeframe"] if TIMEFRAME_MINUTES[tf] >= base]
    out = []
    for tf, strat, stop, trail, ex in itertools.product(
        tfs, grid["strategy"], grid["stop_pct"], grid["trail_pct"], grid.get("exits", [{}])
    ):
        if ex.get("stop_atr") and (stop, trail) != (grid["stop_pct"][0], grid["trail_pct"][0]):
            continue  # Stop/Trail in % spielen hier keine Rolle
        out.append(Combo(tf, tuple(sorted(strat.items())), stop, trail, tuple(sorted(ex.items()))))
    return out


def _split(df: pd.DataFrame, test_share: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    cut = int(len(df) * (1 - test_share))
    return df.iloc[:cut].reset_index(drop=True), df.iloc[cut:].reset_index(drop=True)


def evaluate(
    data: dict[str, pd.DataFrame],
    base_timeframe: str,
    base_bp: BacktestParams,
    base_sp: StrategyParams,
    test_share: float = 0.33,
    grid: dict = GRID,
    fear_greed: pd.DataFrame | None = None,
) -> list[ComboResult]:
    """fear_greed: Verlauf des Fear & Greed Index (für fg_mode), sonst None."""
    base_minutes = TIMEFRAME_MINUTES[base_timeframe]
    frames: dict[str, dict[str, pd.DataFrame]] = {}
    for tf in {c.timeframe for c in combos(base_timeframe, grid)}:
        m = TIMEFRAME_MINUTES[tf]
        frames[tf] = {coin: df if m == base_minutes else resample(df, m) for coin, df in data.items()}
        if "BTC" in frames[tf]:  # für btc_filter
            btc = frames[tf]["BTC"]
            frames[tf] = {coin: with_btc(df, btc) for coin, df in frames[tf].items()}
        if fear_greed is not None:
            frames[tf] = {coin: with_fear_greed(df, fear_greed) for coin, df in frames[tf].items()}

    from tradingbot.strategy import signals

    # Signale hängen nur von Zeitrahmen und Strategie ab, nicht von Stop/Ausstieg:
    # einmal berechnen, für alle Stop-Varianten wiederverwenden.
    parts: dict[tuple, tuple] = {}
    sig_cache: dict[tuple, pd.Series] = {}

    def split(tf: str, coin: str):
        if (tf, coin) not in parts:
            parts[(tf, coin)] = _split(frames[tf][coin], test_share)
        return parts[(tf, coin)]

    def sig_for(combo: Combo, coin: str, which: int, df: pd.DataFrame, sp: StrategyParams) -> pd.Series:
        key = (combo.timeframe, combo.strategy, coin, which)
        if key not in sig_cache:
            sig_cache[key] = signals(df, sp)
        return sig_cache[key]

    results = []
    for combo in combos(base_timeframe, grid):
        exits = {"stop_atr": 0.0, "trail_atr": 0.0, "take_profit_r": 0.0, **dict(combo.exits)}
        bp = replace(base_bp, stop_pct=combo.stop_pct, trail_pct=combo.trail_pct, **exits)
        sp = combo.params(base_sp)
        train_ret = test_ret = 0.0
        wins = losses = 0.0
        trades = positive = 0
        for coin in frames[combo.timeframe]:
            train, test = split(combo.timeframe, coin)
            if len(train) < sp.trend + 50 or len(test) < sp.trend + 50:
                continue
            train_ret += run(train, bp, sp, sig_for(combo, coin, 0, train, sp)).summary()["return_pct"]
            res = run(test, bp, sp, sig_for(combo, coin, 1, test, sp))
            s = res.summary()
            test_ret += s["return_pct"]
            trades += s["trades"]
            positive += s["return_pct"] > 0
            wins += sum(t.pnl for t in res.trades if t.pnl > 0)
            losses += -sum(t.pnl for t in res.trades if t.pnl <= 0)
        pf = round(wins / losses, 2) if losses > 0 else None
        results.append(ComboResult(combo, round(train_ret, 2), round(test_ret, 2), pf, trades, positive))
    return results


def best(results: list[ComboResult], top: int = 10, min_trades: int = 15) -> list[ComboResult]:
    """Rangliste nach Trainingsergebnis. Der Test wird nur angezeigt, nie zum
    Auswählen benutzt, damit er ehrlich bleibt."""
    usable = [r for r in results if r.test_trades >= min_trades]
    return sorted(usable, key=lambda r: r.train_return, reverse=True)[:top]
