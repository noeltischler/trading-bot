import pytest

from tradingbot.config import load_config


def _write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_example_config_loads():
    cfg = load_config("config.example.yaml")
    assert cfg.symbols["BTC"] == "BTC/USD"
    assert cfg.exchange == "kraken"
    assert cfg.history_exchange == "coinbaseexchange"
    assert cfg.strategy.fast == 20
    assert 0 < cfg.risk.risk_per_trade_pct <= 1


def test_rejects_risk_above_one_percent(tmp_path):
    p = _write(tmp_path, "symbols: {BTC: BTC/USD}\nrisk: {risk_per_trade_pct: 2}\n")
    with pytest.raises(ValueError):
        load_config(p)


def test_rejects_unknown_timeframe(tmp_path):
    p = _write(tmp_path, "symbols: {BTC: BTC/USD}\ntimeframe: 7m\n")
    with pytest.raises(ValueError):
        load_config(p)


def test_strategy_list_loads_as_combination(tmp_path):
    p = _write(tmp_path, "symbols: {BTC: BTC/USD}\nstrategy:\n  - {kind: wedge, lookback: 80}\n  - {kind: sweep, lookback: 50}\n"
                         "risk: {stop_atr: 2, trail_atr: 3, daily_loss_limit_pct: 3}\n")
    cfg = load_config(p)
    assert [s.kind for s in cfg.strategy] == ["wedge", "sweep"]
    bp = cfg.backtest_params()
    assert (bp.stop_atr, bp.trail_atr, bp.daily_loss_limit_pct) == (2, 3, 3)
