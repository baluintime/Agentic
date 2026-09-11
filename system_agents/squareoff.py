"""Square-off Scheduler: two times that always win over strategy logic.

`intraday_no_new_entries` stops new entries; `intraday_squareoff` cancels open
GTTs, exits intraday positions and then verifies with the positions API that
everything is flat. Overnight (Delivery) pipelines are untouched.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import date, time
from typing import Any, ClassVar

from pydantic import BaseModel

from core import clock
from core.base_agent import BaseAgent
from core.contracts import SystemEvent, system_topic


class SquareOffConfig(BaseModel):
    intraday_no_new_entries: str = "15:00"
    intraday_squareoff: str = "15:15"
    expiry_day_no_new_entries: str = "13:00"
    verify_attempts: int = 3
    verify_gap_seconds: float = 5.0


class SquareOffAgent(BaseAgent):
    kind: ClassVar[str] = "system"
    name: ClassVar[str] = "squareoff"
    Config: ClassVar[type[BaseModel]] = SquareOffConfig
    description: ClassVar[str] = "Stops new entries and squares off before the broker does"

    def __init__(self, *args: Any, rest=None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rest = rest
        self.no_new_entries_done: date | None = None
        self.squareoff_done: date | None = None
        self.flat_verified: bool | None = None
        self.last_message = ""
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await super().start()
        self._task = asyncio.create_task(self._loop(), name="squareoff")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await super().stop()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(5.0)
            await self.tick()

    async def tick(self, now=None) -> None:
        """Idempotent: safe to call as often as you like."""
        cfg: SquareOffConfig = self.config  # type: ignore[assignment]
        now = now or clock.now()
        today = now.date()
        if self.no_new_entries_done != today and now.time() >= _t(cfg.intraday_no_new_entries):
            self.no_new_entries_done = today
            self.last_message = "no new intraday entries"
            await self.emit(
                system_topic("squareoff.no_new_entries"),
                SystemEvent("squareoff.no_new_entries", now, self.last_message),
            )
        if self.squareoff_done != today and now.time() >= _t(cfg.intraday_squareoff):
            self.squareoff_done = today
            await self.run_squareoff("scheduled square-off")

    async def run_squareoff(self, reason: str) -> None:
        """Cancel GTTs -> exit intraday positions -> verify flat."""
        self.last_message = reason
        await self.emit(
            system_topic("squareoff.cancel_gtt"),
            SystemEvent("squareoff.cancel_gtt", clock.now(), reason),
        )
        await self.emit(
            system_topic("squareoff.start"), SystemEvent("squareoff.start", clock.now(), reason)
        )
        self.flat_verified = await self.verify_flat()
        await self.emit(
            system_topic("squareoff.done"),
            SystemEvent(
                "squareoff.done",
                clock.now(),
                reason,
                {"flat": self.flat_verified},
            ),
        )

    async def verify_flat(self) -> bool | None:
        """Confirm with the broker that no intraday position is left open."""
        if self.rest is None:
            return None
        cfg: SquareOffConfig = self.config  # type: ignore[assignment]
        for attempt in range(cfg.verify_attempts):
            try:
                positions = await self.rest.positions()
            except Exception as exc:
                self.fail(f"positions check failed: {exc}")
                return None
            open_intraday = [
                p
                for p in positions
                if int(p.get("quantity", 0)) != 0 and p.get("product") in ("I", "INTRADAY", "MIS")
            ]
            if not open_intraday:
                return True
            self.log.warning(
                "still %d open intraday position(s) after square-off", len(open_intraday)
            )
            if attempt < cfg.verify_attempts - 1:
                await clock.get_clock().sleep(cfg.verify_gap_seconds)
        return False

    def status(self) -> dict[str, Any]:
        cfg: SquareOffConfig = self.config  # type: ignore[assignment]
        base = super().status()
        base.update(
            {
                "no_new_entries_at": cfg.intraday_no_new_entries,
                "squareoff_at": cfg.intraday_squareoff,
                "no_new_entries_done": bool(self.no_new_entries_done),
                "squareoff_done": bool(self.squareoff_done),
                "flat_verified": self.flat_verified,
                "message": self.last_message,
            }
        )
        return base


def _t(value: str) -> time:
    return clock.parse_time(value)
