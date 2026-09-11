"""Replay runs the same pipeline code on recorded data."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from core import clock
from core.bus import EventBus
from core.clock import SimClock, set_clock
from core.contracts import Timeframe
from core.store import Store
from system_agents.candles import CandleAgent
from system_agents.recorder import RecorderAgent
from system_agents.replay import ReplayAgent, ReplayConfig, ticks_from_frame
from tests import fixtures
from tests.helpers import Collector

DAY = date(2026, 9, 10)


@pytest.fixture
def sim_clock():
    previous = set_clock(SimClock(datetime(2026, 9, 10, 9, 15)))
    yield clock.get_clock()
    set_clock(previous)


def recorded_frame() -> pd.DataFrame:
    return fixtures.ticks()


@pytest.mark.asyncio
async def test_replay_publishes_every_recorded_tick(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    ticks = Collector(bus, "tick.*")
    agent = ReplayAgent("replay", ReplayConfig(), bus)
    frame = recorded_frame()
    count = await agent.replay_frame("NSE_FO|CE", frame)
    assert count == len(frame)
    assert len(ticks.messages) == len(frame)
    assert ticks.messages[0].ltp == frame["ltp"].iloc[0]
    assert agent.finished
    await bus.stop()


@pytest.mark.asyncio
async def test_replay_drives_the_simulated_clock(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = ReplayAgent("replay", ReplayConfig(), bus)
    frame = recorded_frame()
    await agent.replay_frame("NSE_FO|CE", frame)
    assert clock.now() == pytest.approx(
        pd.Timestamp(frame["ts"].iloc[-1]).to_pydatetime(), abs=timedelta(seconds=1)
    )
    await bus.stop()


@pytest.mark.asyncio
async def test_replayed_ticks_build_the_same_candles(sim_clock) -> None:
    """Replay feeds the ordinary candle agent: no special code path."""
    bus = EventBus()
    await bus.start()
    candles = Collector(bus, "candle.1m.NSE_FO|CE")
    builder = CandleAgent(
        "c", instrument_key="NSE_FO|CE", timeframe=Timeframe.M1, bus=bus, auto_close=False
    )
    await builder.start()
    replay = ReplayAgent("replay", ReplayConfig(), bus)
    await replay.replay_frame("NSE_FO|CE", recorded_frame())
    closed = [c for c in candles.messages if c.is_closed]
    assert len(closed) >= 15  # 600 ticks two seconds apart
    assert all(c.high >= c.low for c in closed)
    assert closed[0].volume > 0
    await bus.stop()


@pytest.mark.asyncio
async def test_record_then_replay_round_trip(tmp_path, sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    store = Store(tmp_path)
    recorder = RecorderAgent("recorder", None, bus, store, flush_every=10_000)
    await recorder.start()
    source = ReplayAgent("source", ReplayConfig(), bus, store)
    frame = recorded_frame().head(50)
    await source.replay_frame("NSE_FO|CE", frame)
    recorder.flush()

    replay = ReplayAgent("replay", ReplayConfig(), bus, store)
    assert replay.recorded_days("NSE_FO|CE") == [DAY]
    ticks = Collector(bus, "tick.*")
    count = await replay.replay_day("NSE_FO|CE", DAY)
    replay.restore_clock()
    assert count == 50
    assert len(ticks.messages) == 50
    await bus.stop()


@pytest.mark.asyncio
async def test_missing_recording_is_reported(tmp_path, sim_clock) -> None:
    agent = ReplayAgent("replay", ReplayConfig(), EventBus(), Store(tmp_path))
    assert await agent.replay_day("NSE_FO|NOPE", DAY) == 0
    assert agent.errors


def test_ticks_from_frame_handles_missing_columns() -> None:
    frame = pd.DataFrame({"ts": [datetime(2026, 9, 10, 9, 15)], "ltp": [100.0]})
    ticks = ticks_from_frame("K", frame)
    assert ticks[0].cum_volume is None and ticks[0].oi is None
    assert ticks[0].ts.tzinfo is not None
