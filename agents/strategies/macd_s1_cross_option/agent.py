"""MACD S1 — MACD/signal crossover, traded as an ATM option."""

from __future__ import annotations

from core.contracts import Action, IndicatorResult
from core.position import Position
from core.strategy_base import StrategyAgent, StrategyConfig


class Config(StrategyConfig):
    """Target and stop-loss apply to the option premium, not the underlying."""


class MacdCrossOption(StrategyAgent):
    name = "macd_s1_cross_option"
    description = "MACD crosses above signal -> ATM CE; crosses below -> ATM PE"
    Config = Config
    requires = ["macd", "segment:option"]

    def decide(self, r: IndicatorResult, pos: Position) -> Action | None:
        macd, signal = r.values["macd"], r.values["signal"]
        prev_macd, prev_signal = r.prev_values["macd"], r.prev_values["signal"]
        if prev_macd <= prev_signal and macd > signal:
            self.last_reason = f"MACD {macd:.2f} crossed above signal {signal:.2f}"
            return Action.ENTER_LONG  # base class buys the ATM CE
        if prev_macd >= prev_signal and macd < signal:
            self.last_reason = f"MACD {macd:.2f} crossed below signal {signal:.2f}"
            return Action.ENTER_SHORT  # base class buys the ATM PE
        return None
