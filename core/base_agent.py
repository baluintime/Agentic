"""Base class shared by every agent."""

from __future__ import annotations

import logging
from abc import ABC
from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel

from core.bus import EventBus, Handler, Subscription
from core.store import Store


class EmptyConfig(BaseModel):
    pass


class BaseAgent(ABC):
    kind: ClassVar[str] = "system"  # indicator | strategy | order | data | system
    name: ClassVar[str] = "base"
    Config: ClassVar[type[BaseModel]] = EmptyConfig
    requires: ClassVar[list[str]] = []
    description: ClassVar[str] = ""

    def __init__(
        self,
        agent_id: str,
        config: BaseModel | None = None,
        bus: EventBus | None = None,
        store: Store | None = None,
        pipeline_id: str = "",
    ) -> None:
        self.agent_id = agent_id
        self.config = config if config is not None else self.Config()
        self.bus = bus
        self.store = store
        self.pipeline_id = pipeline_id
        self.log = logging.getLogger(f"{self.kind}.{self.name}").getChild(agent_id)
        self._subs: list[Subscription] = []
        self.running = False
        self.errors: list[str] = []

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False
        for sub in self._subs:
            if self.bus:
                self.bus.unsubscribe(sub)
        self._subs.clear()

    # -- helpers -------------------------------------------------------------
    def listen(self, pattern: str, handler: Handler) -> Subscription:
        if self.bus is None:
            raise RuntimeError(f"{self.agent_id}: no bus attached")
        sub = self.bus.subscribe(pattern, handler, name=self.agent_id)
        self._subs.append(sub)
        return sub

    async def emit(self, topic: str, message: Any) -> None:
        if self.bus is None:
            raise RuntimeError(f"{self.agent_id}: no bus attached")
        await self.bus.publish(topic, message)

    def fail(self, message: str) -> None:
        self.errors.append(message)
        self.log.error(message)

    # -- UI / export ---------------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {"agent_id": self.agent_id, "name": self.name, "running": self.running}

    def export_frames(self) -> dict[str, pd.DataFrame]:
        return {}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<{type(self).__name__} {self.agent_id}>"
