import json

import pytest

from tradingbot import hyperliquid as hl
from tradingbot.hyperliquid import LiveAccount, round_price, round_size
from tradingbot.position import Position
from tradingbot.strategy import LONG, SHORT


class FakeInfo:
    def __init__(self, ex):
        self.ex = ex
        self.coin_to_asset = {"BTC": 0, "ETH": 1, "PAXG": 2}
        self.asset_to_sz_decimals = {0: 5, 1: 4, 2: 3}

    def user_state(self, address):
        return {"marginSummary": {"accountValue": str(self.ex.value + sum(p["upnl"] for p in self.ex.pos.values()))},
                "assetPositions": [{"position": {"coin": c, "szi": str(p["szi"]), "unrealizedPnl": str(p["upnl"])}}
                                   for c, p in self.ex.pos.items()]}

    def user_fills(self, address):
        return self.ex.fills


class FakeExchange:
    """Spielt Hyperliquid nach: Market-Orders werden zum Kurs px gefüllt, Stops ruhen."""

    def __init__(self, px=100.0, value=100.0):
        self.px, self.value, self.pos, self.fills, self.calls, self.oid = px, value, {}, [], [], 0
        self.info = FakeInfo(self)

    def _ok(self, st):
        return {"status": "ok", "response": {"type": "order", "data": {"statuses": [st]}}}

    def update_leverage(self, lev, coin, cross):
        self.calls.append(("lev", coin, lev, cross))
        return {"status": "ok"}

    def market_open(self, coin, is_buy, sz, px, slippage):
        self.calls.append(("open", coin, is_buy, sz))
        self.pos[coin] = {"szi": sz if is_buy else -sz, "upnl": 0.0}
        return self._ok({"filled": {"totalSz": str(sz), "avgPx": str(self.px), "oid": 1}})

    def market_close(self, coin, sz, px, slippage):
        self.calls.append(("close", coin))
        p = self.pos.pop(coin)
        return self._ok({"filled": {"totalSz": str(abs(p["szi"])), "avgPx": str(self.px), "oid": 2}})

    def order(self, coin, is_buy, sz, limit_px, order_type, reduce_only=False):
        self.oid += 1
        self.calls.append(("stop", coin, is_buy, sz, order_type["trigger"]["triggerPx"], reduce_only))
        return self._ok({"resting": {"oid": 100 + self.oid}})

    def cancel(self, coin, oid):
        self.calls.append(("cancel", coin, oid))
        return {"status": "ok"}


def test_rounding_follows_hyperliquid_rules():
    assert round_size(0.123456, 4) == 0.1234
    assert round_price(97123.456, 5) == 97123.0
    assert round_price(0.1234567, 0) == 0.12346


def test_open_trail_and_close_mirror_to_exchange(tmp_path):
    ex = FakeExchange(px=101.0, value=100.0)
    acc = LiveAccount(tmp_path / "live.db", client=ex, account_address="0x" + "1" * 40)
    assert acc.balance == 100.0
    pos = Position.open("ETH", LONG, 100.0, acc.balance, 0.75, 3.0, 0)  # 25 $ Position
    acc.open(pos)
    p = acc.position("ETH")
    assert p.entry == 101.0 and p.stop == pytest.approx(97.0 * 1.01)  # Stop behält 3 % Abstand zum echten Kurs
    assert ("lev", "ETH", hl.LEVERAGE, True) in ex.calls
    stops = [c for c in ex.calls if c[0] == "stop"]
    assert stops[-1][2] is False and stops[-1][5] is True  # Verkaufs-Stop, nur Reduzieren
    # Stop nachziehen: neue Stop-Order, alte gelöscht
    p.stop = 99.0
    acc.save_position(p)
    assert ex.calls[-2][0] == "stop" and ex.calls[-2][4] == 99.0 and ex.calls[-1] == ("cancel", "ETH", 101)
    # unveränderter Stop: keine neue Order
    n = len(ex.calls)
    acc.save_position(acc.position("ETH"))
    assert len(ex.calls) == n
    ex.px = 105.0
    pnl = acc.close("ETH", 0.0, "signal")
    assert ("close", "ETH") in ex.calls and ex.calls[-1] == ("cancel", "ETH", 102)
    assert pnl == pytest.approx((105 - 101) * p.qty - (101 + 105) * p.qty * hl.FEE_PCT / 100)
    assert acc.trades()[0]["exit"] == 105.0 and acc.positions() == {}


def test_too_small_position_is_refused(tmp_path):
    ex = FakeExchange(px=100.0, value=20.0)
    acc = LiveAccount(tmp_path / "live.db", client=ex, account_address="0x" + "1" * 40)
    pos = Position.open("BTC", SHORT, 100.0, acc.balance, 0.75, 3.0, 0)  # 5 $ Position
    with pytest.raises(ValueError, match="Mindestgröße"):
        acc.open(pos)
    assert not [c for c in ex.calls if c[0] == "open"]


def test_sync_records_stop_hit_on_exchange(tmp_path):
    ex = FakeExchange(px=100.0)
    acc = LiveAccount(tmp_path / "live.db", client=ex, account_address="0x" + "1" * 40)
    acc.open(Position.open("BTC", LONG, 100.0, 1000.0, 0.75, 3.0, 0))
    ex.pos.pop("BTC")  # Stop an der Börse ausgelöst
    ex.fills = [{"coin": "BTC", "time": 5000, "px": "96.9", "dir": "Close Long"}]
    done = acc.sync(["BTC"])
    assert done[0][0] == "BTC" and done[0][2] == 96.9
    assert acc.trades()[0]["reason"] == "stop" and acc.positions() == {}


def test_gold_trades_as_paxg(tmp_path):
    ex = FakeExchange(px=2000.0)
    acc = LiveAccount(tmp_path / "live.db", client=ex, account_address="0x" + "1" * 40)
    acc.open(Position.open("GOLD", LONG, 2000.0, 100.0, 0.75, 1.5, 0, max_position_pct=60))
    assert ex.calls[1][:2] == ("open", "PAXG") and "PAXG" in ex.pos


def test_live_round_panic_and_disabled(tmp_path):
    from tradingbot.config import Config
    from tradingbot.live import live_round

    ex = FakeExchange(px=100.0)
    acc = LiveAccount(tmp_path / "live.db", client=ex, account_address="0x" + "1" * 40)
    cfg = Config(symbols={"BTC": "BTC/USD"})

    class NoMarket:
        def closed_candles(self, *a, **k):
            raise AssertionError("ausgeschaltet: keine Abfrage")

    live_round(cfg, NoMarket(), acc)  # aus und keine Positionen: nichts passiert
    acc.open(Position.open("BTC", LONG, 100.0, 1000.0, 0.75, 3.0, 0))
    acc.set_state("live:panic", "1")
    acc.set_state("live:enabled", "1")
    sent = []
    live_round(cfg, NoMarket(), acc, notify_fn=lambda a, t, m, tags=None: sent.append(t))
    assert acc.positions() == {} and acc.get_state("live:enabled") == "0" and acc.get_state("live:panic") == "0"
    assert sent == ["Echtgeld · Not-Aus ausgeführt"]


def test_agent_key_created_once_and_private(tmp_path):
    db = tmp_path / "live.db"
    assert hl.agent_address(db) is None
    a = hl.agent_address(db, create=True)
    assert a.startswith("0x") and len(a) == 42 and hl.agent_address(db, create=True) == a
    assert (hl.agent_key_path(db).stat().st_mode & 0o777) == 0o600


def test_dashboard_only_approves_this_servers_agent(tmp_path):
    from tradingbot.dashboard import hl_exchange_allowed, live_request

    agent = hl.agent_address(tmp_path / "live.db", create=True)
    sig = {"r": "0x1", "s": "0x2", "v": 27}
    action = {"type": "approveAgent", "signatureChainId": "0xa4b1", "hyperliquidChain": "Mainnet",
              "agentAddress": agent, "agentName": "tradingbot", "nonce": 5}
    body = {"action": action, "nonce": 5, "signature": sig}
    assert hl_exchange_allowed(body, "mainnet", agent)
    assert not hl_exchange_allowed(body, "mainnet", "0x" + "9" * 40)
    assert not hl_exchange_allowed(body, "testnet", agent)
    assert not hl_exchange_allowed({**body, "action": {**action, "agentName": "x"}}, "mainnet", agent)

    user = "0x" + "a" * 40
    db = str(tmp_path / "live.db")
    no = lambda path, payload, net: (200, b"[]")
    yes = lambda path, payload, net: (200, json.dumps([{"address": agent.lower(), "name": "tradingbot"}]).encode())
    assert live_request("start", {"user": user}, db, post=no)[0] == 400
    code, st = live_request("start", {"user": user}, db, post=yes)
    assert code == 200 and st["enabled"] and st["account"] == user and st["agent"] == agent
    code, st = live_request("panic", {}, db)
    assert st["panic"] and not st["enabled"]
    assert live_request("start", {"user": user, "x": 1}, db, post=yes)[0] == 400
