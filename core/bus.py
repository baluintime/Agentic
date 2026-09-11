"""In-process async publish/subscribe bus.

A slow or failing subscriber must never block or crash the publisher or other
subscribers: every subscription owns a bounded queue drained by its own task,
and handler exceptions are logged and swallowed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[[Any], Awaitable[None] | None]

DEFAULT_QUEUE_SIZE = 2000


def topic_matches(pattern: str, topic: str) -> bool:
    """`*` matches one segment; a trailing `*` matches one or more segments.

    `candle.5m.*` matches `candle.5m.NSE|123`; `system.*` matches
    `system.squareoff.done`; `#` matches everything.
    """
    if pattern == "#" or pattern == topic:
        return True
    p_parts = pattern.split(".")
    t_parts = topic.split(".")
    if p_parts[-1] == "*":
        if len(t_parts) < len(p_parts):
            return False
        head_ok = all(p == "*" or p == t for p, t in zip(p_parts[:-1], t_parts, strict=False))
        return head_ok and len(t_parts) >= len(p_parts)
    if len(p_parts) != len(t_parts):
        return False
    return all(p == "*" or p == t for p, t in zip(p_parts, t_parts, strict=True))


@dataclass
class Subscription:
    pattern: str
    handler: Handler
    name: str = "anon"
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(DEFAULT_QUEUE_SIZE))
    task: asyncio.Task | None = None
    dropped: int = 0
    delivered: int = 0


class EventBus:
    """Topic based pub/sub. One queue + worker task per subscription."""

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._subs: list[Subscription] = []
        self._queue_size = queue_size
        self._running = False
        self._published = 0

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        self._running = True
        for sub in self._subs:
            self._ensure_worker(sub)

    async def stop(self) -> None:
        self._running = False
        tasks = [s.task for s in self._subs if s.task]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for sub in self._subs:
            sub.task = None

    # -- wiring --------------------------------------------------------------
    def subscribe(self, pattern: str, handler: Handler, name: str = "anon") -> Subscription:
        sub = Subscription(
            pattern=pattern, handler=handler, name=name, queue=asyncio.Queue(self._queue_size)
        )
        self._subs.append(sub)
        if self._running:
            self._ensure_worker(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.remove(sub)
        if sub.task:
            sub.task.cancel()
            sub.task = None

    # -- traffic -------------------------------------------------------------
    async def publish(self, topic: str, message: Any) -> None:
        self._published += 1
        for sub in list(self._subs):
            if not topic_matches(sub.pattern, topic):
                continue
            try:
                sub.queue.put_nowait((topic, message))
            except asyncio.QueueFull:
                sub.dropped += 1
                log.warning(
                    "bus: queue full for %s (%s), dropped %d", sub.name, sub.pattern, sub.dropped
                )

    async def publish_and_drain(self, topic: str, message: Any) -> None:
        """Publish then wait until every subscriber has processed it (tests)."""
        await self.publish(topic, message)
        await self.drain()

    async def drain(self) -> None:
        for _ in range(100):
            await asyncio.sleep(0)
            if all(sub.queue.empty() for sub in self._subs):
                await asyncio.sleep(0)
                if all(sub.queue.empty() for sub in self._subs):
                    return
        return

    # -- internals -----------------------------------------------------------
    def _ensure_worker(self, sub: Subscription) -> None:
        if sub.task is None or sub.task.done():
            sub.task = asyncio.create_task(self._worker(sub), name=f"bus:{sub.name}")

    async def _worker(self, sub: Subscription) -> None:
        while True:
            topic, message = await sub.queue.get()
            try:
                result = sub.handler(message)
                if asyncio.iscoroutine(result):
                    await result
                sub.delivered += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("bus: handler %s failed on topic %s", sub.name, topic)
            finally:
                sub.queue.task_done()

    def stats(self) -> dict[str, Any]:
        return {
            "published": self._published,
            "subscriptions": len(self._subs),
            "dropped": sum(s.dropped for s in self._subs),
            "delivered": sum(s.delivered for s in self._subs),
        }
