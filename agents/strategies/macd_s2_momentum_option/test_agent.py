from __future__ import annotations

import pytest

from agents.strategies.macd_s2_momentum_option.agent import Config, MacdMomentumOption
from core.contracts import Action
from core.position import Position
from tests.helpers import make_result

CASES = [
    (1.0, 2.0, 0.0, Action.ENTER_LONG),
    (2.0, 1.0, 0.0, Action.ENTER_SHORT),
    (1.0, 1.0, 0.0, None),
    (1.0, 1.05, 0.1, None),  # below the hysteresis threshold
    (1.0, 1.2, 0.1, Action.ENTER_LONG),
    (1.0, 0.8, 0.1, Action.ENTER_SHORT),
]


@pytest.mark.parametrize("prev,now,min_change,expected", CASES)
def test_momentum_rules(prev, now, min_change, expected) -> None:
    agent = MacdMomentumOption("s2", Config(min_change=min_change))
    result = make_result({"macd": now, "signal": 0.0}, {"macd": prev, "signal": 0.0})
    assert agent.decide(result, Position()) is expected


def test_default_is_reverse_on_opposite_signal() -> None:
    from core.registry import Registry

    spec = Registry().discover().get("strategy", "macd_s2_momentum_option")
    assert spec.build_config().on_opposite_signal == "reverse"
