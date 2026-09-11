"""Time for the whole system. Nothing else may call datetime.now().

`now()` always returns a timezone-aware IST datetime so that replay (SimClock)
and live behave identically.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


class Clock:
    """Wall-clock time in IST."""

    name = "real"

    def now(self) -> datetime:
        return datetime.now(IST)

    def today(self) -> date:
        return self.now().date()

    def time_of_day(self) -> time:
        return self.now().timetz().replace(tzinfo=None)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def is_market_open(self, open_t: time = MARKET_OPEN, close_t: time = MARKET_CLOSE) -> bool:
        now = self.now()
        if now.weekday() >= 5:  # Sat/Sun; exchange holidays come from the broker calendar
            return False
        return open_t <= now.time() <= close_t


class SimClock(Clock):
    """Simulated clock for replay and tests. `sleep` advances time instantly."""

    name = "sim"

    def __init__(self, start: datetime) -> None:
        self._now = _as_ist(start)

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        self._now = _as_ist(moment)

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.advance(seconds)
        await asyncio.sleep(0)


def _as_ist(moment: datetime) -> datetime:
    return moment.astimezone(IST) if moment.tzinfo else moment.replace(tzinfo=IST)


_clock: Clock = Clock()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> Clock:
    global _clock
    previous, _clock = _clock, clock
    return previous


def now() -> datetime:
    return _clock.now()


def ist(moment: datetime) -> datetime:
    """Attach/convert to IST without going through the global clock."""
    return _as_ist(moment)


def parse_time(value: str) -> time:
    """`"15:15"` -> time(15, 15). Used for config values."""
    hh, _, mm = value.partition(":")
    return time(int(hh), int(mm or 0))


def combine(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, tzinfo=IST)


def floor_to(moment: datetime, seconds: int, anchor: time = MARKET_OPEN) -> datetime:
    """Floor `moment` to a bucket of `seconds`, aligned to the session anchor.

    5-minute candles therefore start 09:15, 09:20, ... and not 09:00, 09:05.
    """
    moment = _as_ist(moment)
    base = combine(moment.date(), anchor)
    if moment < base:
        base -= timedelta(days=1)
    elapsed = int((moment - base).total_seconds())
    return base + timedelta(seconds=(elapsed // seconds) * seconds)
