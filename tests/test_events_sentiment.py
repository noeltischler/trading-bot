from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from tradingbot.backtest import BacktestParams, entry_risk, run
from tradingbot.events import EVENTS, event_near, next_events, upcoming_gap
from tradingbot.sentiment import with_fear_greed
from tradingbot.strategy import LONG, SHORT, StrategyParams, signals


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc).timestamp()


def test_event_times_respect_daylight_saving():
    # Fed 28.01.2026 14:00 New York = 19:00 UTC (Winterzeit), 17.06.2026 = 18:00 UTC (Sommerzeit)
    assert event_near(_utc(2026, 1, 28, 19, 0), 0, 0) == "US-Zinsentscheid (Fed)"
    assert event_near(_utc(2026, 6, 17, 18, 0), 0, 0) == "US-Zinsentscheid (Fed)"
    # EZB 11.06.2026 14:15 Frankfurt = 12:15 UTC
    assert event_near(_utc(2026, 6, 11, 12, 15), 0, 0) == "EZB-Zinsentscheid"


def test_event_window():
    cpi = _utc(2026, 7, 14, 12, 30)  # 8:30 New York
    assert event_near(cpi - 1.5 * 3600, 2, 2) == "US-Inflation (CPI)"
    assert event_near(cpi + 2.5 * 3600, 2, 2) is None
    assert event_near(_utc(2026, 7, 20, 12, 0), 2, 2) is None


def test_event_list_sorted_and_next_events():
    assert [e[0] for e in EVENTS] == sorted(e[0] for e in EVENTS)
    nxt = next_events(_utc(2026, 10, 24), 2)
    assert [name for _, name in nxt] == ["US-Zinsentscheid (Fed)", "EZB-Zinsentscheid"]
    assert not upcoming_gap(_utc(2026, 10, 24))
    assert upcoming_gap(_utc(2027, 6, 1))


def test_entry_risk_pauses_around_events():
    bp = BacktestParams(risk_pct=1.0, event_pause_hours=2)
    fed = _utc(2026, 10, 28, 18, 0)  # 14:00 New York, Sommerzeit in den USA bis 1.11.
    assert entry_risk(bp, 10_000, 10_000, 10_000, fed - 3600) == 0.0
    assert entry_risk(bp, 10_000, 10_000, 10_000, fed + 5 * 3600) == 1.0
    assert entry_risk(BacktestParams(risk_pct=1.0), 10_000, 10_000, 10_000, fed) == 1.0


def test_fear_greed_uses_previous_day_only():
    fg = pd.DataFrame({"day_start": [0, 86400, 172800], "value": [10, 50, 90], "label": ["Extreme Fear", "Neutral", "Extreme Greed"]})
    df = pd.DataFrame({"time": [3600, 86400 + 3600, 172800 + 3600, 259200 + 3600]})
    out = with_fear_greed(df, fg)
    assert np.isnan(out.fear_greed.iloc[0])          # Tag 0: Vortag unbekannt
    assert list(out.fear_greed.iloc[1:]) == [10, 50, 90]


def _sweep_frame():
    rows = [(100.0, 100.5, 99.5, 100.0)] * 25 + [(100, 100.2, 98.5, 100.1)] + [(100.0, 100.5, 99.5, 100.0)] * 5
    rows += [(100.0, 101.6, 99.9, 100.0)]  # Short-Sweep: über das Hoch, Schluss darunter
    o, h, l, c = (np.asarray(x) for x in zip(*rows))
    return pd.DataFrame({"time": np.arange(len(c)) * 3600 + 86400 * 5, "open": o, "high": h, "low": l, "close": c, "volume": 1.0})


@pytest.mark.parametrize("mode,value,long_ok,short_ok", [
    ("contrarian", 10, True, False),   # extreme Angst: nicht shorten
    ("contrarian", 90, False, True),   # extreme Gier: nicht kaufen
    ("follow", 10, False, True),
    ("follow", 90, True, False),
    ("contrarian", 50, True, True),
])
def test_fear_greed_filter(mode, value, long_ok, short_ok):
    df = _sweep_frame()
    df["fear_greed"] = float(value)
    sp = StrategyParams(kind="sweep", lookback=20, use_trend=False, fg_mode=mode)
    sig = signals(df, sp)
    assert ((sig == LONG).any()) == long_ok
    assert ((sig == SHORT).any()) == short_ok


def test_fear_greed_filter_inactive_without_data_but_column_required():
    df = _sweep_frame()
    sp = StrategyParams(kind="sweep", lookback=20, use_trend=False, fg_mode="contrarian")
    with pytest.raises(ValueError):
        signals(df, sp)
    df["fear_greed"] = np.nan
    assert (signals(df, sp) != 0).sum() == 2


def test_backtest_skips_entries_in_event_window():
    n = 400
    start = _utc(2026, 7, 10)
    rng = np.random.default_rng(5)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    df = pd.DataFrame({"time": (start + np.arange(n) * 3600).astype(int), "open": o, "high": np.maximum(o, c) * 1.004,
                       "low": np.minimum(o, c) * 0.996, "close": c, "volume": 1.0})
    sp = StrategyParams(kind="sweep", lookback=10, use_trend=False)
    paused = run(df, BacktestParams(event_pause_hours=48), sp)
    for t in paused.trades:
        assert event_near(t.entry_time, 48, 48) is None
