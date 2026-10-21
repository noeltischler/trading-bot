from tradingbot.market import Market


class FakeExchange:
    """Liefert Kerzen seitenweise (max. 3 pro Abruf), wie echte Börsen."""

    def __init__(self, n):
        self.rows = [[i * 900_000, 1, 2, 0.5, 1.5, 10] for i in range(n)]

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        if since is None:
            return self.rows[-3:]
        return [r for r in self.rows if r[0] >= since][:3]

    def parse_timeframe(self, tf):
        return 900


def _market(n):
    m = Market.__new__(Market)
    m.ex, m.id = FakeExchange(n), "fake"
    return m


def test_candles_paginates_until_the_end():
    df = _market(8).candles("BTC/USD", "15m", since_ms=0)
    assert list(df.time) == [i * 900 for i in range(8)]


def test_candles_without_since_returns_latest_page():
    df = _market(8).candles("BTC/USD", "15m")
    assert list(df.time) == [5 * 900, 6 * 900, 7 * 900]


def test_closed_candles_drops_running_candle():
    # Zeitstempel weit in der Vergangenheit: alle Kerzen abgeschlossen
    assert len(_market(5).closed_candles("BTC/USD", "15m")) == 3
