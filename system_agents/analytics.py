"""Analytics: win rate, profit factor, drawdown and expectancy per strategy.

Pure arithmetic over the trade log, so it works identically on a replayed
session and on a live one. This is what the paper-first promotion rule reads
before a strategy is allowed anywhere near Live.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel, Field

from core import clock
from core.base_agent import BaseAgent


@dataclass
class Stats:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    gross: float = 0.0
    charges: float = 0.0
    net: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    max_drawdown: float = 0.0
    best: float = 0.0
    worst: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AnalyticsConfig(BaseModel):
    min_paper_sessions: int = Field(
        5, ge=0, description="Paper sessions required before Live is unlocked"
    )


class AnalyticsAgent(BaseAgent):
    kind: ClassVar[str] = "system"
    name: ClassVar[str] = "analytics"
    Config: ClassVar[type[BaseModel]] = AnalyticsConfig
    description: ClassVar[str] = "Win rate, profit factor, drawdown and expectancy"

    def trades(self, session_date=None) -> list[dict]:
        return self.store.trades(session_date) if self.store else []

    def stats(self, session_date=None) -> Stats:
        return compute(self.trades(session_date))

    def by_strategy(self, session_date=None) -> dict[str, Stats]:
        grouped: dict[str, list[dict]] = {}
        for trade in self.trades(session_date):
            grouped.setdefault(trade.get("strategy", "unknown"), []).append(trade)
        return {name: compute(rows) for name, rows in grouped.items()}

    def paper_sessions(self, strategy: str) -> int:
        """How many distinct sessions this strategy has already traded on paper."""
        days = {
            trade.get("session_date")
            for trade in self.trades()
            if trade.get("strategy") == strategy and trade.get("mode") == "paper"
        }
        return len({day for day in days if day})

    def live_unlocked(self, strategy: str) -> tuple[bool, str]:
        """The paper-first promotion rule."""
        required = int(getattr(self.config, "min_paper_sessions", 0))
        done = self.paper_sessions(strategy)
        if done >= required:
            return True, f"{done} paper session(s) recorded"
        return False, f"{done} of {required} paper sessions recorded"

    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update({"today": self.stats(clock.now().date()).to_dict()})
        return base

    def export_frames(self) -> dict[str, pd.DataFrame]:
        per_strategy = self.by_strategy()
        if not per_strategy:
            return {}
        rows = [{"strategy": name, **stats.to_dict()} for name, stats in per_strategy.items()]
        return {"analytics": pd.DataFrame(rows)}


def compute(trades: Iterable[dict]) -> Stats:
    rows = [t for t in trades if t.get("net_pnl") is not None]
    if not rows:
        return Stats()
    nets = [float(t["net_pnl"]) for t in rows]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    profit, loss = sum(wins), abs(sum(losses))
    equity, peak, drawdown = 0.0, 0.0, 0.0
    for net in nets:
        equity += net
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    win_rate = len(wins) / len(nets)
    average_win = profit / len(wins) if wins else 0.0
    average_loss = loss / len(losses) if losses else 0.0
    return Stats(
        trades=len(nets),
        wins=len(wins),
        losses=len(losses),
        win_rate=round(100 * win_rate, 1),
        gross=round(sum(float(t.get("gross_pnl") or 0) for t in rows), 2),
        charges=round(sum(float(t.get("charges") or 0) for t in rows), 2),
        net=round(sum(nets), 2),
        average_win=round(average_win, 2),
        average_loss=round(average_loss, 2),
        # Infinite profit factor is meaningless in a UI; report the gross profit instead.
        profit_factor=round(profit / loss, 2) if loss else round(profit, 2),
        expectancy=round(win_rate * average_win - (1 - win_rate) * average_loss, 2),
        max_drawdown=round(drawdown, 2),
        best=round(max(nets), 2),
        worst=round(min(nets), 2),
    )
