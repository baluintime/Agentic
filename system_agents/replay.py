"""Replay agent: push recorded data through the live pipelines.

Because every agent is deterministic and all data is recorded, a recorded
session can be pushed through the exact same candle, indicator, strategy and
Paper Order agents with a simulated clock. A strategy can therefore be tested on
last week's data before it ever trades — this is the single highest-value tool
in the platform, and it needs no special code path anywhere else.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel, Field

from core import clock
from core.base_agent import BaseAgent
from core.clock import SimClock, set_clock
from core.contracts import Tick, tick_topic


class ReplayConfig(BaseModel):
    speed: float = Field(0.0, ge=0, description="0 = as fast as possible, 1 = real time")
    drain_every: int = Field(1, ge=1, description="Let subscribers catch up every N ticks")


class ReplayAgent(BaseAgent):
    kind: ClassVar[str] = "system"
    name: ClassVar[str] = "replay"
    Config: ClassVar[type[BaseModel]] = ReplayConfig
    description: ClassVar[str] = "Feeds recorded ticks through the live pipelines"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.replayed = 0
        self.source = ""
        self.finished = False
        self.previous_clock = None

    # -- sources -------------------------------------------------------------
    def recorded_days(self, instrument_key: str) -> list[date]:
        if self.store is None:
            return []
        prefix = instrument_key.replace("|", "_")
        days = []
        for path in sorted((self.store.base / "ticks").glob(f"{prefix}_*.parquet")):
            try:
                days.append(date.fromisoformat(path.stem.rsplit("_", 1)[1]))
            except ValueError:
                continue
        return days

    def load(self, instrument_key: str, day: date) -> pd.DataFrame:
        if self.store is None:
            return pd.DataFrame()
        path = self.store.tick_path(instrument_key, day)
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()

    # -- replay --------------------------------------------------------------
    async def replay_frame(self, instrument_key: str, frame: pd.DataFrame) -> int:
        """Publish every recorded tick, advancing the simulated clock with it."""
        return await self.replay_ticks(
            Tick(
                instrument_key=instrument_key,
                ts=_as_datetime(row.ts),
                ltp=float(row.ltp),
                cum_volume=_as_int(getattr(row, "cum_volume", None)),
                oi=_as_float(getattr(row, "oi", None)),
            )
            for row in frame.itertuples()
        )

    async def replay_ticks(self, ticks: Iterable[Tick]) -> int:
        cfg: ReplayConfig = self.config  # type: ignore[assignment]
        count = 0
        previous: datetime | None = None
        for tick in ticks:
            sim = clock.get_clock()
            if isinstance(sim, SimClock):
                sim.set(tick.ts)
            if cfg.speed and previous is not None:
                await asyncio.sleep((tick.ts - previous).total_seconds() / cfg.speed)
            previous = tick.ts
            await self.emit(tick_topic(tick.instrument_key), tick)
            count += 1
            self.replayed += 1
            if self.bus and count % cfg.drain_every == 0:
                await self.bus.drain()
        if self.bus:
            await self.bus.drain()
        self.finished = True
        return count

    async def replay_day(self, instrument_key: str, day: date) -> int:
        """Replay one recorded session, with the clock starting at its first tick."""
        frame = self.load(instrument_key, day)
        if frame.empty:
            self.fail(f"no recorded ticks for {instrument_key} on {day}")
            return 0
        self.source = f"{instrument_key} {day}"
        self.use_sim_clock(_as_datetime(frame["ts"].iloc[0]))
        return await self.replay_frame(instrument_key, frame)

    # -- clock ---------------------------------------------------------------
    def use_sim_clock(self, start: datetime) -> SimClock:
        sim = SimClock(start)
        self.previous_clock = set_clock(sim)
        return sim

    def restore_clock(self) -> None:
        if self.previous_clock is not None:
            set_clock(self.previous_clock)
            self.previous_clock = None

    async def stop(self) -> None:
        self.restore_clock()
        await super().stop()

    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {
                "source": self.source,
                "replayed": self.replayed,
                "finished": self.finished,
                "clock": clock.get_clock().name,
            }
        )
        return base


def ticks_from_frame(instrument_key: str, frame: pd.DataFrame) -> list[Tick]:
    """Helper for tests and for replaying a Parquet file loaded elsewhere."""
    return [
        Tick(
            instrument_key=instrument_key,
            ts=_as_datetime(row.ts),
            ltp=float(row.ltp),
            cum_volume=_as_int(getattr(row, "cum_volume", None)),
            oi=_as_float(getattr(row, "oi", None)),
        )
        for row in frame.itertuples()
    ]


def parquet_days(folder: Path, instrument_key: str) -> list[date]:
    prefix = instrument_key.replace("|", "_")
    days = []
    for path in sorted(folder.glob(f"{prefix}_*.parquet")):
        try:
            days.append(date.fromisoformat(path.stem.rsplit("_", 1)[1]))
        except ValueError:
            continue
    return days


def _as_datetime(value: Any) -> datetime:
    stamp = pd.Timestamp(value)
    moment = stamp.to_pydatetime()
    return clock.ist(moment)


def _as_int(value: Any) -> int | None:
    return None if value is None or pd.isna(value) else int(value)


def _as_float(value: Any) -> float | None:
    return None if value is None or pd.isna(value) else float(value)
