from __future__ import annotations

import pytest

from agents.orders.gtt.agent import Config, GttOrderAgent
from core import clock
from core.bus import EventBus
from core.contracts import OrderStatus, Side, SystemEvent
from tests.helpers import Collector, FakeRest, fill, make_request

LIVE_META = {
    "order_agent": "gtt",
    "exec_mode": "live",
    "segment": "option",
    "purpose": "entry",
    "lot_size": 75,
    "freeze_quantity": 1800,
}


def build(rest: FakeRest, bus: EventBus | None = None) -> GttOrderAgent:
    return GttOrderAgent(
        "gtt-1", Config(poll_gap_seconds=0.001), bus or EventBus(), rest=rest, armed_live=True
    )


@pytest.mark.asyncio
async def test_target_and_stop_come_from_the_actual_fill() -> None:
    rest = FakeRest(fills=[fill(quantity=75, price=104.0)])
    event = await build(rest).place(make_request(meta=LIVE_META))  # target 20, SL 10
    assert event.status == OrderStatus.FILLED.value
    rules = {r["strategy"]: r["trigger_price"] for r in rest.gtts[0]["rules"]}
    assert rules["ENTRY"] == 124.0  # 104 + 20, not 100 + 20
    assert rules["STOPLOSS"] == 94.0
    assert rest.gtts[0]["transaction_type"] == "SELL"  # exit side of a long


@pytest.mark.asyncio
async def test_short_entry_inverts_the_legs() -> None:
    rest = FakeRest(fills=[fill(quantity=75, price=100.0)])
    await build(rest).place(make_request(side=Side.SELL, meta=LIVE_META))
    rules = {r["strategy"]: r["trigger_price"] for r in rest.gtts[0]["rules"]}
    assert rules["ENTRY"] == 80.0  # short target is below the fill
    assert rules["STOPLOSS"] == 110.0


@pytest.mark.asyncio
async def test_percent_mode_applies_to_the_fill_price() -> None:
    rest = FakeRest(fills=[fill(quantity=75, price=200.0)])
    meta = {**LIVE_META, "target_mode": "percent", "target_value": 10, "stoploss_value": 5}
    await build(rest).place(make_request(meta=meta))
    rules = {r["strategy"]: r["trigger_price"] for r in rest.gtts[0]["rules"]}
    assert rules["ENTRY"] == 220.0
    assert rules["STOPLOSS"] == 190.0


@pytest.mark.asyncio
async def test_entry_rejection_places_no_legs() -> None:
    rest = FakeRest(fills=[fill(status="rejected", quantity=0, price=0)])
    event = await build(rest).place(make_request(meta=LIVE_META))
    assert event.status == OrderStatus.REJECTED.value
    assert rest.gtts == []


@pytest.mark.asyncio
async def test_entry_survives_a_failed_gtt_placement() -> None:
    rest = FakeRest(fills=[fill(quantity=75, price=100.0)])
    rest.gtt_error = RuntimeError("gtt rejected")
    event = await build(rest).place(make_request(meta=LIVE_META))
    assert event.status == OrderStatus.FILLED.value
    assert "GTT legs failed" in event.message


@pytest.mark.asyncio
async def test_stoploss_hit_is_routed_to_the_origin_and_cancels_the_sibling() -> None:
    bus = EventBus()
    await bus.start()
    events = Collector(bus, "order.event.*")
    rest = FakeRest(fills=[fill(quantity=75, price=100.0)])
    agent = build(rest, bus)
    await agent.place(make_request(meta=LIVE_META))
    await agent.on_broker_update(
        {"tag": "corr0001", "status": "complete", "strategy": "STOPLOSS", "average_price": 90.0}
    )
    await bus.drain()
    hit = events.of_status(OrderStatus.SL_HIT.value)[0]
    assert hit.origin_agent_id == "strategy-1"
    assert hit.correlation_id == "corr0001"
    assert rest.cancelled_gtts == ["GTT-1"]
    assert not agent.open_gtts
    await bus.stop()


@pytest.mark.asyncio
async def test_target_hit_is_reported() -> None:
    bus = EventBus()
    await bus.start()
    events = Collector(bus, "order.event.*")
    rest = FakeRest(fills=[fill(quantity=75, price=100.0)])
    agent = build(rest, bus)
    await agent.place(make_request(meta=LIVE_META))
    await agent.on_broker_update(
        {"tag": "corr0001", "status": "complete", "strategy": "ENTRY", "average_price": 120.0}
    )
    await bus.drain()
    assert events.of_status(OrderStatus.TARGET_HIT.value)[0].fill_price == 120.0
    await bus.stop()


@pytest.mark.asyncio
async def test_unknown_tag_is_ignored() -> None:
    rest = FakeRest(fills=[fill()])
    agent = build(rest)
    await agent.on_broker_update({"tag": "nope", "status": "complete", "strategy": "ENTRY"})
    assert not agent.events


@pytest.mark.asyncio
async def test_squareoff_cancels_open_gtts_first() -> None:
    bus = EventBus()
    await bus.start()
    rest = FakeRest(fills=[fill(quantity=75, price=100.0)])
    agent = build(rest, bus)
    await agent.start()
    await agent.place(make_request(meta=LIVE_META))
    await bus.publish_and_drain(
        "system.squareoff.cancel_gtt", SystemEvent("squareoff.cancel_gtt", clock.now(), "cut-off")
    )
    assert rest.cancelled_gtts == ["GTT-1"]
    assert not agent.open_gtts
    await bus.stop()


@pytest.mark.asyncio
async def test_trigger_prices_snap_to_the_instrument_tick() -> None:
    """A fractional target lands between ticks; the exchange rejects those."""
    rest = FakeRest(fills=[fill(quantity=75, price=104.25)])
    meta = {**LIVE_META, "tick_size": 0.05}
    await build(rest).place(make_request(target_points=0.33, stoploss_points=0.33, meta=meta))
    prices = [rule["trigger_price"] for rule in rest.gtts[0]["rules"]]
    assert prices == [104.6, 103.9]  # 104.58 / 103.92 snapped to the 0.05 grid
    assert all(round(p / 0.05) * 0.05 == pytest.approx(p) for p in prices)


@pytest.mark.asyncio
async def test_a_clean_fractional_target_is_left_alone() -> None:
    rest = FakeRest(fills=[fill(quantity=75, price=104.25)])
    meta = {**LIVE_META, "tick_size": 0.05}
    await build(rest).place(make_request(target_points=0.5, stoploss_points=0.25, meta=meta))
    prices = {r["strategy"]: r["trigger_price"] for r in rest.gtts[0]["rules"]}
    assert prices["ENTRY"] == 104.75 and prices["STOPLOSS"] == 104.0


def test_rounding_helper_handles_a_missing_tick_size() -> None:
    assert GttOrderAgent.round_to_tick(104.583, None) == 104.58
    assert GttOrderAgent.round_to_tick(104.583, 0) == 104.58
    assert GttOrderAgent.round_to_tick(None, 0.05) is None
    assert GttOrderAgent.round_to_tick(104.583, 0.05) == 104.6
    assert GttOrderAgent.round_to_tick(1387.4, 0.01) == 1387.4
