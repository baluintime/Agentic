from __future__ import annotations

import asyncio

import pytest

from core.bus import EventBus, topic_matches


@pytest.mark.parametrize(
    "pattern,topic,expected",
    [
        ("tick.NSE|1", "tick.NSE|1", True),
        ("tick.*", "tick.NSE|1", True),
        ("tick.*", "tick.NSE|1.extra", True),
        ("candle.5m.*", "candle.5m.NSE|1", True),
        ("candle.5m.*", "candle.1m.NSE|1", False),
        ("system.*", "system.squareoff.done", True),
        ("system.*", "order.request", False),
        ("order.event.*", "order.event.s1", True),
        ("#", "anything.at.all", True),
        ("order.request", "order.approved", False),
        ("a.*.c", "a.b.c", True),
        ("a.*.c", "a.b.d", False),
    ],
)
def test_topic_matching(pattern, topic, expected) -> None:
    assert topic_matches(pattern, topic) is expected


@pytest.mark.asyncio
async def test_delivers_to_every_matching_subscriber() -> None:
    bus = EventBus()
    first, second, other = [], [], []
    bus.subscribe("tick.*", first.append, "first")
    bus.subscribe("tick.X", second.append, "second")
    bus.subscribe("candle.*.*", other.append, "other")
    await bus.start()
    await bus.publish_and_drain("tick.X", 1)
    assert first == [1] and second == [1] and other == []
    await bus.stop()


@pytest.mark.asyncio
async def test_async_handlers_are_awaited() -> None:
    bus = EventBus()
    seen = []

    async def handler(message):
        await asyncio.sleep(0)
        seen.append(message)

    bus.subscribe("x", handler, "async")
    await bus.start()
    await bus.publish_and_drain("x", "hello")
    assert seen == ["hello"]
    await bus.stop()


@pytest.mark.asyncio
async def test_a_failing_subscriber_does_not_affect_the_others() -> None:
    bus = EventBus()
    good = []

    def explode(_message):
        raise RuntimeError("boom")

    bus.subscribe("x", explode, "bad")
    bus.subscribe("x", good.append, "good")
    await bus.start()
    await bus.publish_and_drain("x", 1)
    await bus.publish_and_drain("x", 2)
    assert good == [1, 2]  # the bad subscriber kept running too
    await bus.stop()


@pytest.mark.asyncio
async def test_a_full_queue_drops_instead_of_blocking() -> None:
    bus = EventBus(queue_size=2)
    blocked = asyncio.Event()

    async def slow(_message):
        await blocked.wait()

    bus.subscribe("x", slow, "slow")
    await bus.start()
    for index in range(10):
        await bus.publish("x", index)
    assert bus.stats()["dropped"] > 0
    blocked.set()
    await bus.stop()


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    seen = []
    sub = bus.subscribe("x", seen.append, "one")
    await bus.start()
    await bus.publish_and_drain("x", 1)
    bus.unsubscribe(sub)
    await bus.publish_and_drain("x", 2)
    assert seen == [1]
    await bus.stop()


@pytest.mark.asyncio
async def test_subscribing_after_start_still_works() -> None:
    bus = EventBus()
    await bus.start()
    seen = []
    bus.subscribe("x", seen.append, "late")
    await bus.publish_and_drain("x", 1)
    assert seen == [1]
    await bus.stop()
