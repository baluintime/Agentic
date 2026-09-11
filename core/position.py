"""Position state machine used by every strategy agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from core.contracts import Instrument, Side


class PositionState(str, Enum):
    FLAT = "FLAT"
    ENTERING = "ENTERING"
    OPEN = "OPEN"
    EXITING = "EXITING"


class Direction(str, Enum):
    """The market view. For options both views are BUY orders (CE / PE)."""

    LONG = "long"
    SHORT = "short"

    @property
    def opposite(self) -> Direction:
        return Direction.SHORT if self is Direction.LONG else Direction.LONG


@dataclass
class Position:
    state: PositionState = PositionState.FLAT
    direction: Direction | None = None
    side: Side | None = None  # the side of the *entry* order
    instrument: Instrument | None = None
    quantity: int = 0
    filled_qty: int = 0
    entry_price: float | None = None
    entry_time: datetime | None = None
    ltp: float | None = None
    target_price: float | None = None
    stoploss_price: float | None = None
    underlying_at_entry: float | None = None
    entry_correlation_id: str | None = None
    exit_correlation_id: str | None = None
    broker_order_ids: list[str] = field(default_factory=list)
    indicator_snapshot: dict[str, float] = field(default_factory=dict)

    @property
    def is_flat(self) -> bool:
        return self.state is PositionState.FLAT

    @property
    def is_open(self) -> bool:
        return self.state is PositionState.OPEN

    @property
    def is_busy(self) -> bool:
        return self.state in (PositionState.ENTERING, PositionState.EXITING)

    def unrealised(self, ltp: float | None = None) -> float:
        """Gross MTM in rupees. A long option position gains when premium rises."""
        price = ltp if ltp is not None else self.ltp
        if not self.is_open or price is None or self.entry_price is None:
            return 0.0
        sign = 1 if self.side is Side.BUY else -1
        return sign * (price - self.entry_price) * self.filled_qty

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]

    def snapshot(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "direction": self.direction.value if self.direction else None,
            "instrument": self.instrument.label if self.instrument else None,
            "quantity": self.filled_qty or self.quantity,
            "entry_price": self.entry_price,
            "ltp": self.ltp,
            "target": self.target_price,
            "stoploss": self.stoploss_price,
            "mtm": round(self.unrealised(), 2),
        }
