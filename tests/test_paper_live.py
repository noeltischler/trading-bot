
import numpy as np
import pandas as pd
import pytest

from tradingbot.config import Config, RiskConfig
from tradingbot.live import step
from tradingbot.paper import PaperAccount
from tradingbot.position import Position
from tradingbot.strategy import LONG, SHORT, StrategyParams


def test_paper_account_open_close_updates_balance():
    acc = PaperAccount(":memory:", start_equity=10_000, fee_pct=0.0)
    pos = Position.open("BTC", LONG, 100.0, acc.balance, risk_pct=1.0, stop_pct=1.0, time=0)
    acc.open(pos)
    with pytest.raises(ValueError):
        acc.open(pos)
    pnl = acc.close("BTC", 99.0, "stop", when=10)
    assert pnl == pytest.approx(-100.0)
    assert acc.balance == pytest.approx(9_900.0)
    assert acc.positions() == {}
    assert acc.trades()[0]["reason"] == "stop"


def test_paper_account_persists_trailing_stop():
    acc = PaperAccount(":memory:", fee_pct=0.0)
    pos = Position.open("ETH", SHORT, 100.0, acc.balance, 1.0, 1.0, 0)
    acc.open(pos)
    pos.on_candle(99, 99.5, 90, trail_pct=1.0)
    acc.save_position(pos)
    assert acc.position("ETH").stop == pytest.approx(90.9)


class FakeMarket:
    id = "fake"

    def __init__(self, df, price):
        self.df, self.price = df, price

    def closed_candles(self, symbol, timeframe, limit=400):
        return self.df

    def last_price(self, symbol):
        return self.price


def _frame(closes):
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame({
        "time": np.arange(len(closes)) * 900, "open": opens,
        "high": np.maximum(opens, closes) * 1.001, "low": np.minimum(opens, closes) * 0.999,
        "close": closes, "volume": 1.0,
    })


def _cfg():
    return Config(
        symbols={"BTC": "BTC/USD"},
        risk=RiskConfig(risk_per_trade_pct=1.0, stop_pct=1.0, trailing_stop_pct=1.0),
        strategy=StrategyParams(fast=3, slow=6, trend=10, rsi_max_long=100, rsi_min_short=0),
    )


def _uptrend_with_fresh_cross():
    # Rücksetzer, dann Kreuzung der EMAs genau auf der letzten Kerze suchen
    base = list(np.linspace(100, 110, 30)) + list(np.linspace(110, 107, 5))
    for extra in range(1, 30):
        closes = base + list(np.linspace(107, 120, 30))[:extra]
        from tradingbot.strategy import signals
        if signals(_frame(closes), _cfg().strategy).iloc[-1] == LONG:
            return closes
    raise AssertionError("kein Signal gefunden")


def test_live_step_opens_position_once_per_candle():
    closes = _uptrend_with_fresh_cross()
    acc = PaperAccount(":memory:", fee_pct=0.0)
    market = FakeMarket(_frame(closes), price=closes[-1])
    step(_cfg(), market, acc)
    pos = acc.position("BTC")
    assert pos and pos.side == LONG
    assert pos.qty * (pos.entry - pos.stop) == pytest.approx(100.0)  # 1 % Risiko
    # Gleiche Kerze nochmal: nichts passiert
    step(_cfg(), market, acc)
    assert len(acc.positions()) == 1 and acc.trades() == []


def test_live_step_hits_stop_on_next_candle():
    closes = _uptrend_with_fresh_cross()
    acc = PaperAccount(":memory:", fee_pct=0.0)
    df = _frame(closes)
    step(_cfg(), FakeMarket(df, price=closes[-1]), acc)
    entry = acc.position("BTC").entry
    crash = pd.concat([df, pd.DataFrame([{
        "time": df.time.iloc[-1] + 900, "open": entry, "high": entry,
        "low": entry * 0.95, "close": entry * 0.96, "volume": 1.0,
    }])], ignore_index=True)
    step(_cfg(), FakeMarket(crash, price=entry * 0.96), acc)
    trades = acc.trades()
    assert trades and trades[0]["reason"] == "stop"
    assert trades[0]["pnl"] == pytest.approx(-100.0)


def test_live_step_btc_filter_blocks_long_when_btc_falls():
    from dataclasses import replace

    closes = _uptrend_with_fresh_cross()
    up, down = _frame(closes), _frame(list(np.linspace(200, 100, len(closes))))

    class TwoCoins(FakeMarket):
        def closed_candles(self, symbol, timeframe, limit=400):
            return down if symbol == "BTC/USD" else up

    cfg = _cfg()
    cfg.symbols = {"BTC": "BTC/USD", "ETH": "ETH/USD"}
    cfg.strategy = replace(cfg.strategy, btc_filter=10)
    acc = PaperAccount(":memory:", fee_pct=0.0)
    step(cfg, TwoCoins(up, price=closes[-1]), acc)
    assert acc.position("ETH") is None   # ETH hätte ein Long-Signal, aber Bitcoin fällt

    cfg.strategy = replace(cfg.strategy, btc_filter=0)
    acc2 = PaperAccount(":memory:", fee_pct=0.0)
    step(cfg, TwoCoins(up, price=closes[-1]), acc2)
    assert acc2.position("ETH") is not None
