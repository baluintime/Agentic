# Contracts and skeletons (design reference)

> Once Phase 1 is built, `core/contracts.py` and `core/*_base.py` are the source of truth. If they differ from this file, the code wins; update this file in the same commit.

## Message contracts (`core/contracts.py`)

These are the only types agents use to talk to each other. Keeping them in one small file is what lets future agents (written by Claude or by hand) communicate without reading the rest of the code.

```python
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

class Timeframe(str, Enum):
    TICK = "tick"; M1 = "1m"; M5 = "5m"; D1 = "1d"

class Side(str, Enum):
    BUY = "BUY"; SELL = "SELL"

class Action(str, Enum):
    ENTER_LONG = "ENTER_LONG"    # buy CE / buy stock / buy future
    ENTER_SHORT = "ENTER_SHORT"  # buy PE / short stock / sell future
    EXIT = "EXIT"
    REVERSE = "REVERSE"

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
    open: float; high: float; low: float; close: float
    volume: int = 0
    is_closed: bool = False

@dataclass(frozen=True)
class IndicatorResult:
    pipeline_id: str
    indicator: str
    timeframe: Timeframe
    candle: Candle
    prev_candle: Candle
    values: dict[str, float]       # e.g. {"macd": .., "signal": .., "histogram": ..}
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
    product: str                   # "I" intraday, "D" delivery
    order_type: str = "MARKET"
    target_points: float | None = None
    stoploss_points: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class OrderEvent:
    correlation_id: str
    origin_agent_id: str
    status: str                    # PLACED | FILLED | PARTIAL | REJECTED | TARGET_HIT | SL_HIT | CANCELLED | SQUARED_OFF
    fill_price: float | None
    filled_qty: int
    broker_order_id: str | None
    ts: datetime
    message: str = ""
```

**Bus topics:** `tick.<instrument>`, `candle.<tf>.<instrument>`, `indicator.<pipeline>`, `signal.<pipeline>`, `order.request`, `order.event.<origin_agent>`, `system.*` (square-off, kill switch, health, token).

## Agent skeletons

### Base agent (`core/base_agent.py`)

```python
from abc import ABC
from typing import ClassVar
import pandas as pd
from pydantic import BaseModel

class BaseAgent(ABC):
    kind: ClassVar[str]                  # "indicator" | "strategy" | "order" | "data" | "system"
    name: ClassVar[str]                  # unique registry name, e.g. "macd"
    Config: ClassVar[type[BaseModel]]    # parameter schema -> auto-generates the UI form
    requires: ClassVar[list[str]] = []   # e.g. ["macd"] or ["segment:option"]

    def __init__(self, agent_id: str, config: BaseModel, bus, store):
        self.agent_id, self.config, self.bus, self.store = agent_id, config, bus, store

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> dict: return {}                         # shown in the UI widget
    def export_frames(self) -> dict[str, pd.DataFrame]: return {}
```

The `Config` pydantic model does double duty: it validates parameters and the UI generates the widget form from it, so a new agent gets its UI for free.

### Indicator skeleton (`agents/indicators/_template/agent.py`)

```python
import pandas as pd
from pydantic import BaseModel
from core.indicator_base import IndicatorAgent

class Config(BaseModel):
    fast: int = 12
    slow: int = 26
    signal: int = 9

class MacdAgent(IndicatorAgent):
    name = "macd"
    Config = Config
    outputs = ["macd", "signal", "histogram"]

    def warmup_bars(self) -> int:
        return 3 * self.config.slow + self.config.signal

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Pure function: receives OHLCV candles, returns df with output columns added."""
        fast = df["close"].ewm(span=self.config.fast, adjust=False).mean()
        slow = df["close"].ewm(span=self.config.slow, adjust=False).mean()
        df["macd"] = fast - slow
        df["signal"] = df["macd"].ewm(span=self.config.signal, adjust=False).mean()
        df["histogram"] = df["macd"] - df["signal"]
        return df
```

The `IndicatorAgent` base class does everything else: subscribes to the attached candle agent, calls `compute` on each closed candle, builds `IndicatorResult` with current and previous values, publishes it, and exports.

### Strategy skeleton (`agents/strategies/_template/agent.py`)

```python
from pydantic import BaseModel
from core.contracts import Action, IndicatorResult
from core.strategy_base import StrategyAgent, Position

class Config(BaseModel):
    target_points: float = 20
    stoploss_points: float = 10
    lots: int = 1
    on_opposite_signal: str = "exit"    # exit | reverse | ignore

class MacdCrossOption(StrategyAgent):
    name = "macd_s1_cross_option"
    Config = Config
    requires = ["macd", "segment:option"]

    def decide(self, r: IndicatorResult, pos: Position) -> Action | None:
        """Only the trading rule lives here. Sizing, ATM resolution, risk, orders,
        logging, P&L and square-off are handled by StrategyAgent."""
        m, s = r.values["macd"], r.values["signal"]
        pm, ps = r.prev_values["macd"], r.prev_values["signal"]
        if pm <= ps and m > s:
            return Action.ENTER_LONG      # base class maps to ATM CE for options
        if pm >= ps and m < s:
            return Action.ENTER_SHORT     # base class maps to ATM PE for options
        return None
```

### Order agent skeleton (`agents/orders/_template/agent.py`)

```python
from core.contracts import OrderRequest, OrderEvent
from core.order_base import OrderAgent

class NormalOrderAgent(OrderAgent):
    name = "normal"

    async def place(self, req: OrderRequest) -> OrderEvent:
        """Send to broker adapter, wait for fill, return FILLED/REJECTED event.
        The base class routes the event to req.origin_agent_id."""
        ...
```

### Agent folder convention

```
agents/strategies/macd_s1_cross_option/
    agent.py          # the agent class (target < 200 lines)
    manifest.yaml     # name, kind, version, requires, short description
    config.yaml       # default parameters
    test_agent.py     # tests using fixture candles, no network
    README.md         # 10–20 lines: what it does, rules, parameters
```

Agents are discovered automatically by scanning `agents/*/*/manifest.yaml`, so adding a folder is enough to make a new agent appear in the UI.

