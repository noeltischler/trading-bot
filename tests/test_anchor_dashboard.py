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


def test_one_dashboard_for_two_bots(tmp_path):
    from tradingbot.dashboard import Bot, page_for, route

    bots = [Bot("config", "Krypto", "Trading-Bot", str(tmp_path / "a.db"), 10000, 0.1, ["BTC"], "4h"),
            Bot("gold", "Gold", "Gold-Bot", str(tmp_path / "g.db"), 10000, 0.1, ["GOLD"], "1d")]
    assert route(bots, "/") == (bots[0], "/")
    assert route(bots, "/gold") == (bots[1], "/")
    assert route(bots, "/gold/trades") == (bots[1], "/trades")
    assert route(bots, "/gold/x") is None and route(bots, "/x") is None
    page, _ = page_for(bots, bots[1], "/", {})
    assert "Gold-Bot" in page and 'href="/gold/trades' in page
    assert '<nav class="tabs bots"><a href="/" class="">Krypto</a><a href="/gold" class="on">Gold</a>' in page
    page, _ = page_for(bots, bots[0], "/chart", {})
    assert 'href="/gold' not in page.replace('href="/gold" class=""', "")
    csv, ctype = page_for(bots, bots[1], "/trades.csv", {})
    assert ctype.startswith("text/csv")


def test_trade_and_bar_tips():
    from tradingbot.dashboard import bucket_tips, trade_tip

    t = {"coin": "BTC", "side": 1, "entry": 100.0, "exit": 110.0, "opened_at": 0, "closed_at": 7200,
         "pnl": 50.0, "balance": 10050.0, "strategy": "Keil 80"}
    tip = trade_tip(t)
    assert "BTC · Long" in tip and "+10,00 %" in tip and "+0,50 %" in tip and "+50,00 $" in tip and "Keil 80" in tip
    tips = bucket_tips([t], 0, 86400 * 2, "day")
    assert len(tips) == 3 and "Trades" in tips[0] and "+50,00 $" in tips[0] and "BTC Long" in tips[0]


def test_overview_balance_includes_open_positions():
    acc = PaperAccount(":memory:", fee_pct=0.0)
    pos = Position.open("ETH", SHORT, 2000.0, acc.balance, 1.0, 1.0, _ts("2026-10-03"))
    acc.open(pos)
    acc.set_state("price:ETH", f"1900.0:{_ts('2026-10-04')}")
    gain = pos.pnl(1900.0, 0.0)
    assert gain > 0
    page = render(acc, now=_ts("2026-10-04"))
    total = acc.balance + gain
    from tradingbot.dashboard import _money, _signed
    assert f"{_money(total)} $" in page
    assert f"{_money(acc.balance)} $</div><div class=\"l\">Kapital" in page
    assert f"{_signed(gain)} $</div><div class=\"l\">Offene Positionen (1)" in page and "<th>Jetzt</th>" in page
    # ohne Kurs bleibt der Kontostand beim Bargeld
    acc2 = PaperAccount(":memory:", fee_pct=0.0)
    acc2.open(Position.open("ETH", SHORT, 2000.0, acc2.balance, 1.0, 1.0, _ts("2026-10-03")))
    assert f"{_money(acc2.balance)} $" in render(acc2)


def test_login_logout_with_session_cookie(tmp_path):
    import socket, threading, time, urllib.error, urllib.request
    from tradingbot.dashboard import serve, session_token, session_valid

    assert session_valid(session_token("geheim"), "geheim")
    assert not session_valid(session_token("geheim"), "anders")  # neues Passwort -> alte Anmeldung ungültig
    assert not session_valid(session_token("geheim", now=0), "geheim")  # abgelaufen
    assert not session_valid("kaputt", "geheim")

    db = str(tmp_path / "paper.db")
    PaperAccount(db)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    threading.Thread(target=serve, kwargs=dict(db_path=db, port=port, password="geheim"), daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)

    def req(path, data=None, cookie=None):
        r = urllib.request.Request(base + path, data=data.encode() if data else None)
        if cookie:
            r.add_header("Cookie", cookie)
        try:
            resp = opener.open(r)
        except urllib.error.HTTPError as e:
            resp = e
        return resp.status, resp.headers, resp.read().decode("utf-8", "replace")

    code, headers, _ = req("/trades")
    assert code == 303 and headers["Location"] == "/login?next=/trades"
    code, _, body = req("/login")
    assert code == 200 and 'type="password"' in body
    code, _, body = req("/login", "password=falsch&next=/trades")
    assert code == 401 and "Falsches Passwort" in body
    code, headers, _ = req("/login", "password=geheim&next=//evil.example")
    assert code == 303 and headers["Location"] == "/"
    cookie = headers["Set-Cookie"].split(";")[0]
    assert "HttpOnly" in headers["Set-Cookie"]
    code, _, body = req("/", cookie=cookie)
    assert code == 200 and 'href="/logout"' in body and "Kontostand" in body
    menu = body.split('<nav class="menu"', 1)[1].split("</nav>", 1)[0]
    assert 'href="/logout"' in menu and 'id="wchip"' in body.split("<header", 1)[1].split("</header>", 1)[0]
    code, headers, _ = req("/logout", cookie=cookie)
    assert code == 303 and headers["Location"] == "/login" and "Max-Age=0" in headers["Set-Cookie"]


def test_no_logout_button_without_password():
    assert "/logout" not in render(PaperAccount(":memory:"))


def test_menu_and_wallet_page():
    from tradingbot.dashboard import render_wallet

    page = render(PaperAccount(":memory:"))
    assert '<nav class="menu"' in page and 'href="/wallet"' in page and "<body data-noreload>" not in page
    assert "Wallet trennen" in page and "wallet_revokePermissions" in page
    w = render_wallet("Trading-Bot", "testnet")
    assert 'data-net="testnet"' in w and "TESTNETZ" in w and "Wallet verbinden" in w and "<body data-noreload>" in w
    assert '<a href="/wallet" class="on">' in w
    assert "ECHTGELD" in render_wallet("Trading-Bot", "mainnet")


def test_hyperliquid_proxy_only_allows_reads_and_signed_withdrawals(monkeypatch):
    from tradingbot.dashboard import hl_exchange_allowed, hl_info_allowed, hl_network

    a = "0x" + "ab" * 20
    assert hl_info_allowed({"type": "clearinghouseState", "user": a})
    assert hl_info_allowed({"type": "userNonFundingLedgerUpdates", "user": a, "startTime": 0})
    assert not hl_info_allowed({"type": "clearinghouseState", "user": "kein"})
    assert not hl_info_allowed({"type": "allMids"})
    good = {"action": {"type": "withdraw3", "signatureChainId": "0xa4b1", "hyperliquidChain": "Testnet",
                       "destination": a, "amount": "10", "time": 5}, "nonce": 5, "signature": {"r": "0x1", "s": "0x2", "v": 27}}
    assert hl_exchange_allowed(good, "testnet")
    assert not hl_exchange_allowed(good, "mainnet")  # falsches Netz
    order = {**good, "action": {**good["action"], "type": "order"}}
    assert not hl_exchange_allowed(order, "testnet")  # Orders oder andere Aktionen nie über das Dashboard
    assert not hl_exchange_allowed({**good, "nonce": 6}, "testnet")
    monkeypatch.delenv("HL_NETWORK", raising=False)
    assert hl_network() == "testnet"
    monkeypatch.setenv("HL_NETWORK", "Mainnet")
    assert hl_network() == "mainnet"


def test_chain_balances_reads_balanceof_only():
    from tradingbot.dashboard import CHAIN_TOKENS, chain_balances, render_wallet

    calls = []

    def fake_post(url, payload):
        calls.append((url, payload))
        token = payload["params"][0]["to"]
        if token.startswith("0x8AC7"):
            raise OSError("down")
        dec = 6 if token.startswith("0xaf88") else 18
        return {"result": hex(1234500 * 10 ** (dec - 4))}

    user = "0x" + "ab" * 20
    out = chain_balances(user, post=fake_post)
    assert out == {"bsc_usdt": 123.45, "bsc_usdc": None, "arb_usdc": 123.45}
    assert len(calls) == len(CHAIN_TOKENS)
    assert all(p["method"] == "eth_call" and p["params"][0]["data"] == "0x70a08231" + "00" * 12 + "ab" * 20 for _, p in calls)
    try:
        chain_balances("nope", post=fake_post)
        assert False
    except ValueError:
        pass
    w = render_wallet("Trading-Bot", "testnet")
    assert "Einzahlungsadresse" in w and "Guthaben in deiner Wallet" in w and "BEP20" in w
