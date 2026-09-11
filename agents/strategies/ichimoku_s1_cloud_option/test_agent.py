from __future__ import annotations

import pytest

from agents.strategies.ichimoku_s1_cloud_option.agent import Config, IchimokuCloudOption
from core.contracts import Action
from core.position import Position
from tests.helpers import make_result


def spans(a_prev, b_prev, a_now, b_now, suffix="projected"):
    values = {f"span_a_{suffix}": a_now, f"span_b_{suffix}": b_now}
    prev = {f"span_a_{suffix}": a_prev, f"span_b_{suffix}": b_prev}
    other = "current" if suffix == "projected" else "projected"
    values[f"span_a_{other}"] = values[f"span_b_{other}"] = 0.0
    prev[f"span_a_{other}"] = prev[f"span_b_{other}"] = 0.0
    return make_result(values, prev, indicator="ichimoku")


CASES = [
    (99.0, 100.0, 101.0, 100.0, Action.ENTER_LONG),
    (100.0, 100.0, 101.0, 100.0, Action.ENTER_LONG),
    (101.0, 100.0, 99.0, 100.0, Action.ENTER_SHORT),
    (100.0, 100.0, 99.0, 100.0, Action.ENTER_SHORT),
    (102.0, 100.0, 103.0, 100.0, None),
    (98.0, 100.0, 97.0, 100.0, None),
]


@pytest.mark.parametrize("pa,pb,a,b,expected", CASES)
def test_cloud_cross_rules(pa, pb, a, b, expected) -> None:
    agent = IchimokuCloudOption("i1", Config())
    assert agent.decide(spans(pa, pb, a, b), Position()) is expected


def test_current_span_reference_reads_the_other_pair() -> None:
    agent = IchimokuCloudOption("i1", Config(span_reference="current"))
    result = spans(99.0, 100.0, 101.0, 100.0, suffix="current")
    assert agent.decide(result, Position()) is Action.ENTER_LONG
    projected_only = spans(99.0, 100.0, 101.0, 100.0, suffix="projected")
    assert agent.decide(projected_only, Position()) is None
