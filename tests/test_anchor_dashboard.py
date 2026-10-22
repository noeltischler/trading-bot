from datetime import date, datetime, timezone

from tradingbot.anchor import GENESIS, anchor_due_days, verify
from tradingbot.dashboard import render
from tradingbot.paper import PaperAccount
from tradingbot.position import Position
from tradingbot.strategy import LONG, SHORT


def _ts(day: str, hour: int = 12) -> int:
    return int(datetime.fromisoformat(f"{day}T{hour:02d}:00:00+00:00").timestamp())


def _account_with_trades():
    acc = PaperAccount(":memory:", fee_pct=0.0)
    for i, (day, side, exit_) in enumerate([("2026-10-01", LONG, 101.0), ("2026-10-02", SHORT, 98.0)]):
        coin = f"C{i}"
        acc.open(Position.open(coin, side, 100.0, acc.balance, 1.0, 1.0, _ts(day, 1)))
        acc.close(coin, exit_, "stop", when=_ts(day))
    return acc


def test_anchors_one_per_finished_day_and_chain(monkeypatch):
    monkeypatch.delenv("ANCHOR_RPC_URL", raising=False)
    acc = _account_with_trades()
    anchor_due_days(acc, today=date(2026, 10, 4))
    days = [a["day"] for a in acc.anchors()]
    assert days == ["2026-10-01", "2026-10-02", "2026-10-03"]
    assert acc.anchors()[0]["prev"] == GENESIS
    assert acc.anchors()[1]["prev"] == acc.anchors()[0]["hash"]
    # zweiter Aufruf am selben Tag legt nichts doppelt an
    anchor_due_days(acc, today=date(2026, 10, 4))
    assert len(acc.anchors()) == 3
    assert verify(acc) == []


def test_verify_detects_tampering(monkeypatch):
    monkeypatch.delenv("ANCHOR_RPC_URL", raising=False)
    acc = _account_with_trades()
    anchor_due_days(acc, today=date(2026, 10, 3))
    with acc.conn:
        acc.conn.execute("UPDATE trades SET pnl = 999 WHERE id = 1")
    problems = verify(acc)
    assert problems and "verändert" in problems[0]


def test_no_anchor_without_trades():
    acc = PaperAccount(":memory:")
    anchor_due_days(acc, today=date(2026, 10, 4))
    assert acc.anchors() == []


def test_dashboard_renders_kpis_trades_and_positions(monkeypatch):
    monkeypatch.delenv("ANCHOR_RPC_URL", raising=False)
    acc = _account_with_trades()
    acc.open(Position.open("BTC", LONG, 50_000.0, acc.balance, 1.0, 1.0, _ts("2026-10-03")))
    anchor_due_days(acc, today=date(2026, 10, 3))
    page = render(acc)
    assert "Kontostand" in page and "BTC" in page and "Long" in page and "Short" in page
    assert "<svg" in page and "nur lokal" in page


def test_dashboard_empty_account():
    page = render(PaperAccount(":memory:"))
    assert "Noch keine Trades" in page and "Keine offene Position" in page


def test_only_days_with_trades_are_published(monkeypatch):
    published = []
    monkeypatch.setattr("tradingbot.anchor.publish", lambda h: published.append(h) or f"https://x/{h}")
    acc = _account_with_trades()  # Trades am 01.10. und 02.10.
    anchor_due_days(acc, today=date(2026, 10, 5))
    urls = {a["day"]: a["tx_url"] for a in acc.anchors()}
    assert urls["2026-10-01"] and urls["2026-10-02"]
    assert urls["2026-10-03"] is None and urls["2026-10-04"] is None
    assert len(published) == 2


def test_chart_page_shows_candles_trades_stop_and_price():
    import pandas as pd

    from tradingbot.dashboard import render_chart
    from tradingbot.storage import CandleStore

    acc = PaperAccount(":memory:", fee_pct=0.0)
    store = CandleStore(":memory:")
    t0 = _ts("2026-10-20", 0)
    store.upsert("BTC", "1h", pd.DataFrame({
        "time": [t0 + i * 3600 for i in range(30)], "open": 100.0, "high": 102.0,
        "low": 99.0, "close": 101.0, "volume": 1.0,
    }))
    acc.open(Position.open("BTC", LONG, 100.0, acc.balance, 1.0, 1.0, t0 + 3 * 3600))
    acc.close("BTC", 101.5, "stop", when=t0 + 8 * 3600)
    acc.open(Position.open("BTC", SHORT, 101.0, acc.balance, 1.0, 1.0, t0 + 20 * 3600))
    acc.set_state("price:BTC", f"100.5:{t0 + 30 * 3600}")
    page = render_chart(acc, store, ["BTC", "ETH"], "1h", "BTC", "1d", now=t0 + 30 * 3600)
    assert page.count("<rect") == 24 + 0  # nur die Kerzen des letzten Tages
    assert "m-long" in page and "m-short" in page and "m-exit" in page
    assert 'class="stop"' in page and "price-l" in page
    assert "coin=ETH" in page and "Short</b>" in page
    empty = render_chart(acc, store, ["BTC", "ETH"], "1h", "ETH")
    assert "noch keine Kurse" in empty


def test_live_step_saves_candles_and_price_for_chart():
    import pandas as pd

    from tradingbot.config import Config
    from tradingbot.live import step
    from tradingbot.storage import CandleStore

    class Market:
        def closed_candles(self, symbol, timeframe, limit=400):
            return pd.DataFrame({"time": [0, 900], "open": 1.0, "high": 1.0, "low": 1.0,
                                 "close": 1.0, "volume": 1.0})

        def last_price(self, symbol):
            return 1.25

    acc, store = PaperAccount(":memory:"), CandleStore(":memory:")
    cfg = Config(symbols={"BTC": "BTC/USD"})
    step(cfg, Market(), acc, store)
    assert len(store.load("BTC", cfg.timeframe)) == 2
    assert acc.get_state("price:BTC").startswith("1.25:")


def _account_many_trades():
    acc = PaperAccount(":memory:", fee_pct=0.0)
    now = _ts("2026-10-22", 12)
    spec = [("BTC", LONG, 102.0, 1), ("ETH", SHORT, 103.0, 3), ("BTC", SHORT, 97.0, 20), ("SOL", LONG, 99.0, 200)]
    for coin, side, exit_, days_ago in spec:
        t = now - days_ago * 86400
        acc.open(Position.open(coin, side, 100.0, acc.balance, 1.0, 5.0, t - 3600))
        acc.close(coin, exit_, "signal", when=t)
    return acc, now


def test_pnl_buckets_per_day_and_month():
    from tradingbot.dashboard import pnl_buckets

    acc, now = _account_many_trades()
    days = pnl_buckets(acc.trades(), now - 7 * 86400, now, "day")
    assert len(days) == 8 and sum(n for _, _, n in days) == 2
    months = pnl_buckets(acc.trades(), now - 365 * 86400, now, "month")
    assert len(months) in (12, 13) and sum(n for _, _, n in months) == 4
    assert round(sum(v for _, v, _ in months), 6) == round(sum(t["pnl"] for t in acc.trades()), 6)


def test_overview_periods_and_prices_keep_thousands_separator():
    acc, now = _account_many_trades()
    page = render(acc, period="1w", now=now)
    for name in ("Tag", "Woche", "Monat", "3 Monate", "Jahr"):
        assert f'<div class="l">{name}</div>' in page
    assert "Ergebnis je Tag" in page and "svg" in page and "Profitfaktor" in page
    assert "100,00 → 102,00" in page  # Kurse im deutschen Format, nicht 100,00 → 102,00 kaputt ersetzt


def test_trades_page_filters_and_csv():
    from tradingbot.dashboard import filter_trades, render_trades, trades_csv

    acc, now = _account_many_trades()
    assert len(filter_trades(acc.trades(), coin="BTC")) == 2
    assert len(filter_trades(acc.trades(), side="short")) == 2
    assert len(filter_trades(acc.trades(), result="loss", now=now)) == 2
    assert len(filter_trades(acc.trades(), period="1m", now=now)) == 3
    page = render_trades(acc, coin="BTC", now=now)
    assert page.count("/chart?trade=") == 2 and "Gegensignal" in page
    csv = trades_csv(acc.trades())
    assert csv.splitlines()[0].startswith("Nr;") and len(csv.splitlines()) == 5 and "102" in csv


def test_chart_zooms_to_single_trade():
    import pandas as pd

    from tradingbot.dashboard import render_chart
    from tradingbot.storage import CandleStore

    acc, now = _account_many_trades()
    store = CandleStore(":memory:")
    store.upsert("BTC", "1h", pd.DataFrame({
        "time": [now - 30 * 86400 + i * 3600 for i in range(30 * 24)], "open": 100.0, "high": 101.0,
        "low": 99.0, "close": 100.5, "volume": 1.0,
    }))
    btc = [t for t in acc.trades() if t["coin"] == "BTC"]
    page = render_chart(acc, store, ["BTC", "ETH"], "1h", trade_id=btc[0]["id"], now=now)
    assert f"Trade #{btc[0]['id']}" in page and "Zurück zum ganzen Chart" in page and "class='hl'" in page
    assert page.count("<rect") < 200  # nur ein Ausschnitt, nicht 30 Tage
