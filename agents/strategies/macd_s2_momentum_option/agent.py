"""MACD S2 — MACD momentum (rising/falling), traded as an ATM option.

Evaluated on closed candles by default. `min_change` adds hysteresis: without
it, a MACD hovering around its previous value flips the position on almost
every bar and the charges eat the edge (docs/DECISIONS.md, issue 5).
"""

from __future__ import annotations

from pydantic import Field

from core.contracts import Action, IndicatorResult
from core.position import Position
from core.strategy_base import StrategyAgent, StrategyConfig


class Config(StrategyConfig):
    min_change: float = Field(
        0.0, ge=0, description="Minimum MACD move before the direction counts as changed"
    )


class MacdMomentumOption(StrategyAgent):
    name = "macd_s2_momentum_option"
    description = "MACD rising -> ATM CE; falling -> ATM PE"
    Config = Config
    requires = ["macd", "segment:option"]

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
