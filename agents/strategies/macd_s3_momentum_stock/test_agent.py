from __future__ import annotations

import pytest

from agents.strategies.macd_s3_momentum_stock.agent import Config, MacdMomentumStock
from core.contracts import Action, Segment
from core.position import Position
from tests.helpers import make_instrument, make_result


@pytest.mark.parametrize(
    "prev,now,expected",
    [(1.0, 1.5, Action.ENTER_LONG), (1.5, 1.0, Action.ENTER_SHORT), (1.0, 1.0, None)],
)
def test_momentum_rules(prev, now, expected) -> None:
    agent = MacdMomentumStock("s3", Config())
    result = make_result({"macd": now, "signal": 0.0}, {"macd": prev, "signal": 0.0})
    assert agent.decide(result, Position()) is expected


def test_capital_sizing_is_floor_of_capital_over_price() -> None:
    agent = MacdMomentumStock("s3", Config(capital=100_000))
    stock = make_instrument("NSE_EQ|INE002A01018", lot_size=1, segment=Segment.STOCK, freeze=None)
    assert agent.size(stock, 1387.0) == 72  # floor(100000 / 1387)
    assert agent.size(stock, 0.0) == 0


def test_requires_stock_segment() -> None:
    assert "segment:stock" in MacdMomentumStock.requires
