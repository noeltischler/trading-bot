import pandas as pd

from tradingbot.storage import CandleStore


def _candles(times, close=100.0):
    return pd.DataFrame({
        "time": times, "open": close, "high": close + 1,
        "low": close - 1, "close": close, "volume": 10.0,
    })


def test_upsert_and_load_roundtrip():
    store = CandleStore(":memory:")
    store.upsert("BTC", "15m", _candles([0, 900, 1800]))
    df = store.load("BTC", "15m")
    assert list(df.time) == [0, 900, 1800]
    assert store.last_time("BTC", "15m") == 1800


def test_upsert_overwrites_unfinished_candle():
    store = CandleStore(":memory:")
    store.upsert("BTC", "15m", _candles([0, 900], close=100.0))
    store.upsert("BTC", "15m", _candles([900, 1800], close=105.0))
    df = store.load("BTC", "15m")
    assert list(df.time) == [0, 900, 1800]
    assert df.close.tolist() == [100.0, 105.0, 105.0]


def test_coins_and_timeframes_are_separate():
    store = CandleStore(":memory:")
    store.upsert("BTC", "15m", _candles([0]))
    store.upsert("ETH", "15m", _candles([0, 900]))
    store.upsert("BTC", "1h", _candles([0, 3600, 7200]))
    assert len(store.load("BTC", "15m")) == 1
    assert len(store.load("ETH", "15m")) == 2
    assert len(store.load("BTC", "1h")) == 3
    assert store.last_time("SOL", "15m") is None
