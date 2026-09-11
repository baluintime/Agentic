"""Message contracts: the only types agents use to talk to each other.

Changing anything here affects every agent. Bump CONTRACTS_VERSION and update
docs/CONTRACTS.md in the same commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

CONTRACTS_VERSION = "1.0.0"


class Timeframe(str, Enum):
    TICK = "tick"
    M1 = "1m"
    M5 = "5m"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        return {"tick": 0, "1m": 60, "5m": 300, "1d": 0}[self.value]


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class Action(str, Enum):
    ENTER_LONG = "ENTER_LONG"  # buy CE / buy stock / buy future
    ENTER_SHORT = "ENTER_SHORT"  # buy PE / short stock / sell future
    EXIT = "EXIT"
    REVERSE = "REVERSE"


class Segment(str, Enum):
    OPTION = "option"
    FUTURE = "future"
    STOCK = "stock"


class Product(str, Enum):
    INTRADAY = "I"
    DELIVERY = "D"


class ExecMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


class OrderStatus(str, Enum):
    PLACED = "PLACED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    TARGET_HIT = "TARGET_HIT"
    SL_HIT = "SL_HIT"
    CANCELLED = "CANCELLED"
    SQUARED_OFF = "SQUARED_OFF"


TERMINAL_EXIT_STATUSES = frozenset(
    {OrderStatus.TARGET_HIT, OrderStatus.SL_HIT, OrderStatus.SQUARED_OFF}
)


@dataclass(frozen=True)
class Tick:
    instrument_key: str
    ts: datetime
    ltp: float
    cum_volume: int | None = None
    oi: float | None = None


@dataclass(frozen=True)
class Candle:
    instrument_key: str
    timeframe: Timeframe
    start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    is_closed: bool = False


@dataclass(frozen=True)
class IndicatorResult:
    pipeline_id: str
    indicator: str
    timeframe: Timeframe
    candle: Candle
    prev_candle: Candle
    values: dict[str, float]
    prev_values: dict[str, float]


@dataclass(frozen=True)
class Signal:
    pipeline_id: str
    strategy_id: str
    action: Action
    reason: str
    ts: datetime


@dataclass(frozen=True)
class OrderRequest:
    correlation_id: str
    origin_agent_id: str
    pipeline_id: str
    instrument_key: str
    side: Side
    quantity: int
    product: str = Product.INTRADAY.value
    order_type: str = "MARKET"
    target_points: float | None = None
    stoploss_points: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderEvent:
    correlation_id: str
    origin_agent_id: str
    status: str
    fill_price: float | None
    filled_qty: int
    broker_order_id: str | None
    ts: datetime
    message: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Instrument:
    """A resolved tradable instrument, produced by the Instrument Master."""

    instrument_key: str
    tradingsymbol: str
    segment: Segment
    lot_size: int = 1
    tick_size: float = 0.05
    freeze_quantity: int | None = None
    expiry: datetime | None = None
    strike: float | None = None
    option_type: OptionType | None = None
    underlying_key: str | None = None

    @property
    def label(self) -> str:
        """Human label. Broker trading symbols already carry strike and expiry."""
        if self.option_type and self.strike and f"{self.strike:g}" not in self.tradingsymbol:
            exp = self.expiry.strftime("%d%b%y").upper() if self.expiry else ""
            return f"{self.tradingsymbol} {self.strike:g}{self.option_type.value} {exp}".strip()
        return self.tradingsymbol


@dataclass(frozen=True)
class SystemEvent:
    """Anything published on a `system.*` topic."""

    kind: str  # squareoff.no_new_entries | squareoff.start | kill | health | token | risk
    ts: datetime
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


# --- Topic helpers -----------------------------------------------------------
# tick.<instrument>            candle.<tf>.<instrument>      indicator.<pipeline>
# signal.<pipeline>            order.request                 order.approved
# order.event.<origin_agent>   system.<kind>


def tick_topic(instrument_key: str) -> str:
    return f"tick.{instrument_key}"


def candle_topic(timeframe: Timeframe | str, instrument_key: str) -> str:
    tf = timeframe.value if isinstance(timeframe, Timeframe) else timeframe
    return f"candle.{tf}.{instrument_key}"


def indicator_topic(pipeline_id: str) -> str:
    return f"indicator.{pipeline_id}"


def signal_topic(pipeline_id: str) -> str:
    return f"signal.{pipeline_id}"


ORDER_REQUEST_TOPIC = "order.request"
ORDER_APPROVED_TOPIC = "order.approved"


def order_event_topic(origin_agent_id: str) -> str:
    return f"order.event.{origin_agent_id}"


def system_topic(kind: str) -> str:
    return f"system.{kind}"
