"""Indicator base, order base, registry, store and pipeline specs."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest
from pydantic import BaseModel

from core import clock
from core.bus import EventBus
from core.clock import SimClock, set_clock
from core.contracts import (
    Candle,
    ExecMode,
    IndicatorResult,
    OrderRequest,
    OrderStatus,
    Segment,
    Side,
    Timeframe,
)
from core.indicator_base import IndicatorAgent
from core.order_base import OrderAgent
from core.pipeline import PipelineSpec
from core.registry import Registry
from core.store import Store
from tests.helpers import Collector, make_request

OPEN = datetime(2026, 9, 11, 9, 15)


@pytest.fixture
def sim_clock():
    previous = set_clock(SimClock(OPEN))
    yield clock.get_clock()
    set_clock(previous)


# -- indicator base ----------------------------------------------------------
class SmaConfig(BaseModel):
    period: int = 3


class Sma(IndicatorAgent):
    name = "sma"
    Config = SmaConfig
    outputs = ["value"]

    def warmup_bars(self) -> int:
        return self.config.period

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df["value"] = df["close"].rolling(self.config.period).mean()
        return df


def candle(minute: int, close: float, closed: bool = True) -> Candle:
    start = clock.ist(OPEN) + timedelta(minutes=5 * minute)
    return Candle("K", Timeframe.M5, start, close, close + 1, close - 1, close, 10, closed)


async def build_indicator(bus: EventBus, **kwargs) -> Sma:
    agent = Sma(
        "sma-1",
        SmaConfig(),
        bus,
        pipeline_id="p1",
        instrument_key="K",
        timeframe=Timeframe.M5,
        **kwargs,
    )
    await agent.start()
    return agent


@pytest.mark.asyncio
async def test_indicator_waits_for_warmup(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    results = Collector(bus, "indicator.p1")
    agent = await build_indicator(bus)
    for index, close in enumerate([10.0, 20.0]):
        await bus.publish_and_drain("candle.5m.K", candle(index, close))
    assert results.messages == []
    assert not agent.ready
    await bus.publish_and_drain("candle.5m.K", candle(2, 30.0))
    assert results.messages == []  # only 3 bars: the previous value is still NaN
    await bus.publish_and_drain("candle.5m.K", candle(3, 40.0))
    assert len(results.messages) == 1
    await bus.stop()


@pytest.mark.asyncio
async def test_indicator_result_carries_current_and_previous(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    results = Collector(bus, "indicator.p1")
    await build_indicator(bus)
    for index, close in enumerate([10.0, 20.0, 30.0, 40.0]):
        await bus.publish_and_drain("candle.5m.K", candle(index, close))
    result: IndicatorResult = results.last
    assert result.values["value"] == pytest.approx(30.0)  # mean(20, 30, 40)
    assert result.prev_values["value"] == pytest.approx(20.0)
    assert result.values["close"] == 40.0
    assert result.candle.is_closed and result.prev_candle.start < result.candle.start
    assert result.indicator == "sma" and result.timeframe is Timeframe.M5
    await bus.stop()


@pytest.mark.asyncio
async def test_indicator_ignores_open_candles_by_default(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    results = Collector(bus, "indicator.p1")
    await build_indicator(bus)
    for index, close in enumerate([10.0, 20.0, 30.0, 40.0]):
        await bus.publish_and_drain("candle.5m.K", candle(index, close, closed=False))
    assert results.messages == []
    await bus.stop()


@pytest.mark.asyncio
async def test_evaluate_on_every_tick_uses_open_candles(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    results = Collector(bus, "indicator.p1")
    await build_indicator(bus, evaluate_on="every_tick")
    for index, close in enumerate([10.0, 20.0, 30.0, 40.0]):
        await bus.publish_and_drain("candle.5m.K", candle(index, close, closed=False))
    assert len(results.messages) == 1
    await bus.stop()


@pytest.mark.asyncio
async def test_seeding_history_shortens_the_warmup(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    results = Collector(bus, "indicator.p1")
    agent = await build_indicator(bus)
    history = pd.DataFrame(
        {
            "open": [1.0] * 5,
            "high": [1.0] * 5,
            "low": [1.0] * 5,
            "close": [1.0] * 5,
            "volume": [0.0] * 5,
        },
        index=pd.DatetimeIndex(
            [clock.ist(OPEN) - timedelta(minutes=5 * i) for i in range(5, 0, -1)], name="start"
        ),
    )
    agent.seed(history)
    assert agent.ready
    await bus.publish_and_drain("candle.5m.K", candle(0, 4.0))
    assert len(results.messages) == 1
    await bus.stop()


@pytest.mark.asyncio
async def test_indicator_export_includes_the_output_columns(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = await build_indicator(bus)
    for index, close in enumerate([10.0, 20.0, 30.0, 40.0]):
        await bus.publish_and_drain("candle.5m.K", candle(index, close))
    frames = agent.export_frames()
    frame = frames["sma_5m"]
    assert "value" in frame.columns and "close" in frame.columns
    await bus.stop()


# -- order base --------------------------------------------------------------
class Echo(OrderAgent):
    name = "echo"

    async def place(self, req: OrderRequest):
        return self.event(req, OrderStatus.FILLED, fill_price=100.0, filled_qty=req.quantity)


class Exploding(OrderAgent):
    name = "boom"

    async def place(self, req: OrderRequest):
        raise RuntimeError("broker on fire")


@pytest.mark.asyncio
async def test_order_agent_routes_events_to_the_origin(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    events = Collector(bus, "order.event.strategy-1")
    agent = Echo("echo-1", None, bus)
    await agent.start()
    await bus.publish_and_drain("order.approved", make_request(meta={"order_agent": "echo"}))
    assert events.last.status == OrderStatus.FILLED.value
    assert events.last.origin_agent_id == "strategy-1"
    await bus.stop()


@pytest.mark.asyncio
async def test_order_agent_ignores_requests_for_other_agents(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = Echo("echo-1", None, bus)
    await agent.start()
    await bus.publish_and_drain("order.approved", make_request(meta={"order_agent": "other"}))
    assert agent.handled == 0
    await bus.stop()


@pytest.mark.asyncio
async def test_a_broker_exception_becomes_a_rejection(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    events = Collector(bus, "order.event.*")
    agent = Exploding("boom-1", None, bus)
    await agent.start()
    await bus.publish_and_drain("order.approved", make_request(meta={"order_agent": "boom"}))
    assert events.last.status == OrderStatus.REJECTED.value
    assert "broker on fire" in events.last.message
    await bus.stop()


def test_zero_quantity_is_refused() -> None:
    agent = Echo("echo-1")
    assert agent.guard(make_request(quantity=0)) is not None


def test_order_tag_fits_the_broker_limit() -> None:
    assert len(OrderAgent.tag("0123456789abcdef0123456789")) <= 20


@pytest.mark.parametrize(
    "side,target_points,expected",
    [(Side.BUY, 20.0, (120.0, 90.0)), (Side.SELL, 20.0, (80.0, 110.0))],
)
def test_resolve_targets_from_the_fill(side, target_points, expected) -> None:
    request = make_request(side=side, target_points=target_points, stoploss_points=10.0)
    assert OrderAgent.resolve_targets(request, 100.0) == expected


def test_resolve_targets_without_levels() -> None:
    request = make_request(target_points=None, stoploss_points=None)
    assert OrderAgent.resolve_targets(request, 100.0) == (None, None)


@pytest.mark.asyncio
async def test_order_events_are_recorded_in_the_store(tmp_path, sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    store = Store(tmp_path)
    agent = Echo("echo-1", None, bus, store)
    await agent.start()
    await bus.publish_and_drain("order.approved", make_request(meta={"order_agent": "echo"}))
    rows = store.db.execute("SELECT * FROM orders").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "FILLED"
    await bus.stop()


# -- registry ----------------------------------------------------------------
def test_registry_finds_every_shipped_agent() -> None:
    registry = Registry().discover()
    names = {(spec.kind, spec.name) for spec in registry.all()}
    assert ("indicator", "macd") in names
    assert ("indicator", "ichimoku") in names
    assert ("strategy", "macd_s1_cross_option") in names
    assert ("order", "paper") in names
    assert not any(name.startswith("_") for _, name in names)  # templates are skipped


def test_registry_offers_only_compatible_strategies() -> None:
    registry = Registry().discover()
    option_names = {s.name for s in registry.strategies_for("macd", "option")}
    assert "macd_s1_cross_option" in option_names
    assert "macd_s3_momentum_stock" not in option_names
    assert "ichimoku_s1_cloud_option" not in option_names
    stock_names = {s.name for s in registry.strategies_for("macd", "stock")}
    assert stock_names == {"macd_s3_momentum_stock"}


def test_registry_config_defaults_come_from_yaml() -> None:
    registry = Registry().discover()
    macd = registry.get("indicator", "macd")
    assert macd.config_defaults() == {"fast": 12, "slow": 26, "signal": 9, "source": "close"}
    assert macd.build_config({"fast": 5}).fast == 5


def test_registry_reports_unknown_agents() -> None:
    with pytest.raises(KeyError):
        Registry().discover().get("strategy", "does_not_exist")


def test_spec_requirements_are_parsed() -> None:
    spec = Registry().discover().get("strategy", "macd_s1_cross_option")
    assert spec.needs_indicator == "macd"
    assert spec.needs_segment == "option"


# -- store -------------------------------------------------------------------
def test_store_key_value_round_trip(tmp_path) -> None:
    store = Store(tmp_path)
    store.put("thing", {"a": 1})
    assert store.get("thing") == {"a": 1}
    assert store.get("missing", "fallback") == "fallback"
    store.put("thing", {"a": 2})
    assert store.get("thing") == {"a": 2}


def test_store_paths_are_sanitised(tmp_path) -> None:
    store = Store(tmp_path)
    path = store.candle_path("NSE_FO|24850CE", "5m", datetime(2026, 9, 11).date())
    assert "|" not in path.name and path.parent.name == "candles"


def test_store_filters_trades_by_session(tmp_path) -> None:
    store = Store(tmp_path)
    store.record_trade(
        {
            "trade_id": "a",
            "pipeline_id": "p",
            "strategy": "s",
            "session_date": "2026-09-11",
            "net_pnl": 10,
        }
    )
    store.record_trade(
        {
            "trade_id": "b",
            "pipeline_id": "p",
            "strategy": "s",
            "session_date": "2026-09-10",
            "net_pnl": -5,
        }
    )
    assert len(store.trades(datetime(2026, 9, 11).date())) == 1
    assert len(store.trades()) == 2


def test_store_agent_state(tmp_path) -> None:
    store = Store(tmp_path)
    assert store.load_agent_state("a1") is None
    store.save_agent_state("a1", {"bars": 10})
    assert store.load_agent_state("a1") == {"bars": 10}


# -- pipeline spec -----------------------------------------------------------
def test_paper_mode_always_routes_to_the_paper_agent() -> None:
    spec = PipelineSpec(
        underlying_key="K",
        underlying_symbol="NIFTY",
        indicator="macd",
        strategy="macd_s1_cross_option",
        order_agent="gtt",
        exec_mode=ExecMode.PAPER,
    )
    assert spec.effective_order_agent == "paper"
    spec.exec_mode = ExecMode.LIVE
    assert spec.effective_order_agent == "gtt" and spec.live


def test_pipeline_spec_round_trips_through_a_dict() -> None:
    spec = PipelineSpec(
        underlying_key="K",
        underlying_symbol="NIFTY",
        indicator="macd",
        strategy="macd_s1_cross_option",
        segment=Segment.STOCK,
    )
    restored = PipelineSpec.from_dict(spec.to_dict())
    assert restored.segment is Segment.STOCK
    assert restored.pipeline_id == spec.pipeline_id
    assert restored.name == spec.name


def test_pipeline_spec_names_itself() -> None:
    spec = PipelineSpec(
        underlying_key="K",
        underlying_symbol="NIFTY",
        indicator="macd",
        strategy="macd_s1_cross_option",
        timeframe=Timeframe.M1,
    )
    assert spec.name == "NIFTY macd_s1_cross_option 1m"
