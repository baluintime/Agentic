from __future__ import annotations

import pytest

from agents.orders.paper.agent import Config, PaperOrderAgent
from core import clock
from core.bus import EventBus
from core.contracts import OrderStatus, Side, SystemEvent, Tick
from tests.helpers import Collector, make_request


async def build(bus: EventBus, **overrides) -> PaperOrderAgent:
    agent = PaperOrderAgent("paper-1", Config(**overrides), bus)
    await agent.start()
    return agent


@pytest.mark.asyncio
async def test_rejects_without_a_price() -> None:
    bus = EventBus()
    agent = await build(bus)
    event = await agent.place(make_request())
    assert event.status == OrderStatus.REJECTED.value
    assert "no LTP" in event.message


@pytest.mark.asyncio
async def test_fills_at_ltp_plus_slippage() -> None:
    bus = EventBus()
    agent = await build(bus, slippage_points=0.5, slippage_percent=0.0)
    agent.broker.on_tick("NSE_FO|CE24850", 100.0)
    event = await agent.place(make_request())
    assert event.status == OrderStatus.FILLED.value
    assert event.fill_price == 100.5  # slippage always works against the trader
    assert event.filled_qty == 75


@pytest.mark.asyncio
async def test_slippage_works_against_a_sell() -> None:
    bus = EventBus()
    agent = await build(bus, slippage_points=0.5, slippage_percent=0.0)
    agent.broker.on_tick("NSE_FO|CE24850", 100.0)
    event = await agent.place(make_request(side=Side.SELL))
    assert event.fill_price == 99.5


@pytest.mark.asyncio
async def test_target_leg_reports_on_the_entry_correlation_id() -> None:
    bus = EventBus()
    await bus.start()
    events = Collector(bus, "order.event.*")
    agent = await build(bus, slippage_points=0.0, slippage_percent=0.0)
    agent.broker.on_tick("NSE_FO|CE24850", 100.0)
    await agent.place(make_request())  # target +20, SL -10
    await bus.publish_and_drain("tick.NSE_FO|CE24850", Tick("NSE_FO|CE24850", clock.now(), 121.0))
    hits = events.of_status(OrderStatus.TARGET_HIT.value)
    assert len(hits) == 1
    assert hits[0].correlation_id == "corr0001"
    assert hits[0].fill_price == 120.0
    await bus.stop()


@pytest.mark.asyncio
async def test_stoploss_leg_triggers() -> None:
    bus = EventBus()
    await bus.start()
    events = Collector(bus, "order.event.*")
    agent = await build(bus, slippage_points=0.0, slippage_percent=0.0)
    agent.broker.on_tick("NSE_FO|CE24850", 100.0)
    await agent.place(make_request())
    await bus.publish_and_drain("tick.NSE_FO|CE24850", Tick("NSE_FO|CE24850", clock.now(), 89.0))
    assert events.of_status(OrderStatus.SL_HIT.value)[0].fill_price == 90.0
    await bus.stop()


@pytest.mark.asyncio
async def test_leg_is_dropped_on_exit_order() -> None:
    bus = EventBus()
    await bus.start()
    agent = await build(bus, slippage_points=0.0, slippage_percent=0.0)
    agent.broker.on_tick("NSE_FO|CE24850", 100.0)
    await agent.place(make_request())
    assert agent.legs
    await agent.place(
        make_request(
            correlation_id="corr0002",
            side=Side.SELL,
            meta={"order_agent": "paper", "exec_mode": "paper", "purpose": "exit"},
        )
    )
    assert not agent.legs
    await bus.stop()


@pytest.mark.asyncio
async def test_squareoff_closes_open_legs() -> None:
    bus = EventBus()
    await bus.start()
    events = Collector(bus, "order.event.*")
    agent = await build(bus, slippage_points=0.0, slippage_percent=0.0)
    agent.broker.on_tick("NSE_FO|CE24850", 100.0)
    await agent.place(make_request())
    await bus.publish_and_drain(
        "system.squareoff.start", SystemEvent("squareoff.start", clock.now(), "cut-off")
    )
    assert events.of_status(OrderStatus.SQUARED_OFF.value)
    assert not agent.legs
    await bus.stop()


@pytest.mark.asyncio
async def test_refuses_live_orders() -> None:
    bus = EventBus()
    agent = await build(bus)
    request = make_request(meta={"order_agent": "paper", "exec_mode": "live", "purpose": "entry"})
    guard = agent.guard(request)
    assert guard is not None and guard.status == OrderStatus.REJECTED.value
