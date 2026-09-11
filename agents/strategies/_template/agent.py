"""One-line description of the trading rule."""

from __future__ import annotations

from core.contracts import Action, IndicatorResult
from core.position import Position
from core.strategy_base import StrategyAgent, StrategyConfig


class Config(StrategyConfig):
    """Standard fields come from StrategyConfig; add strategy-specific ones here."""


class TemplateStrategy(StrategyAgent):
    name = "_template"
    description = "Describe the rule in one line"
    Config = Config
    requires: list[str] = []

    def decide(self, r: IndicatorResult, pos: Position) -> Action | None:
        """Only the trading rule. Sizing, orders, logging and P&L are in the base class."""
        return None
