
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
    assert trades[0]["strategy"] == "EMA 3/6"  # die auslösende Strategie wird mitgespeichert


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


def test_last_signal_names_first_strategy_with_signal():
    from tradingbot.strategy import FLAT, last_signal, signals

    closes = _uptrend_with_fresh_cross()
    df = _frame(closes)
    ema_ = _cfg().strategy
    quiet = StrategyParams(kind="breakout", lookback=500)  # zu lang, nie ein Signal
    assert last_signal(df, [quiet, ema_]) == (LONG, "EMA 3/6")
    assert last_signal(df, [quiet]) == (FLAT, "")
    assert last_signal(df, [quiet, ema_])[0] == signals(df, [quiet, ema_]).iloc[-1]


def test_old_account_gets_strategy_column_and_keeps_fingerprints(tmp_path):
    import sqlite3

    from tradingbot.anchor import GENESIS, digest, verify

    path = tmp_path / "paper.db"
    old = sqlite3.connect(path)  # Konto im alten Format ohne Spalte strategy
    old.executescript("""
        CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT NOT NULL, side INTEGER NOT NULL,
            qty REAL NOT NULL, entry REAL NOT NULL, exit REAL NOT NULL, opened_at INTEGER NOT NULL,
            closed_at INTEGER NOT NULL, pnl REAL NOT NULL, reason TEXT NOT NULL, balance REAL NOT NULL);
        INSERT INTO trades (coin, side, qty, entry, exit, opened_at, closed_at, pnl, reason, balance)
            VALUES ('BTC', 1, 1.0, 100.0, 99.0, 0, 3600, -1.0, 'stop', 9999.0);
    """)
    old_rows = [dict(zip([c[0] for c in old.execute("SELECT * FROM trades").description], r))
                for r in old.execute("SELECT * FROM trades")]
    before = digest(old_rows, "1970-01-01", GENESIS)
    old.execute("CREATE TABLE anchors (day TEXT PRIMARY KEY, hash TEXT NOT NULL, prev TEXT NOT NULL, tx_url TEXT)")
    old.execute("INSERT INTO anchors VALUES ('1970-01-01', ?, ?, NULL)", (before, GENESIS))
    old.commit()
    old.close()

    acc = PaperAccount(path)
    assert acc.trades()[0]["strategy"] == ""
    assert verify(acc) == []
    acc.open(Position.open("ETH", LONG, 100.0, acc.balance, 1.0, 1.0, 7200, strategy="RSI-2 10/90"))
    acc.close("ETH", 101.0, "stop", when=10_000)
    assert acc.trades()[-1]["strategy"] == "RSI-2 10/90"


def test_shared_account_counts_only_own_coins_for_max_positions():
    from tradingbot.live import own_open

    closes = _uptrend_with_fresh_cross()
    acc = PaperAccount(":memory:", fee_pct=0.0)
    # Gold-Bot hat schon eine Position im gemeinsamen Konto
    acc.open(Position.open("GOLD", LONG, 2000.0, acc.balance, 0.75, 1.5, 0))
    cfg = _cfg()
    cfg.risk.max_open_positions = 1
    assert own_open(acc, cfg) == 0
    step(cfg, FakeMarket(_frame(closes), price=closes[-1]), acc)
    assert set(acc.positions()) == {"GOLD", "BTC"}


def test_merge_gold_account_into_shared(tmp_path):
    from tradingbot.cli import main
    from tradingbot.paper import merge_into
    from tradingbot.storage import CandleStore

    gold = PaperAccount(tmp_path / "gold.db", fee_pct=0.0)
    gold.open(Position.open("GOLD", LONG, 2000.0, gold.balance, 0.75, 1.5, 0))
    gold.set_state("last_candle:GOLD", "123")
    gold.close_db()
    CandleStore(tmp_path / "gold.db").upsert("GOLD", "1d", _frame([1.0, 2.0]))
    main_acc = PaperAccount(tmp_path / "paper.db", fee_pct=0.0)
    main_acc.open(Position.open("BTC", LONG, 100.0, main_acc.balance, 1.0, 1.0, 0))
    main_acc.close_db()

    (tmp_path / "config.yaml").write_text(f"symbols: {{BTC: BTC/USD}}\npaper_database: {tmp_path / 'paper.db'}\n")
    (tmp_path / "gold.yaml").write_text(f"name: Gold-Bot\nsymbols: {{GOLD: PAXG/USD}}\npaper_database: {tmp_path / 'gold.db'}\n")
    args = ["--config", str(tmp_path / "gold.yaml"), "merge-account", "--into", str(tmp_path / "config.yaml")]
    assert main(args) == 0
    assert f"paper_database: {tmp_path / 'paper.db'}" in (tmp_path / "gold.yaml").read_text()
    acc = PaperAccount(tmp_path / "paper.db")
    assert set(acc.positions()) == {"BTC", "GOLD"} and acc.get_state("last_candle:GOLD") == "123"
    assert acc.balance == 10_000  # Kapital bleibt eins, nicht doppelt
    assert len(CandleStore(tmp_path / "paper.db").load("GOLD", "1d")) == 2
    assert main(args) == 0  # zweiter Lauf: nichts mehr zu tun
    assert merge_into(tmp_path / "gold.db", acc) == []  # GOLD ist schon da
