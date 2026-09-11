"""Required tests: every entry/exit rule, duplicate-signal suppression, opposite-signal setting."""

from __future__ import annotations

from agents.strategies._template.agent import Config, TemplateStrategy
from core.position import Position
from tests.helpers import make_result


def test_returns_no_action_by_default() -> None:
    agent = TemplateStrategy("t", Config())
    assert agent.decide(make_result({}, {}), Position()) is None
