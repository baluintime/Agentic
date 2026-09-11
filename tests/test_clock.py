from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from core import clock
from core.clock import IST, Clock, SimClock, floor_to, parse_time, set_clock


def test_now_is_timezone_aware_ist() -> None:
    now = Clock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(hours=5, minutes=30)


@pytest.mark.parametrize(
    "moment,seconds,expected",
    [
        (time(9, 15, 0), 300, time(9, 15)),
        (time(9, 17, 59), 300, time(9, 15)),
        (time(9, 20, 0), 300, time(9, 20)),
        (time(9, 22, 30), 300, time(9, 20)),
        (time(15, 29, 59), 300, time(15, 25)),
        (time(9, 15, 30), 60, time(9, 15)),
        (time(10, 0, 30), 60, time(10, 0)),
    ],
)
def test_candles_align_to_the_session_start(moment, seconds, expected) -> None:
    stamp = datetime.combine(date(2026, 9, 11), moment, tzinfo=IST)
    assert floor_to(stamp, seconds).time() == expected


def test_floor_before_the_open_stays_on_the_same_grid() -> None:
    """Pre-open ticks are bucketed on the grid anchored to the previous 09:15."""
    stamp = datetime.combine(date(2026, 9, 11), time(9, 2, 30), tzinfo=IST)
    bucket = floor_to(stamp, 300)
    assert bucket.time() == time(9, 0)
    assert bucket.date() == date(2026, 9, 11)
    assert (
        bucket - datetime.combine(date(2026, 9, 10), time(9, 15), tzinfo=IST)
    ).seconds % 300 == 0


def test_parse_time() -> None:
    assert parse_time("15:15") == time(15, 15)
    assert parse_time("9") == time(9, 0)


@pytest.mark.asyncio
async def test_sim_clock_advances_without_waiting() -> None:
    sim = SimClock(datetime(2026, 9, 11, 9, 15))
    start = sim.now()
    await sim.sleep(600)
    assert sim.now() - start == timedelta(seconds=600)
    sim.set(datetime(2026, 9, 11, 15, 0))
    assert sim.now().time() == time(15, 0)


def test_set_clock_swaps_the_global_clock() -> None:
    sim = SimClock(datetime(2026, 9, 11, 11, 0))
    previous = set_clock(sim)
    try:
        assert clock.now().hour == 11
    finally:
        set_clock(previous)


def test_market_hours() -> None:
    friday = SimClock(datetime(2026, 9, 11, 10, 0))
    saturday = SimClock(datetime(2026, 9, 12, 10, 0))
    early = SimClock(datetime(2026, 9, 11, 9, 0))
    assert friday.is_market_open()
    assert not saturday.is_market_open()
    assert not early.is_market_open()
