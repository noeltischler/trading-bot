import sqlite3

from tradingbot import news
from tradingbot.dashboard import render
from tradingbot.paper import PaperAccount

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Bitcoin surges to record high as ETF inflows jump</title><link>https://example.com/a</link>
<pubDate>Sat, 24 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Exchange hacked, regulators open investigation</title><link>https://example.com/b</link>
<pubDate>Sat, 24 Oct 2026 11:00:00 +0000</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>y</title>
<entry><title>EZB senkt den Leitzins</title><link href="https://example.com/c"/><updated>2026-10-24T12:00:00Z</updated></entry>
</feed>"""


def test_score_english_and_german():
    assert news.score("Bitcoin surges to record high as ETF inflows jump") == 3
    assert news.score("Exchange hacked, regulators open investigation") == -2
    assert news.score("Bitcoin fällt nach Zinserhöhung") == -2
    assert news.score("Wie es war, als Bitcoin startete") == 0  # „war“ ist kein Stichwort
    assert news.score("Markets quiet ahead of Fed") == 0


def test_parse_rss_and_atom():
    items = news.parse_feed(RSS, "Test", now=1_900_000_000)
    assert [i["score"] for i in items] == [3, -2]
    assert items[0]["link"] == "https://example.com/a" and items[0]["published"] == 1792836000
    atom = news.parse_feed(ATOM, "Atom", now=1_900_000_000)
    assert atom[0]["title"] == "EZB senkt den Leitzins" and atom[0]["link"] == "https://example.com/c"
    assert atom[0]["score"] == 1


def test_save_skips_duplicates_and_mood():
    conn = sqlite3.connect(":memory:")
    items = news.parse_feed(RSS, "Test", now=1_900_000_000)
    assert news.save(conn, items) == 2
    assert news.save(conn, items) == 0
    got = news.recent(conn, 0)
    assert len(got) == 2 and news.mood(got) == 0.5


def test_fetch_all_survives_broken_feed():
    assert news.fetch_all({"kaputt": "http://127.0.0.1:9/nichts"}, timeout=1) == []


def test_dashboard_shows_headlines_escaped():
    acc = PaperAccount(":memory:")
    news.save(acc.conn, [{"source": "Test", "title": "<b>Bitcoin surges</b>", "link": "https://example.com/x",
                          "published": 1_792_836_000, "score": 1},
                         {"source": "Test", "title": "Böser Link", "link": "javascript:alert(1)",
                          "published": 1_792_836_000, "score": 0}])
    page = render(acc, now=1_792_840_000)
    assert "&lt;b&gt;Bitcoin surges&lt;/b&gt;" in page
    assert "javascript:" not in page
    assert "Nachrichtenlage" in page


def test_notify_topic_texts_and_dashboard_card():
    from tradingbot import notify

    acc = PaperAccount(":memory:")
    t = notify.topic(acc)
    assert t.startswith("tradingbot-") and len(t) > 30 and notify.topic(acc) == t
    title, msg, tags = notify.opened_text("SOL", False, 187.4, 193.02, 74.9)
    assert title == "SOL: SHORT (Leerverkauf) eröffnet" and "187,40" in msg and "193,02" in msg
    title, msg, _ = notify.closed_text("BTC", True, 101_234.5, -12.5, "stop", 9_800)
    assert title == "BTC: Long geschlossen, -12,50 $" and "101.234,50" in msg and "Stop" in msg
    assert t in render(acc, now=1_792_840_000)


def test_live_step_notifies_on_open():
    import numpy as np
    import pandas as pd

    from tradingbot.config import Config, RiskConfig
    from tradingbot.live import step
    from tradingbot.strategy import StrategyParams

    rows = [(100.0, 100.5, 99.5, 100.0)] * 25 + [(100, 100.2, 98.5, 100.1)]
    o, h, l, c = (np.asarray(x) for x in zip(*rows))
    df = pd.DataFrame({"time": np.arange(len(c)) * 3600, "open": o, "high": h, "low": l, "close": c, "volume": 1.0})

    class Market:
        id = "fake"
        def closed_candles(self, *a, **k): return df
        def last_price(self, symbol): return 100.1

    cfg = Config(symbols={"BTC": "BTC/USD"}, risk=RiskConfig(stop_pct=2, trailing_stop_pct=2),
                 strategy=StrategyParams(kind="sweep", lookback=20, use_trend=False))
    sent = []
    acc = PaperAccount(":memory:", fee_pct=0.0)
    step(cfg, Market(), acc, notify_fn=lambda account, title, msg, tags: sent.append(title))
    assert sent == ["BTC: LONG (Kauf) eröffnet"]
