"""Candle building: alignment, volume deltas, the 1m -> 5m chain, history."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from core import clock
from core.bus import EventBus
from core.clock import SimClock, set_clock
from core.contracts import Tick, Timeframe
from core.store import Store
from system_agents.candles import CandleAgent, candles_to_frame
from tests.helpers import Collector, FakeRest

OPEN = datetime(2026, 9, 11, 9, 15)


@pytest.fixture
def sim_clock():
    previous = set_clock(SimClock(OPEN))
    yield clock.get_clock()
    set_clock(previous)


def at(minutes: float) -> datetime:
    return clock.ist(OPEN) + timedelta(minutes=minutes)


async def build(bus: EventBus, timeframe: Timeframe, **kwargs) -> CandleAgent:
    agent = CandleAgent(
        f"c-{timeframe.value}",
        instrument_key="K",
        timeframe=timeframe,
        bus=bus,
        auto_close=False,
        **kwargs,
    )
    await agent.start()
    return agent


@pytest.mark.asyncio
async def test_one_minute_candles_align_to_the_session_open(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    candles = Collector(bus, "candle.1m.K")
    await build(bus, Timeframe.M1)
    for minute, price in ((0.1, 100.0), (0.5, 102.0), (0.9, 101.0), (1.2, 105.0)):
        await bus.publish_and_drain("tick.K", Tick("K", at(minute), price))
    closed = [c for c in candles.messages if c.is_closed]
    assert len(closed) == 1
    candle = closed[0]
    assert candle.start.time().strftime("%H:%M") == "09:15"
    assert (candle.open, candle.high, candle.low, candle.close) == (100.0, 102.0, 100.0, 101.0)
    await bus.stop()


@pytest.mark.asyncio
async def test_volume_is_the_delta_of_cumulative_day_volume(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    candles = Collector(bus, "candle.1m.K")
    await build(bus, Timeframe.M1)
    for minute, volume in ((0.1, 1000), (0.5, 1500), (0.9, 1800), (1.2, 2000)):
        await bus.publish_and_drain("tick.K", Tick("K", at(minute), 100.0, cum_volume=volume))
    closed = [c for c in candles.messages if c.is_closed][0]
    assert closed.volume == 800  # 1800 - 1000, not the cumulative 1800
    await bus.stop()


@pytest.mark.asyncio
async def test_volume_resets_on_a_new_day(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = await build(bus, Timeframe.M1)
    await bus.publish_and_drain("tick.K", Tick("K", at(0.1), 100.0, cum_volume=5000))
    tomorrow = clock.ist(OPEN) + timedelta(days=1)
    await bus.publish_and_drain("tick.K", Tick("K", tomorrow, 100.0, cum_volume=10))
    assert agent.current.volume == 0  # the counter restarted; no huge negative or positive jump
    await bus.stop()


@pytest.mark.asyncio
async def test_five_minute_candles_are_built_from_closed_one_minute_candles(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    candles = Collector(bus, "candle.5m.K")
    await build(bus, Timeframe.M1)
    await build(bus, Timeframe.M5)
    price = 100.0
    for minute in range(12):
        for offset in (0.1, 0.5, 0.9):
            await bus.publish_and_drain("tick.K", Tick("K", at(minute + offset), price))
            price += 1
    closed = [c for c in candles.messages if c.is_closed]
    assert [c.start.time().strftime("%H:%M") for c in closed] == ["09:15", "09:20"]
    assert closed[0].open == 100.0
    assert closed[0].high == 114.0  # the last tick of the 09:19 candle
    assert closed[0].low == 100.0
    await bus.stop()


@pytest.mark.asyncio
async def test_close_due_closes_a_candle_without_further_ticks(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    candles = Collector(bus, "candle.1m.K")
    agent = await build(bus, Timeframe.M1)
    await bus.publish_and_drain("tick.K", Tick("K", at(0.1), 100.0))
    await agent.close_due(at(0.5))
    assert not [c for c in candles.messages if c.is_closed]
    await agent.close_due(at(1.5))
    await bus.drain()
    assert len([c for c in candles.messages if c.is_closed]) == 1
    await bus.stop()


@pytest.mark.asyncio
async def test_updates_are_emitted_before_the_close(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    candles = Collector(bus, "candle.1m.K")
    await build(bus, Timeframe.M1)
    await bus.publish_and_drain("tick.K", Tick("K", at(0.1), 100.0))
    await bus.publish_and_drain("tick.K", Tick("K", at(0.5), 101.0))
    updates = [c for c in candles.messages if not c.is_closed]
    assert len(updates) == 2 and updates[-1].close == 101.0
    await bus.stop()


@pytest.mark.asyncio
async def test_ticks_for_other_instruments_are_ignored(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = await build(bus, Timeframe.M1)
    await bus.publish_and_drain("tick.OTHER", Tick("OTHER", at(0.1), 100.0))
    assert agent.current is None
    await bus.stop()


def test_candles_to_frame_parses_broker_rows() -> None:
    rows = [["2026-09-11T09:15:00+05:30", 100, 102, 99, 101, 500, 0]]
    frame = candles_to_frame(rows)
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert frame.index[0].strftime("%H:%M") == "09:15"
    assert candles_to_frame([]).empty


@pytest.mark.asyncio
async def test_history_length_is_driven_by_the_indicator_warmup(sim_clock) -> None:
    agent = CandleAgent(
        "c",
        instrument_key="K",
        timeframe=Timeframe.M5,
        history_days=5,
        warmup_bars=600,
        auto_close=False,
    )
    assert agent.history_days_needed() == 8  # 600 bars / 75 per day, rounded up
    assert agent.required_bars() == 600
    short = CandleAgent(
        "c",
        instrument_key="K",
        timeframe=Timeframe.M5,
        history_days=5,
        warmup_bars=100,
        auto_close=False,
    )
    assert short.history_days_needed() == 5  # the configured minimum still wins


@pytest.mark.asyncio
async def test_history_is_cached_as_parquet(tmp_path, sim_clock) -> None:
    class WithHistory(FakeRest):
        async def historical_candles(self, key, unit, interval, to_date, from_date):
            base = clock.ist(OPEN) - timedelta(days=1)
            return [
                [(base + timedelta(minutes=5 * i)).isoformat(), 100, 101, 99, 100.5, 10, 0]
                for i in range(20)
            ]

        async def intraday_candles(self, key, unit="minutes", interval=1):
            return []

    store = Store(tmp_path)
    agent = CandleAgent(
        "c",
        instrument_key="K",
        timeframe=Timeframe.M5,
        store=store,
        rest=WithHistory(),
        auto_close=False,
    )
    assert await agent.load_history() == 20
    assert agent.history_loaded and len(agent.frame) == 20
    cached = store.history_path("K", "5m", clock.now().date())
    assert cached.exists()

    # a restart reuses the cache instead of calling the API again
    class NoApi(FakeRest):
        async def historical_candles(self, *a, **k):
            raise AssertionError("must not hit the API twice in one day")

    again = CandleAgent(
        "c", instrument_key="K", timeframe=Timeframe.M5, store=store, rest=NoApi(), auto_close=False
    )
    assert await again.load_history() == 20


@pytest.mark.asyncio
async def test_backfill_replaces_the_gap_after_a_reconnect(sim_clock) -> None:
    class Intraday(FakeRest):
        async def intraday_candles(self, key, unit="minutes", interval=1):
            return [
                [(clock.ist(OPEN) + timedelta(minutes=5 * i)).isoformat(), 1, 9, 0, 5, 50, 0]
                for i in range(3)
            ]

    agent = CandleAgent(
        "c", instrument_key="K", timeframe=Timeframe.M5, rest=Intraday(), auto_close=False
    )
    agent.frame = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [0.0]},
        index=pd.DatetimeIndex([clock.ist(OPEN)], name="start"),
    )
    assert await agent.backfill() == 3
    assert len(agent.frame) == 3
    assert agent.frame["high"].iloc[0] == 9.0  # the API row wins over the partial local candle
