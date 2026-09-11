# Contracts and skeletons

> `core/contracts.py` and `core/*_base.py` are the source of truth. This file
> describes them; if the two ever differ, the code wins and this file is updated
> in the same commit. `CONTRACTS_VERSION` is currently `1.0.0`.

## Message contracts (`core/contracts.py`)

These are the only types agents use to talk to each other.

### Enums

| Enum | Values |
|------|--------|
| `Timeframe` | `tick`, `1m`, `5m`, `1d` (`.seconds` gives the bucket length) |
| `Side` | `BUY`, `SELL` (`.opposite`) |
| `Action` | `ENTER_LONG`, `ENTER_SHORT`, `EXIT`, `REVERSE` |
| `Segment` | `option`, `future`, `stock` |
| `Product` | `I` (intraday), `D` (delivery/overnight) |
| `ExecMode` | `paper`, `live` |
| `OptionType` | `CE`, `PE` |
| `OrderStatus` | `PLACED`, `FILLED`, `PARTIAL`, `REJECTED`, `TARGET_HIT`, `SL_HIT`, `CANCELLED`, `SQUARED_OFF` |

### Dataclasses

```python
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
    values: dict[str, float]  # outputs + open/high/low/close
    prev_values: dict[str, float]  # the same keys, one bar back


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
    product: str = "I"
    order_type: str = "MARKET"
    target_points: float | None = None
    stoploss_points: float | None = None
    meta: dict[str, Any] = {}  # order_agent, exec_mode, segment, purpose,
    # lot_size, freeze_quantity, target_mode, target_value, ...


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
    meta: dict[str, Any] = {}


@dataclass(frozen=True)
class Instrument:  # produced by the Instrument Master
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


@dataclass(frozen=True)
class SystemEvent:  # anything on a `system.*` topic
    kind: str
    ts: datetime
    message: str = ""
    payload: dict[str, Any] = {}
```

An `IndicatorResult` is published only when every value **and** every previous
value is a real number, so a strategy never compares against a NaN.

## Bus topics

| Topic | Carries | Published by |
|-------|---------|--------------|
| `tick.<instrument>` | `Tick` | Market Data Hub, Replay agent |
| `candle.<tf>.<instrument>` | `Candle` (updates and one closed event) | Candle agents |
| `indicator.<pipeline>` | `IndicatorResult` | Indicator agents |
| `signal.<pipeline>` | `Signal` | Strategy agents |
| `order.request` | `OrderRequest` | Strategy agents |
| `order.approved` | `OrderRequest` | **Risk Agent only** |
| `order.event.<origin_agent>` | `OrderEvent` | Order agents, Risk Agent (rejections) |
| `system.<kind>` | `SystemEvent` | Square-off, risk, health, feed, session |

The Risk Agent is the only bridge from `order.request` to `order.approved`, which
is what makes it impossible for a strategy to reach an order agent unchecked.
Order agents handle a request only when `meta["order_agent"]` names them.

System kinds in use: `squareoff.no_new_entries`, `squareoff.cancel_gtt`,
`squareoff.start`, `squareoff.done`, `kill`, `risk.block`, `health.*`,
`feed.disconnected`, `session.new_day`.

Wildcards: `*` matches one segment, and a trailing `*` matches the rest — so
`candle.5m.*` matches one instrument and `system.*` matches `system.squareoff.done`.

## Agent skeletons

### Base agent (`core/base_agent.py`)

```python
class BaseAgent(ABC):
    kind: ClassVar[str]  # "indicator" | "strategy" | "order" | "data" | "system"
    name: ClassVar[str]  # unique registry name, e.g. "macd"
    Config: ClassVar[type[BaseModel]]  # parameter schema -> auto-generates the UI form
    requires: ClassVar[list[str]] = []  # e.g. ["macd"] or ["segment:option"]

    def __init__(self, agent_id, config=None, bus=None, store=None, pipeline_id=""): ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def listen(self, pattern, handler): ...  # auto-unsubscribed on stop
    async def emit(self, topic, message): ...
    def status(self) -> dict: ...  # shown in the UI widget
    def export_frames(self) -> dict[str, pd.DataFrame]: ...
```

### Indicator (`core/indicator_base.py`)

Author writes `warmup_bars()` and a pure `compute(df) -> df`; the base class
subscribes to the candle agent, evaluates on closed candles (or every tick when
`evaluate_on="every_tick"`), attaches previous values, publishes and exports.
`seed(history)` loads the morning history.

```python
class MacdAgent(IndicatorAgent):
    name = "macd"
    Config = Config
    outputs = ["macd", "signal", "histogram"]

    def warmup_bars(self) -> int:
        return 3 * self.config.slow + self.config.signal

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        fast = df["close"].ewm(span=self.config.fast, adjust=False).mean()
        slow = df["close"].ewm(span=self.config.slow, adjust=False).mean()
        df["macd"] = fast - slow
        df["signal"] = df["macd"].ewm(span=self.config.signal, adjust=False).mean()
        df["histogram"] = df["macd"] - df["signal"]
        return df
```

### Strategy (`core/strategy_base.py`)

Author writes only `decide(result, pos) -> Action | None`. `StrategyConfig`
supplies `target_points`, `stoploss_points`, `target_mode`, `lots`, `capital`,
`on_opposite_signal` and `evaluate_on`; subclasses add their own fields.

```python
class MacdCrossOption(StrategyAgent):
    name = "macd_s1_cross_option"
    Config = Config
    requires = ["macd", "segment:option"]

    def decide(self, r: IndicatorResult, pos: Position) -> Action | None:
        m, s = r.values["macd"], r.values["signal"]
        pm, ps = r.prev_values["macd"], r.prev_values["signal"]
        if pm <= ps and m > s:
            return Action.ENTER_LONG  # base class maps to ATM CE for options
        if pm >= ps and m < s:
            return Action.ENTER_SHORT  # base class maps to ATM PE for options
        return None
```

The base class does instrument resolution, sizing, order routing through Risk,
the `FLAT → ENTERING → OPEN → EXITING → FLAT` state machine, one decision per
candle, `on_opposite_signal`, the trade log, charges, P&L, pause/kill/square-off.

### Order agent (`core/order_base.py`)

```python
class NormalOrderAgent(OrderAgent):
    name = "normal"
    live_only = True  # refuses unless the pipeline is armed LIVE

    async def place(self, req: OrderRequest) -> OrderEvent: ...
```

The base class subscribes to `order.approved`, applies the guards, routes every
event to `order.event.<origin_agent_id>`, and provides `tag()` (correlation id
for the broker order tag), `slices()` (freeze-quantity splitting),
`resolve_targets()` (absolute target/SL from the actual fill) and `await_fill()`
(polling, never re-placing).

### Agent folder convention

```
agents/strategies/macd_s1_cross_option/
    agent.py          # the agent class (target < 200 lines)
    manifest.yaml     # name, kind, version, requires, description
    config.yaml       # default parameters
    test_agent.py     # tests using fixture candles, no network
    README.md         # 10–20 lines: what it does, rules, parameters
```

Agents are discovered by scanning `agents/*/*/manifest.yaml`, so adding a folder
is enough to make a new agent appear in the UI. Folders starting with `_` (the
templates) are skipped.
