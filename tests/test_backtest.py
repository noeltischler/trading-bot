import numpy as np
import pandas as pd
import pytest

from tradingbot.backtest import BacktestParams, run
from tradingbot.strategy import LONG, SHORT, StrategyParams, signals


def _frame(closes):
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame({
        "time": np.arange(len(closes)) * 900,
        "open": opens,
        "high": np.maximum(opens, closes) * 1.001,
        "low": np.minimum(opens, closes) * 0.999,
        "close": closes,
        "volume": 1.0,
    })


SMALL = StrategyParams(fast=3, slow=6, trend=10, rsi_max_long=100, rsi_min_short=0)


def _dip_then_rally():
    # Erst seitwärts/leicht fallend, dann kurzer Rücksetzer im Aufwärtstrend, dann Rally
    return list(np.linspace(100, 110, 30)) + list(np.linspace(110, 107, 5)) + list(np.linspace(107, 140, 40))


def test_long_signal_in_uptrend():
    sig = signals(_frame(_dip_then_rally()), SMALL)
    assert (sig == LONG).any()


def test_trailing_stop_locks_in_profit_on_rally_then_crash():
    closes = _dip_then_rally() + list(np.linspace(140, 100, 20))
    res = run(_frame(closes), BacktestParams(fee_pct=0.0), SMALL)
    winners = [t for t in res.trades if t.side == LONG and t.reason == "stop" and t.pnl > 0]
    assert winners, res.trades
    assert winners[0].exit > 130  # Stop wurde mit nach oben gezogen


def test_losing_trade_costs_exactly_risk_pct():
    bp = BacktestParams(start_equity=10_000, risk_pct=1.0, stop_pct=1.0, fee_pct=0.0)
    df = _frame([100, 98, 97])
    sig = pd.Series([LONG, 0, 0])
    res = run(df, bp, sig=sig)
    t = res.trades[0]
    assert t.reason == "stop"
    assert t.exit == pytest.approx(99.0)
    assert t.pnl == pytest.approx(-100.0)  # 1 % von 10.000


def test_short_trade_profits_when_price_falls():
    df = _frame(list(np.linspace(100, 80, 30)) + [85] * 3)
    sig = pd.Series([SHORT] + [0] * 32)
    res = run(df, BacktestParams(fee_pct=0.0), sig=sig)
    assert res.trades[0].side == SHORT
    assert res.trades[0].pnl > 0


def test_fees_reduce_result():
    closes = _dip_then_rally() + list(np.linspace(140, 100, 20))
    no_fee = run(_frame(closes), BacktestParams(fee_pct=0.0), SMALL)
    fee = run(_frame(closes), BacktestParams(fee_pct=0.1), SMALL)
    assert fee.equity.iloc[-1] < no_fee.equity.iloc[-1]
    assert fee.summary()["trades"] == no_fee.summary()["trades"]
