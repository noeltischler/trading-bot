import pytest

from tradingbot.risk import initial_stop, position_size, trail_stop
from tradingbot.strategy import LONG, SHORT


def test_initial_stop_both_sides():
    assert initial_stop(100, LONG, 1.0) == pytest.approx(99)
    assert initial_stop(100, SHORT, 1.0) == pytest.approx(101)


def test_position_size_risks_exactly_risk_pct():
    qty = position_size(10_000, 0.75, entry=100, stop=99)
    assert qty * (100 - 99) == pytest.approx(75)  # 0,75 % von 10.000


def test_position_size_zero_distance():
    assert position_size(10_000, 1, 100, 100) == 0


def test_trailing_stop_moves_up_but_never_down_for_long():
    stop = trail_stop(99, LONG, best_price=110, trail_pct=1)
    assert stop == pytest.approx(108.9)
    assert trail_stop(stop, LONG, best_price=105, trail_pct=1) == pytest.approx(108.9)


def test_trailing_stop_moves_down_but_never_up_for_short():
    stop = trail_stop(101, SHORT, best_price=90, trail_pct=1)
    assert stop == pytest.approx(90.9)
    assert trail_stop(stop, SHORT, best_price=95, trail_pct=1) == pytest.approx(90.9)
