"""Base-class behaviour every strategy inherits: state machine, sizing, exits."""

from __future__ import annotations

from datetime import timedelta

import pytest

from core import clock
from core.bus import EventBus
from core.contracts import (
    Action,
    ExecMode,
    OrderEvent,
    OrderRequest,
    OrderStatus,
    Product,
    Segment,
    Side,
    SystemEvent,
    Timeframe,
    order_event_topic,
)
from core.pipeline import PipelineSpec
from core.position import Direction, Position, PositionState
from core.store import Store
from core.strategy_base import StrategyAgent, StrategyConfig
from tests.helpers import SESSION_START, Collector, FakeResolver, make_instrument, make_result


class Scripted(StrategyAgent):
    """A strategy whose decisions the test supplies directly."""

    name = "scripted"
    Config = StrategyConfig
    requires = ["macd", "segment:option"]

    def __init__(self, *args, actions=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.actions = list(actions or [])

    def decide(self, r, pos):
        return self.actions.pop(0) if self.actions else None


def spec(**overrides) -> PipelineSpec:
    values = dict(
        pipeline_id="p1",
        underlying_key="NSE_INDEX|Nifty 50",
        underlying_symbol="NIFTY",
        indicator="macd",
        strategy="scripted",
        segment=Segment.OPTION,
        product=Product.INTRADAY,
        timeframe=Timeframe.M5,
        exec_mode=ExecMode.PAPER,
    )
    values.update(overrides)
    return PipelineSpec(**values)


async def build(bus: EventBus, actions=None, config=None, pipeline=None, resolver=None):
    agent = Scripted(
        "scripted-1",
        config or StrategyConfig(),
        bus,
        None,
        spec=pipeline or spec(),
        resolver=resolver or FakeResolver(),
        actions=actions,
    )
    await agent.start()
    return agent


async def fill(
    bus: EventBus,
    agent: StrategyAgent,
    price: float,
    quantity: int = 75,
    status: OrderStatus = OrderStatus.FILLED,
    correlation_id: str | None = None,
):
    correlation_id = correlation_id or agent.position.entry_correlation_id
    await bus.publish_and_drain(
        order_event_topic(agent.agent_id),
        OrderEvent(
            correlation_id, agent.agent_id, status.value, price, quantity, "B1", clock.now()
        ),
    )


@pytest.mark.asyncio
async def test_entry_request_is_sized_and_routed_through_risk() -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    agent = await build(bus, actions=[Action.ENTER_LONG])
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    request: OrderRequest = requests.last
    assert request.quantity == 75  # one lot
    assert request.side is Side.BUY  # a bullish option view is a CE *buy*
    assert request.meta["order_agent"] == "paper"
    assert request.meta["purpose"] == "entry"
    assert agent.position.state is PositionState.ENTERING
    await bus.stop()


@pytest.mark.asyncio
async def test_fill_opens_the_position_and_sets_target_and_stop() -> None:
    bus = EventBus()
    await bus.start()
    agent = await build(
        bus,
        actions=[Action.ENTER_LONG],
        config=StrategyConfig(target_points=20, stoploss_points=10),
    )
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    await fill(bus, agent, 104.0)
    assert agent.position.state is PositionState.OPEN
    assert agent.position.entry_price == 104.0
    assert agent.position.target_price == 124.0  # from the fill, not the reference price
    assert agent.position.stoploss_price == 94.0
    await bus.stop()


@pytest.mark.asyncio
async def test_percent_targets_are_passed_to_the_order_agent() -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    await build(
        bus,
        actions=[Action.ENTER_LONG],
        config=StrategyConfig(target_mode="percent", target_points=10, stoploss_points=5),
    )
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    assert requests.last.meta["target_mode"] == "percent"
    assert requests.last.meta["target_value"] == 10
    await bus.stop()


@pytest.mark.asyncio
async def test_one_decision_per_candle() -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    await build(bus, actions=[Action.ENTER_LONG, Action.ENTER_LONG])
    result = make_result({"macd": 1}, {"macd": 0})
    await bus.publish_and_drain("indicator.p1", result)
    await bus.publish_and_drain("indicator.p1", result)  # same candle again
    assert len(requests.messages) == 1
    await bus.stop()


@pytest.mark.asyncio
async def test_same_direction_signal_does_not_pyramid() -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    agent = await build(bus, actions=[Action.ENTER_LONG, Action.ENTER_LONG])
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    await fill(bus, agent, 100.0)
    later = make_result({"macd": 2}, {"macd": 1}, start=SESSION_START + timedelta(minutes=5))
    await bus.publish_and_drain("indicator.p1", later)
    assert len(requests.messages) == 1
    assert agent.position.state is PositionState.OPEN
    await bus.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected_requests,expected_state",
    [
        ("exit", 2, PositionState.FLAT),
        ("ignore", 1, PositionState.OPEN),
        ("reverse", 3, PositionState.ENTERING),
    ],
)
async def test_opposite_signal_setting(mode, expected_requests, expected_state) -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    agent = await build(
        bus,
        actions=[Action.ENTER_LONG, Action.ENTER_SHORT],
        config=StrategyConfig(on_opposite_signal=mode),
    )
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    await fill(bus, agent, 100.0)
    later = make_result({"macd": -1}, {"macd": 1}, start=SESSION_START + timedelta(minutes=5))
    await bus.publish_and_drain("indicator.p1", later)
    if mode != "ignore":
        await fill(bus, agent, 110.0, correlation_id=agent.position.exit_correlation_id)
    assert len(requests.messages) == expected_requests
    assert agent.position.state is expected_state
    await bus.stop()


@pytest.mark.asyncio
async def test_reverse_waits_for_the_exit_fill_before_entering() -> None:
    """The opposite entry must not overwrite a position that is still closing."""
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    agent = await build(
        bus,
        actions=[Action.ENTER_LONG, Action.ENTER_SHORT],
        config=StrategyConfig(on_opposite_signal="reverse"),
    )
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    await fill(bus, agent, 100.0)
    later = make_result({"macd": -1}, {"macd": 1}, start=SESSION_START + timedelta(minutes=5))
    await bus.publish_and_drain("indicator.p1", later)

    # only the exit has gone out so far
    assert len(requests.messages) == 2
    assert requests.last.meta["purpose"] == "exit"
    assert agent.position.state is PositionState.EXITING

    await fill(bus, agent, 110.0, correlation_id=agent.position.exit_correlation_id)
    assert len(agent.trades) == 1  # the long round trip was recorded
    assert agent.trades.rows[0].exit_reason == "reverse"
    assert requests.messages[-1].meta["purpose"] == "entry"
    assert agent.position.state is PositionState.ENTERING
    assert agent.position.direction is Direction.SHORT
    await bus.stop()


@pytest.mark.asyncio
async def test_squareoff_during_a_reversal_cancels_the_queued_entry() -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    agent = await build(
        bus,
        actions=[Action.ENTER_LONG, Action.ENTER_SHORT],
        config=StrategyConfig(on_opposite_signal="reverse"),
    )
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    await fill(bus, agent, 100.0)
    later = make_result({"macd": -1}, {"macd": 1}, start=SESSION_START + timedelta(minutes=5))
    await bus.publish_and_drain("indicator.p1", later)
    await bus.publish_and_drain(
        "system.squareoff.start", SystemEvent("squareoff.start", clock.now(), "cut-off")
    )
    await fill(bus, agent, 110.0, correlation_id=agent.position.exit_correlation_id)
    assert agent.position.is_flat
    assert all(m.meta["purpose"] != "entry" for m in requests.messages[1:])
    await bus.stop()


@pytest.mark.asyncio
async def test_exit_records_a_trade_with_pnl() -> None:
    bus = EventBus()
    await bus.start()
    agent = await build(bus, actions=[Action.ENTER_LONG, Action.EXIT])
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    await fill(bus, agent, 100.0)
    later = make_result({"macd": 0}, {"macd": 1}, start=SESSION_START + timedelta(minutes=5))
    await bus.publish_and_drain("indicator.p1", later)
    await fill(bus, agent, 118.0, correlation_id=agent.position.exit_correlation_id)
    assert len(agent.trades) == 1
    row = agent.trades.rows[0]
    assert row.gross_pnl == pytest.approx(18 * 75)
    assert row.exit_reason == "signal"
    assert row.lots == 1 and row.lot_size == 75
    assert agent.position.is_flat
    await bus.stop()


@pytest.mark.asyncio
async def test_broker_target_leg_closes_the_trade_on_the_entry_correlation_id() -> None:
    bus = EventBus()
    await bus.start()
    agent = await build(bus, actions=[Action.ENTER_LONG])
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    await fill(bus, agent, 100.0)
    await fill(bus, agent, 120.0, status=OrderStatus.TARGET_HIT)
    assert agent.trades.rows[0].exit_reason == "target"
    assert agent.position.is_flat
    await bus.stop()


@pytest.mark.asyncio
async def test_rejected_entry_returns_to_flat() -> None:
    bus = EventBus()
    await bus.start()
    agent = await build(bus, actions=[Action.ENTER_LONG])
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    await bus.publish_and_drain(
        order_event_topic(agent.agent_id),
        OrderEvent(
            agent.position.entry_correlation_id,
            agent.agent_id,
            OrderStatus.REJECTED.value,
            None,
            0,
            None,
            clock.now(),
            "no funds",
        ),
    )
    assert agent.position.is_flat
    assert agent.errors
    await bus.stop()


@pytest.mark.asyncio
async def test_squareoff_closes_an_intraday_position() -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    agent = await build(bus, actions=[Action.ENTER_LONG])
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    await fill(bus, agent, 100.0)
    await bus.publish_and_drain(
        "system.squareoff.start", SystemEvent("squareoff.start", clock.now(), "cut-off")
    )
    assert requests.messages[-1].meta["purpose"] == "exit"
    assert agent.new_entries_blocked
    await bus.stop()


@pytest.mark.asyncio
async def test_squareoff_leaves_an_overnight_position_alone() -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    agent = await build(bus, actions=[Action.ENTER_LONG], pipeline=spec(product=Product.DELIVERY))
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    await fill(bus, agent, 100.0)
    await bus.publish_and_drain(
        "system.squareoff.start", SystemEvent("squareoff.start", clock.now(), "cut-off")
    )
    assert len(requests.messages) == 1  # no exit order
    assert agent.position.is_open
    await bus.stop()


@pytest.mark.asyncio
async def test_no_new_entries_after_the_cutoff() -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    await build(bus, actions=[Action.ENTER_LONG])
    await bus.publish_and_drain(
        "system.squareoff.no_new_entries",
        SystemEvent("squareoff.no_new_entries", clock.now(), "cut-off"),
    )
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    assert requests.messages == []
    await bus.stop()


@pytest.mark.asyncio
async def test_paused_strategy_ignores_signals() -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    agent = await build(bus, actions=[Action.ENTER_LONG])
    agent.pause()
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    assert requests.messages == []
    agent.resume()
    later = make_result({"macd": 1}, {"macd": 0}, start=SESSION_START + timedelta(minutes=5))
    await bus.publish_and_drain("indicator.p1", later)
    assert len(requests.messages) == 1
    await bus.stop()


@pytest.mark.asyncio
async def test_delivery_stock_pipeline_is_long_only() -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    pipeline = spec(segment=Segment.STOCK, product=Product.DELIVERY, underlying_key="NSE_EQ|X")
    await build(bus, actions=[Action.ENTER_SHORT], pipeline=pipeline)
    await bus.publish_and_drain("indicator.p1", make_result({"macd": -1}, {"macd": 1}))
    assert requests.messages == []  # cash-segment shorts cannot be carried overnight
    await bus.stop()


@pytest.mark.asyncio
async def test_intraday_stock_pipeline_may_short() -> None:
    bus = EventBus()
    await bus.start()
    requests = Collector(bus, "order.request")
    pipeline = spec(segment=Segment.STOCK, product=Product.INTRADAY, underlying_key="NSE_EQ|X")
    await build(bus, actions=[Action.ENTER_SHORT], pipeline=pipeline)
    await bus.publish_and_drain("indicator.p1", make_result({"macd": -1}, {"macd": 1}))
    assert requests.last.side is Side.SELL
    await bus.stop()


def test_unrealised_mtm_follows_the_side() -> None:
    long_position = Position(
        state=PositionState.OPEN,
        side=Side.BUY,
        filled_qty=75,
        entry_price=100.0,
        direction=Direction.LONG,
        instrument=make_instrument(),
    )
    assert long_position.unrealised(110.0) == pytest.approx(750)
    short_position = Position(
        state=PositionState.OPEN,
        side=Side.SELL,
        filled_qty=75,
        entry_price=100.0,
        direction=Direction.SHORT,
        instrument=make_instrument(),
    )
    assert short_position.unrealised(110.0) == pytest.approx(-750)


@pytest.mark.asyncio
async def test_trade_is_persisted_to_the_store(tmp_path) -> None:
    bus = EventBus()
    await bus.start()
    store = Store(tmp_path)
    agent = Scripted(
        "scripted-1",
        StrategyConfig(),
        bus,
        store,
        spec=spec(),
        resolver=FakeResolver(),
        actions=[Action.ENTER_LONG],
    )
    await agent.start()
    await bus.publish_and_drain("indicator.p1", make_result({"macd": 1}, {"macd": 0}))
    await fill(bus, agent, 100.0)
    await fill(bus, agent, 120.0, status=OrderStatus.TARGET_HIT)
    saved = store.trades(clock.now().date())
    assert len(saved) == 1 and saved[0]["exit_reason"] == "target"
    await bus.stop()
