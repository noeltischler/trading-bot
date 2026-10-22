import numpy as np
import pandas as pd
import pytest

from tradingbot.backtest import BacktestParams, entry_risk, kelly_pct, run
from tradingbot.indicators import supertrend
from tradingbot.live import account_marks
from tradingbot.paper import PaperAccount
from tradingbot.position import Position
from tradingbot.strategy import LONG, SHORT, StrategyParams, signals


def _ohlc(rows):
    o, h, l, c = (np.asarray(x, dtype=float) for x in zip(*rows))
    return pd.DataFrame({"time": np.arange(len(c)) * 3600, "open": o, "high": h, "low": l, "close": c, "volume": 1.0})


def _random_frame(n=600, seed=1):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"time": np.arange(n) * 3600, "open": o, "high": np.maximum(o, c) * 1.004,
                         "low": np.minimum(o, c) * 0.996, "close": c, "volume": 1.0})


def _calm(n, price=100.0):
    return [(price, price + 0.5, price - 0.5, price)] * n


def test_fvg_long_on_retest_of_bullish_gap():
    rows = _calm(30)
    rows += [(100, 101, 99.5, 101), (101, 106, 101, 106), (106, 108, 104, 107)]  # Lücke 101..104
    rows += [(107, 108, 106, 107.5), (107.5, 107.6, 103, 105)]  # Rücklauf in die Lücke, Schluss darüber
    sp = StrategyParams(kind="fvg", lookback=10, fvg_min_atr=0.5, use_trend=False)
    sig = signals(_ohlc(rows), sp)
    assert sig.iloc[-1] == LONG
    assert (sig == SHORT).sum() == 0


def test_fvg_ignores_gap_that_broke():
    rows = _calm(30)
    rows += [(100, 101, 99.5, 101), (101, 106, 101, 106), (106, 108, 104, 107)]
    rows += [(107, 107, 99, 100)]  # schließt unter der Lücke: verbraucht, kein Long
    sp = StrategyParams(kind="fvg", lookback=10, use_trend=False)
    assert (signals(_ohlc(rows), sp) == LONG).sum() == 0


def test_supertrend_flips_up_after_rally():
    rows = [(100 - k, 100.5 - k, 99 - k, 99.5 - k) for k in range(40)]
    rows += [(60 + 2 * k, 62 + 2 * k, 59.5 + 2 * k, 61.5 + 2 * k) for k in range(20)]
    df = _ohlc(rows)
    st = supertrend(df, 10, 3.0)
    assert st.iloc[30] == -1 and st.iloc[-1] == 1
    sig = signals(df, StrategyParams(kind="supertrend"))
    assert (sig == LONG).sum() == 1


def test_fib_long_in_golden_pocket():
    rows = [(100 + k, 101 + k, 99.5 + k, 100.5 + k) for k in range(20)]  # Aufwärts 99,5 → 120
    rows += [(119, 119.5, 106, 108)]  # Tief unter 61,8 % (~107,4), Schluss über 78,6 % (~104,1)
    sp = StrategyParams(kind="fib", lookback=20, use_trend=False)
    assert signals(_ohlc(rows), sp).iloc[-1] == LONG


def test_sweep_long_when_low_is_swept_and_reclaimed():
    rows = _calm(25) + [(100, 100.2, 98.5, 100.1)]  # sticht unter 99,5, schließt darüber
    sp = StrategyParams(kind="sweep", lookback=20, use_trend=False)
    assert signals(_ohlc(rows), sp).iloc[-1] == LONG


@pytest.mark.parametrize("kind", ["fvg", "supertrend", "fib", "sweep"])
def test_new_strategies_use_no_future_data(kind):
    df = _random_frame(400)
    sp = StrategyParams(kind=kind, lookback=20, trend=50)
    full = signals(df, sp)
    assert (full != 0).any()
    for end in range(100, len(df), 7):
        assert signals(df.iloc[:end], sp).iloc[-1] == full.iloc[end - 1]


def test_take_profit_closes_at_target():
    pos = Position.open("BTC", LONG, 100.0, 10_000, 1.0, 2.0, 0, take_profit_r=2.0)
    assert pos.target == pytest.approx(104.0)
    hit = pos.on_candle(101, 105, 100.5, trail_pct=5.0)
    assert hit == pytest.approx(104.0) and pos.hit_reason(hit) == "target"


def test_stop_wins_when_stop_and_target_in_same_candle():
    pos = Position.open("BTC", LONG, 100.0, 10_000, 1.0, 2.0, 0, take_profit_r=2.0)
    hit = pos.on_candle(100, 105, 97, trail_pct=5.0)
    assert hit == pytest.approx(98.0) and pos.hit_reason(hit) == "stop"


def test_max_position_pct_caps_size():
    pos = Position.open("BTC", LONG, 100.0, 10_000, 1.0, 0.2, 0, max_position_pct=25)
    assert pos.qty * 100.0 == pytest.approx(2_500)


def test_old_positions_without_new_fields_still_load():
    pos = Position("BTC", LONG, 100.0, 1.0, 99.0, 100.0, 0)
    assert pos.target == 0.0 and pos.trail_pct == 0.0


def test_entry_risk_daily_limit_and_drawdown_brake():
    bp = BacktestParams(risk_pct=1.0, daily_loss_limit_pct=2.0, drawdown_brake_pct=10.0)
    assert entry_risk(bp, 10_000, 10_000, 10_000) == 1.0
    assert entry_risk(bp, 9_790, 10_000, 10_000) == 0.0       # 2,1 % Tagesverlust
    assert entry_risk(bp, 8_900, 10_000, 8_900) == 0.5        # 11 % unter Höchststand


def test_backtest_with_safety_rules_runs():
    bp = BacktestParams(stop_atr=2.0, trail_atr=2.0, take_profit_r=2.0, max_position_pct=25,
                        daily_loss_limit_pct=2.0, drawdown_brake_pct=10.0)
    res = run(_random_frame(800), bp, StrategyParams(kind="supertrend"))
    assert res.trades
    assert {t.reason for t in res.trades} <= {"stop", "target", "signal", "end"}


def test_kelly():
    assert kelly_pct([10, -5] * 10) == pytest.approx(25.0)   # W 0,5, R 2 → 0,5 − 0,5/2
    assert kelly_pct([5, -10] * 10) < 0
    assert kelly_pct([1, -1]) is None


def test_account_marks_peak_and_day_start():
    acc = PaperAccount(":memory:", start_equity=10_000, fee_pct=0.0)
    for when, exit_ in [(1000, 110.0), (90_000, 95.0)]:
        acc.open(Position.open("BTC", LONG, 100.0, acc.balance, 1.0, 10.0, 0))
        acc.close("BTC", exit_, "signal", when=when)
    peak, day_start = account_marks(acc, now=90_500)
    assert peak == pytest.approx(10_100)
    assert day_start == pytest.approx(10_100)   # Stand nach dem Trade vom Vortag
