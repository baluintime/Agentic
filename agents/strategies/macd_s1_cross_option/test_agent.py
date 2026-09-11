from __future__ import annotations

import pytest

from agents.strategies.macd_s1_cross_option.agent import Config, MacdCrossOption
from core.contracts import Action
from core.position import Position
from tests.helpers import make_result

CASES = [
    # prev_macd, prev_signal, macd, signal, expected
    (-1.0, 0.0, 1.0, 0.0, Action.ENTER_LONG),  # cross above
    (0.0, 0.0, 1.0, 0.0, Action.ENTER_LONG),  # touching then above counts
    (1.0, 0.0, -1.0, 0.0, Action.ENTER_SHORT),  # cross below
    (0.0, 0.0, -1.0, 0.0, Action.ENTER_SHORT),
    (2.0, 1.0, 3.0, 1.5, None),  # above and staying above
    (-2.0, -1.0, -3.0, -1.5, None),  # below and staying below
    (1.0, 0.0, 1.0, 0.0, None),  # unchanged
]


@pytest.mark.parametrize("pm,ps,m,s,expected", CASES)
def test_entry_rules(pm, ps, m, s, expected) -> None:
    agent = MacdCrossOption("s1", Config())
    result = make_result({"macd": m, "signal": s}, {"macd": pm, "signal": ps})
    assert agent.decide(result, Position()) is expected


def test_reason_is_recorded() -> None:
    agent = MacdCrossOption("s1", Config())
    agent.decide(
        make_result({"macd": 1.0, "signal": 0.0}, {"macd": -1.0, "signal": 0.0}), Position()
    )
    assert "crossed above" in agent.last_reason


def test_requires_declares_indicator_and_segment() -> None:
    assert MacdCrossOption.requires == ["macd", "segment:option"]
