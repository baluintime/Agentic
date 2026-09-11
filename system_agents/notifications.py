"""Notification agent: fills, stop-loss hits, disconnects and risk breaches.

The transport is injected, so tests (and anyone without a bot token) run it
fully offline. Telegram is the default because it is the simplest to set up.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, Field

from core import clock
from core.base_agent import BaseAgent
from core.contracts import OrderEvent, OrderStatus, SystemEvent

Sender = Callable[[str], Awaitable[None]]

NOTIFY_STATUSES = {
    OrderStatus.FILLED.value,
    OrderStatus.PARTIAL.value,
    OrderStatus.REJECTED.value,
    OrderStatus.TARGET_HIT.value,
    OrderStatus.SL_HIT.value,
    OrderStatus.SQUARED_OFF.value,
}
NOTIFY_SYSTEM = ("kill", "risk.block", "health.data.stale", "feed.disconnected", "squareoff.start")


class NotificationConfig(BaseModel):
    enabled: bool = Field(False, description="Off unless a bot token is configured")
    order_events: bool = True
    system_events: bool = True


class NotificationAgent(BaseAgent):
    kind: ClassVar[str] = "system"
    name: ClassVar[str] = "notifications"
    Config: ClassVar[type[BaseModel]] = NotificationConfig
    description: ClassVar[str] = "Telegram alerts for fills, SL hits, disconnects and breaches"

    def __init__(
        self, *args: Any, sender: Sender | None = None, env: dict | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.env = env if env is not None else dict(os.environ)
        self.sender = sender or self._telegram
        self.sent: list[str] = []
        self.failures = 0

    @property
    def configured(self) -> bool:
        return bool(self.env.get("TELEGRAM_BOT_TOKEN") and self.env.get("TELEGRAM_CHAT_ID"))

    @property
    def active(self) -> bool:
        return bool(getattr(self.config, "enabled", False))

    async def start(self) -> None:
        await super().start()
        if getattr(self.config, "order_events", True):
            self.listen("order.event.*", self._on_order_event)
        if getattr(self.config, "system_events", True):
            self.listen("system.*", self._on_system_event)

    async def _on_order_event(self, event: OrderEvent) -> None:
        if event.status not in NOTIFY_STATUSES:
            return
        price = f" at {event.fill_price}" if event.fill_price is not None else ""
        await self.notify(
            f"{event.status} {event.filled_qty}{price} ({event.origin_agent_id})"
            + (f" — {event.message}" if event.message else "")
        )

    async def _on_system_event(self, event: SystemEvent) -> None:
        if not any(event.kind.startswith(kind) for kind in NOTIFY_SYSTEM):
            return
        await self.notify(f"{event.kind}: {event.message}".strip(": "))

    async def notify(self, message: str) -> None:
        stamped = f"[{clock.now():%H:%M:%S}] {message}"
        self.sent.append(stamped)
        if not self.active:
            return
        try:
            await self.sender(stamped)
        except Exception as exc:  # an alert failure must never affect trading
            self.failures += 1
            self.log.warning("notification failed: %s", exc)

    async def _telegram(self, message: str) -> None:
        if not self.configured:
            return
        url = f"https://api.telegram.org/bot{self.env['TELEGRAM_BOT_TOKEN']}/sendMessage"
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={"chat_id": self.env["TELEGRAM_CHAT_ID"], "text": message})

    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {
                "enabled": self.active,
                "configured": self.configured,
                "sent": len(self.sent),
                "failures": self.failures,
                "last": self.sent[-1] if self.sent else "",
            }
        )
        return base
