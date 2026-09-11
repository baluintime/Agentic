"""Health / Watchdog: is the data flowing, is the token alive, is the clock right.

If no tick arrives for `stale_tick_seconds` during market hours the agent pauses
new entries and raises an alert; it also watches the feed's reconnects, token
expiry, and the machine clock against the exchange session.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel

from core import clock
from core.base_agent import BaseAgent
from core.contracts import SystemEvent, Tick, system_topic


class HealthConfig(BaseModel):
    stale_tick_seconds: float = 30
    check_interval_seconds: float = 5
    market_open: str = "09:15"
    market_close: str = "15:30"


class HealthAgent(BaseAgent):
    kind: ClassVar[str] = "system"
    name: ClassVar[str] = "health"
    Config: ClassVar[type[BaseModel]] = HealthConfig
    description: ClassVar[str] = "Heartbeat, stale-data detection, token and clock checks"

    def __init__(self, *args: Any, hub=None, auth=None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.hub = hub
        self.auth = auth
        self.last_tick_at: datetime | None = None
        self.stale = False
        self.alerts: list[str] = []
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await super().start()
        self.listen("tick.*", self._on_tick)
        self._task = asyncio.create_task(self._loop(), name="health")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await super().stop()

    async def _on_tick(self, tick: Tick) -> None:
        self.last_tick_at = clock.now()
        if self.stale:
            self.stale = False
            await self._alert("data.resumed", "ticks resumed")

    async def _loop(self) -> None:
        cfg: HealthConfig = self.config  # type: ignore[assignment]
        while True:
            await clock.get_clock().sleep(cfg.check_interval_seconds)
            await self.check()

    async def check(self, now: datetime | None = None) -> dict[str, Any]:
        cfg: HealthConfig = self.config  # type: ignore[assignment]
        now = now or clock.now()
        report = {"stale": False, "token": "", "clock": "", "feed": ""}
        in_session = (
            clock.parse_time(cfg.market_open) <= now.time() <= clock.parse_time(cfg.market_close)
        )
        if in_session and self.last_tick_at is not None:
            gap = (now - self.last_tick_at).total_seconds()
            if gap > cfg.stale_tick_seconds and not self.stale:
                self.stale = True
                report["stale"] = True
                await self._alert("data.stale", f"no tick for {gap:.0f}s — new entries paused")
        if self.auth is not None:
            warning = self.auth.expiry_warning(now)
            if warning:
                report["token"] = warning
                await self._alert("token", warning)
        if self.hub is not None and not getattr(self.hub, "connected", True):
            report["feed"] = "disconnected"
        drift = _clock_drift(now)
        if drift:
            report["clock"] = drift
            await self._alert("clock", drift)
        return report

    async def _alert(self, kind: str, message: str) -> None:
        if self.alerts and self.alerts[-1] == f"{kind}: {message}":
            return
        self.alerts.append(f"{kind}: {message}")
        self.log.warning("health %s: %s", kind, message)
        await self.emit(
            system_topic(f"health.{kind}"), SystemEvent(f"health.{kind}", clock.now(), message)
        )
        if kind == "data.stale":
            await self.emit(
                system_topic("risk.block"), SystemEvent("risk.block", clock.now(), message)
            )

    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {
                "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
                "stale": self.stale,
                "feed_connected": getattr(self.hub, "connected", None),
                "alerts": self.alerts[-5:],
            }
        )
        return base


def _clock_drift(now: datetime) -> str:
    """All timestamps must be IST; a naive or non-IST clock breaks candle alignment."""
    if now.tzinfo is None:
        return "system clock is not timezone-aware"
    offset = now.utcoffset()
    if offset is None or offset.total_seconds() != 19800:
        return f"clock offset {offset} is not IST (+05:30)"
    return ""
