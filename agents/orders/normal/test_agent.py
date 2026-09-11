from __future__ import annotations

import pytest

from agents.orders.normal.agent import Config, NormalOrderAgent
from core.bus import EventBus
from core.contracts import OrderStatus
from tests.helpers import FakeRest, fill, make_request

LIVE_META = {
    "order_agent": "normal",
    "exec_mode": "live",
    "segment": "option",
    "purpose": "entry",
    "lot_size": 75,
    "freeze_quantity": 1800,
}


def build(rest: FakeRest, armed: bool = True) -> NormalOrderAgent:
    return NormalOrderAgent(
        "normal-1", Config(poll_gap_seconds=0.001), EventBus(), rest=rest, armed_live=armed
    )


@pytest.mark.asyncio
async def test_refuses_when_not_armed_live() -> None:
    agent = build(FakeRest(), armed=False)
    guard = agent.guard(make_request(meta=LIVE_META))
    assert guard is not None and "not armed" in guard.message


@pytest.mark.asyncio
async def test_refuses_a_paper_request() -> None:
    agent = build(FakeRest())
    guard = agent.guard(make_request())  # exec_mode "paper"
    assert guard is not None and guard.status == OrderStatus.REJECTED.value


@pytest.mark.asyncio
async def test_full_fill() -> None:
    rest = FakeRest(fills=[fill(quantity=75, price=101.25)])
    event = await build(rest).place(make_request(meta=LIVE_META))
    assert event.status == OrderStatus.FILLED.value
    assert (event.fill_price, event.filled_qty) == (101.25, 75)
    assert rest.placed[0]["tag"] == "corr0001"
    assert rest.placed[0]["transaction_type"] == "BUY"


@pytest.mark.asyncio
async def test_partial_fill_is_reported_as_partial() -> None:
    rest = FakeRest(fills=[fill(quantity=50, price=100.0)])
    event = await build(rest).place(make_request(meta=LIVE_META))
    assert event.status == OrderStatus.PARTIAL.value
    assert event.filled_qty == 50


@pytest.mark.asyncio
async def test_rejection() -> None:
    rest = FakeRest(
        fills=[fill(status="rejected", quantity=0, price=0, status_message="insufficient funds")]
    )
    event = await build(rest).place(make_request(meta=LIVE_META))
    assert event.status == OrderStatus.REJECTED.value
    assert "insufficient funds" in event.message


@pytest.mark.asyncio
async def test_transport_error_becomes_a_rejection() -> None:
    rest = FakeRest()
    rest.place_error = RuntimeError("connection reset")
    event = await build(rest).place(make_request(meta=LIVE_META))
    assert event.status == OrderStatus.REJECTED.value
    assert "connection reset" in event.message


@pytest.mark.asyncio
async def test_freeze_quantity_slicing() -> None:
    rest = FakeRest(fills=[fill(quantity=1800, price=100.0), fill(quantity=600, price=102.0)])
    request = make_request(quantity=2400, meta=LIVE_META)
    event = await build(rest).place(request)
    assert len(rest.placed) == 2
    assert [p["quantity"] for p in rest.placed] == [1800, 600]
    assert event.filled_qty == 2400
    assert event.fill_price == pytest.approx((1800 * 100 + 600 * 102) / 2400)


def test_slice_helper() -> None:
    assert NormalOrderAgent.slices(2400, 1800) == [1800, 600]
    assert NormalOrderAgent.slices(1800, 1800) == [1800]
    assert NormalOrderAgent.slices(75, None) == [75]
    assert NormalOrderAgent.slices(5000, 1800) == [1800, 1800, 1400]


@pytest.mark.asyncio
async def test_cancel_uses_the_recorded_broker_order_id() -> None:
    rest = FakeRest(fills=[fill()])
    agent = build(rest)
    event = await agent.place(make_request(meta=LIVE_META))
    agent.events.append(event)
    cancelled = await agent.cancel("corr0001")
    assert cancelled is not None and rest.cancelled_orders == ["OID-1"]
