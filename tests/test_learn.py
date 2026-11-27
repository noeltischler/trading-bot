import numpy as np
import pandas as pd

from tradingbot import learn
from tradingbot.config import load_config
from tradingbot.dashboard import learn_action, render
from tradingbot.paper import PaperAccount
from tradingbot.strategy import LONG, SHORT, StrategyParams, signals, with_btc

DAY = 86400


def frames(cfg, n=1500, seed=1):
    rng = np.random.default_rng(seed)
    out = {}
    for coin in cfg.symbols:
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
        out[coin] = pd.DataFrame({"time": np.arange(n) * 14400 + 1_700_000_000, "open": c, "high": c * 1.008,
                                  "low": c * 0.992, "close": c, "volume": 1.0})
    return {k: with_btc(v, out["BTC"]) for k, v in out.items()}


def test_range_adx_allows_both_sides_in_sideways_market():
    # Bitcoin unter seiner EMA: ohne Seitwärts-Erkennung nur Shorts, mit ihr auch Longs
    n = 400
    rng = np.random.default_rng(3)
    c = 100 + np.cumsum(rng.normal(0, 0.6, n))  # ziellos hin und her
    btc = np.linspace(200, 100, n)               # BTC fällt: unter EMA
    df = pd.DataFrame({"time": np.arange(n) * 14400, "open": c, "high": c + 0.3, "low": c - 0.3, "close": c,
                       "volume": 1.0, "btc_close": btc})
    base = StrategyParams(kind="meanrev", rsi_period=2, rsi_low=10, rsi_high=90, btc_filter=200, trend=50)
    plain = signals(df, base)
    side = signals(df, StrategyParams(**{**base.__dict__, "range_adx": 25}))
    assert (plain == LONG).sum() == 0 and (side == LONG).sum() > 0
    assert (side == SHORT).sum() >= (plain == SHORT).sum()
    assert StrategyParams(**{**base.__dict__, "range_adx": 25}).label() == "RSI-2 10/90 ~25"


def test_candidates_stay_within_bounds_and_include_current():
    cfg = load_config("config.empfohlen.yaml")
    s = learn.settings_of(cfg)
    cands = learn.candidates(s)
    assert learn.same(cands[0], s)
    assert all(1.5 <= c["stop_pct"] <= 6 and 2 <= c["trailing_stop_pct"] <= 10 for c in cands)
    assert any(x.get("range_adx") == 20 for c in cands for x in c["strategy"])
    assert any(len(c["strategy"]) == 3 for c in cands)  # eine Strategie weggelassen
    assert 20 <= len(cands) <= 40


def test_pick_needs_better_in_both_periods():
    cur = {"train": 100, "test": 100, "test_pf": 1.3, "test_trades": 50}
    only_test = {"train": 90, "test": 400, "test_pf": 2.0, "test_trades": 50}
    both = {"train": 250, "test": 260, "test_pf": 1.4, "test_trades": 45}
    few = {"train": 900, "test": 900, "test_pf": 3.0, "test_trades": 10}
    res = [({"n": 0}, cur), ({"n": 1}, only_test), ({"n": 2}, both), ({"n": 3}, few)]
    assert learn.pick(res, 100)[0]["n"] == 2
    assert learn.pick(res[:2], 100) is None


def test_weekly_paper_first_then_proposal_accept_and_revert(tmp_path, monkeypatch):
    cfg = load_config("config.empfohlen.yaml")
    acc = PaperAccount(tmp_path / "paper.db")
    fr = frames(cfg)
    now = 1_800_000_000.0
    better = learn.candidates(learn.settings_of(cfg))[5]

    def fake_eval(cfg_, frames_, s, split, start=0, cache=None):
        good = learn.same(s, better)
        return {"train": 500 if good else 0, "test": 500 if good else 0, "test_pf": 1.5,
                "train_trades": 50, "test_trades": 50}
    monkeypatch.setattr(learn, "evaluate", fake_eval)
    sent = []
    notify = lambda a, title, text, tags: sent.append(title)  # noqa: E731

    learn.daily(cfg, acc, lambda: fr, now=now, notify_fn=notify)
    assert learn.same(learn._get(acc, "paper"), better)
    assert learn._get(acc, "live") is None                       # Echtgeld unverändert
    assert learn.effective(cfg, acc, "paper").risk.stop_pct == better["stop_pct"]
    assert learn.effective(cfg, acc, "live").risk.stop_pct == cfg.risk.stop_pct

    learn.daily(cfg, acc, lambda: fr, now=now + 3 * DAY)          # zu früh: weder Prüfung noch Vorschlag
    assert learn._get(acc, "proposal") is None and len(sent) == 1
    learn.daily(cfg, acc, lambda: fr, now=now + 15 * DAY, notify_fn=notify)
    assert learn.same(learn._get(acc, "proposal"), better) and "Vorschlag fürs Echtgeld" in sent

    # Echtgeld nur mit der richtigen Wallet
    live_db = str(tmp_path / "live.db")
    live = PaperAccount(live_db, 0.0)
    live.set_state("live:account", "0x" + "a" * 40)
    live.close_db()
    acc.close_db()
    assert learn_action("accept", str(tmp_path / "paper.db"), live_db, "0x" + "b" * 40)[0] == 403
    assert learn_action("accept", str(tmp_path / "paper.db"), live_db, "0x" + "A" * 40)[0] == 200
    acc = PaperAccount(tmp_path / "paper.db")
    assert learn.same(learn._get(acc, "live"), better) and learn._get(acc, "proposal") is None
    page = render(acc)
    assert "Selbst lernen" in page and "Zurückdrehen" in page

    learn.revert(acc, "live")
    learn.revert(acc, "paper")
    assert learn._get(acc, "live") is None and learn._get(acc, "paper") is None
    assert len(learn.history(acc)) >= 4


def test_weekly_keeps_settings_when_nothing_is_clearly_better(tmp_path):
    cfg = load_config("config.empfohlen.yaml")
    acc = PaperAccount(tmp_path / "paper.db")
    text = learn.weekly(cfg, acc, frames(cfg, n=900), 1_800_000_000.0)
    assert learn._get(acc, "last")["text"] == text
    assert text.startswith("Wochen-Prüfung") or learn._get(acc, "paper") is not None
