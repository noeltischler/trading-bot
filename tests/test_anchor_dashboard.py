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
