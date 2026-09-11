"""Paper trading simulation: same interface as the live order path.

Fills happen at the last traded price plus a configurable slippage, and
target/stop-loss legs are evaluated from live ticks. Nothing here touches the
network, which is what makes replay and paper mode identical to live.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from core.contracts import Side


@dataclass
class SimulatedLeg:
    """A pending target/stop-loss pair watched on the tick stream."""

    correlation_id: str
    instrument_key: str
    side: Side  # side of the *entry*
    quantity: int
    entry_price: float
    target_price: float | None = None
    stoploss_price: float | None = None
    done: bool = False

    def check(self, ltp: float) -> tuple[str, float] | None:
        """Returns ("TARGET_HIT" | "SL_HIT", exit_price) when a leg triggers."""
        if self.done:
            return None
        if self.side is Side.BUY:
            if self.target_price is not None and ltp >= self.target_price:
                return "TARGET_HIT", self.target_price
            if self.stoploss_price is not None and ltp <= self.stoploss_price:
                return "SL_HIT", self.stoploss_price
        else:
            if self.target_price is not None and ltp <= self.target_price:
                return "TARGET_HIT", self.target_price
            if self.stoploss_price is not None and ltp >= self.stoploss_price:
                return "SL_HIT", self.stoploss_price
        return None


@dataclass
class PaperBroker:
    """Fill simulation. Slippage always works against the trader."""

    slippage_points: float = 0.0
    slippage_percent: float = 0.0
    ltp_book: dict[str, float] = field(default_factory=dict)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    def on_tick(self, instrument_key: str, ltp: float) -> None:
        self.ltp_book[instrument_key] = ltp

    def ltp(self, instrument_key: str) -> float | None:
        return self.ltp_book.get(instrument_key)

    def fill_price(self, side: Side, reference: float) -> float:
        slip = self.slippage_points + reference * self.slippage_percent / 100
        price = reference + slip if side is Side.BUY else reference - slip
        return round(max(price, 0.05), 2)

    def next_order_id(self) -> str:
        return f"PAPER-{next(self._ids):06d}"
