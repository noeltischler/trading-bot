import numpy as np
import pandas as pd

from tradingbot.backtest import BacktestParams
from tradingbot.optimize import best, combos, evaluate, resample
from tradingbot.strategy import StrategyParams


def test_resample_15m_to_1h():
    df = pd.DataFrame({
        "time": [0, 900, 1800, 2700, 3600],
        "open": [1, 2, 3, 4, 5], "high": [2, 5, 4, 5, 6],
        "low": [0.5, 1, 2, 3, 4], "close": [2, 3, 4, 5, 6], "volume": [1, 1, 1, 1, 1],
    })
    h = resample(df, 60)
    assert list(h.time) == [0, 3600]
    first = h.iloc[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (1, 5, 0.5, 5, 4)


def test_combos_skip_finer_timeframes_than_data():
    tfs = {c.timeframe for c in combos("1h")}
    assert tfs == {"1h", "4h"}


def test_evaluate_runs_and_ranks_by_training_only():
    rng = np.random.default_rng(0)
    n = 4000
    c = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, n)))
    o = np.r_[c[0], c[:-1]]
    df = pd.DataFrame({"time": np.arange(n) * 900, "open": o, "high": np.maximum(o, c) * 1.002,
                       "low": np.minimum(o, c) * 0.998, "close": c, "volume": 1.0})
    grid = {"timeframe": ["15m", "1h"], "strategy": [{"kind": "ema", "fast": 9, "slow": 21}, {"kind": "breakout", "lookback": 20}],
            "stop_pct": [1.0, 2.0], "trail_pct": [1.0]}
    results = evaluate({"A": df, "B": df}, "15m", BacktestParams(), StrategyParams(), grid=grid)
    assert len(results) == 8
    ranked = best(results, min_trades=0)
    assert [r.train_return for r in ranked] == sorted((r.train_return for r in ranked), reverse=True)
