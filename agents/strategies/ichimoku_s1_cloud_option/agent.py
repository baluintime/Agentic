"""Ichimoku S1 — Span A / Span B cloud cross, traded as an ATM option.

The spans are displaced, so `span_reference` chooses which pair the rule reads:
`projected` (the Kumo twist, computed from today's bars) or `current` (the cloud
under today's price, computed 26 bars ago).
"""

from __future__ import annotations

from pydantic import Field

from core.contracts import Action, IndicatorResult
from core.position import Position
from core.strategy_base import StrategyAgent, StrategyConfig


class Config(StrategyConfig):
    span_reference: str = Field("projected", description="projected | current")


class IchimokuCloudOption(StrategyAgent):
    name = "ichimoku_s1_cloud_option"
    description = "Span A crosses Span B -> ATM CE / PE"
    Config = Config
    requires = ["ichimoku", "segment:option"]

    def spans(self, values: dict[str, float]) -> tuple[float, float]:
        suffix = "current" if self.config.span_reference == "current" else "projected"
        return values[f"span_a_{suffix}"], values[f"span_b_{suffix}"]

    def decide(self, r: IndicatorResult, pos: Position) -> Action | None:
        span_a, span_b = self.spans(r.values)
        prev_a, prev_b = self.spans(r.prev_values)
        if prev_a <= prev_b and span_a > span_b:
            self.last_reason = f"Span A crossed above Span B ({self.config.span_reference})"
            return Action.ENTER_LONG
        if prev_a >= prev_b and span_a < span_b:
            self.last_reason = f"Span A crossed below Span B ({self.config.span_reference})"
            return Action.ENTER_SHORT
        return None
