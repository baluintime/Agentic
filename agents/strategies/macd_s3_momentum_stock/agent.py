"""MACD S3 — MACD momentum on the stock itself.

Quantity is capital based: `floor(capital / price)`. Cash-segment shorts cannot
be carried overnight, so in Delivery mode the base class drops short signals and
the strategy is long-only (docs/DECISIONS.md, issue 6).
"""

from __future__ import annotations

from pydantic import Field

from core.contracts import Action, IndicatorResult
from core.position import Position
from core.strategy_base import StrategyAgent, StrategyConfig


class Config(StrategyConfig):
    capital: float = Field(100_000, gt=0, description="Rupees deployed per trade")
    min_change: float = Field(0.0, ge=0, description="Hysteresis on the MACD change")


class MacdMomentumStock(StrategyAgent):
    name = "macd_s3_momentum_stock"
    description = "MACD rising -> buy stock; falling -> short (intraday only)"
    Config = Config
    requires = ["macd", "segment:stock"]

    def decide(self, r: IndicatorResult, pos: Position) -> Action | None:
        delta = r.values["macd"] - r.prev_values["macd"]
        threshold = self.config.min_change
        if delta > threshold:
            self.last_reason = f"MACD rising by {delta:.3f}"
            return Action.ENTER_LONG
        if delta < -threshold:
            self.last_reason = f"MACD falling by {delta:.3f}"
            return Action.ENTER_SHORT
        return None
