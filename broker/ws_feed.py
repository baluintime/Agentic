"""Market Data Hub: exactly one market-data WebSocket for the whole process.

Docs: https://upstox.com/developer/api-documentation/v3/market-data-feed/
Subscriptions are reference counted, so an instrument is unsubscribed only when
the last agent using it goes away. Reconnects use exponential back-off and
resubscribe everything that is still referenced.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any, ClassVar, Protocol

from core import clock
from core.base_agent import BaseAgent
from core.contracts import Tick, system_topic, tick_topic
from core.rest_urls import MARKET_FEED_WS


class FeedClient(Protocol):
    """What the hub needs from a feed transport (real or fake)."""

    async def connect(self) -> None: ...

    async def subscribe(self, keys: list[str]) -> None: ...

    async def unsubscribe(self, keys: list[str]) -> None: ...

    def messages(self) -> AsyncIterator[dict[str, Any]]: ...

    async def close(self) -> None: ...


class MarketDataHub(BaseAgent):
    """Fans one tick stream out to every subscriber on the bus."""

    kind: ClassVar[str] = "data"
    name: ClassVar[str] = "market_data_hub"

    def __init__(self, *args: Any, client: FeedClient | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.client = client
        self.refs: Counter[str] = Counter()
        self.prices: dict[str, Tick] = {}  # last tick per instrument, for the UI
        self.last_tick_at: datetime | None = None
        self.ticks = 0
        self.reconnects = 0
        self.connected = False
        self.max_backoff = 60.0
        self._task: asyncio.Task | None = None
        self._stopping = False

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        await super().start()
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="market-data-hub")

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self.client:
            await self.client.close()
        self.connected = False
        await super().stop()

    # -- ref-counted subscriptions ------------------------------------------
    async def subscribe(self, instrument_key: str) -> None:
        self.refs[instrument_key] += 1
        if self.refs[instrument_key] == 1 and self.client and self.connected:
            await self.client.subscribe([instrument_key])

    async def subscribe_many(self, keys: list[str]) -> None:
        fresh = []
        for key in keys:
            self.refs[key] += 1
            if self.refs[key] == 1:
                fresh.append(key)
        if fresh and self.client and self.connected:
            await self.client.subscribe(fresh)

    async def unsubscribe(self, instrument_key: str) -> None:
        if self.refs[instrument_key] <= 0:
            return
        self.refs[instrument_key] -= 1
        if self.refs[instrument_key] == 0:
            del self.refs[instrument_key]
            if self.client and self.connected:
                await self.client.unsubscribe([instrument_key])

    async def unsubscribe_many(self, keys: list[str]) -> None:
        for key in keys:
            await self.unsubscribe(key)

    @property
    def subscriptions(self) -> list[str]:
        return sorted(self.refs)

    # -- connection loop -----------------------------------------------------
    async def _run(self) -> None:
        backoff = 1.0
        while not self._stopping:
            if self.client is None:
                await asyncio.sleep(0.5)
                continue
            try:
                await self.client.connect()
                self.connected = True
                backoff = 1.0
                if self.subscriptions:
                    await self.client.subscribe(self.subscriptions)
                async for message in self.client.messages():
                    await self._handle(message)
                raise ConnectionError("feed stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.connected = False
                if self._stopping:
                    return
                self.reconnects += 1
                self.log.warning("feed disconnected (%s); reconnecting in %.0fs", exc, backoff)
                await self.emit(
                    system_topic("feed.disconnected"),
                    _system("feed.disconnected", str(exc), {"reconnects": self.reconnects}),
                )
                await asyncio.sleep(backoff)
                backoff = min(self.max_backoff, backoff * 2)

    async def _handle(self, message: dict[str, Any]) -> None:
        for tick in ticks_from(message):
            self.ticks += 1
            self.last_tick_at = tick.ts
            self.prices[tick.instrument_key] = tick
            await self.emit(tick_topic(tick.instrument_key), tick)

    # -- UI ------------------------------------------------------------------
    def price(self, instrument_key: str) -> float | None:
        tick = self.prices.get(instrument_key)
        return tick.ltp if tick else None

    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {
                "connected": self.connected,
                "subscriptions": len(self.refs),
                "ticks": self.ticks,
                "reconnects": self.reconnects,
                "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            }
        )
        return base


def ticks_from(message: dict[str, Any]) -> list[Tick]:
    """Normalised feed message -> Ticks.

    Expected shape: {"feeds": {"<instrument_key>": {"ltp": .., "ts": .., "volume": .., "oi": ..}}}
    A single {"instrument_key": .., "ltp": ..} message is also accepted.
    """
    feeds = message.get("feeds")
    if feeds is None:
        if "instrument_key" in message and "ltp" in message:
            feeds = {message["instrument_key"]: message}
        else:
            return []
    out = []
    for key, payload in feeds.items():
        ltp = payload.get("ltp")
        if ltp is None:
            continue
        out.append(
            Tick(
                instrument_key=key,
                ts=_as_time(payload.get("ts")),
                ltp=float(ltp),
                cum_volume=_as_int(payload.get("volume")),
                oi=_as_float(payload.get("oi")),
            )
        )
    return out


def decode_feed(frame: bytes) -> dict[str, Any]:
    """Decode one v3 protobuf frame into the hub's normalised message shape.

    The schema comes from the official SDK's generated module rather than a
    hand-written guess, so a change on Upstox's side is a dependency bump.
    Docs: https://upstox.com/developer/api-documentation/v3/market-data-feed/
    """
    from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as pb

    response = pb.FeedResponse.FromString(frame)
    feeds: dict[str, dict[str, Any]] = {}
    for key, feed in response.feeds.items():
        payload = _feed_payload(feed)
        if payload is not None:
            feeds[key] = payload
    return {"feeds": feeds}


def _feed_payload(feed: Any) -> dict[str, Any] | None:
    """One instrument's slice of a frame: LTP, exchange timestamp, volume, OI."""
    which = feed.WhichOneof("FeedUnion")
    if which == "ltpc":
        return _ltpc(feed.ltpc)
    if which != "fullFeed":
        return None  # option greeks / depth-only frames carry no trade price
    full = feed.fullFeed
    inner = full.WhichOneof("FullFeedUnion")
    if inner == "marketFF":
        payload = _ltpc(full.marketFF.ltpc)
        if payload is None:
            return None
        # vtt is the day's cumulative traded volume; candles want the delta.
        payload["volume"] = int(full.marketFF.vtt or 0)
        payload["oi"] = float(full.marketFF.oi or 0) or None
        return payload
    if inner == "indexFF":
        return _ltpc(full.indexFF.ltpc)  # indices have no volume or open interest
    return None


def _ltpc(ltpc: Any) -> dict[str, Any] | None:
    if not ltpc.ltp:
        return None
    return {"ltp": float(ltpc.ltp), "ts": int(ltpc.ltt) or None}


class UpstoxFeedClient:
    """The real Upstox v3 market-data WebSocket.

    Connects straight to the feed URL with a Bearer header — v3 needs no
    authorize redirect — and sends subscription requests as binary JSON frames.
    Docs: https://upstox.com/developer/api-documentation/v3/market-data-feed/
    """

    def __init__(
        self,
        rest,
        decode: Callable[[bytes], dict[str, Any]] | None = None,
        url: str = MARKET_FEED_WS,
        mode: str = "full",
    ) -> None:
        self.rest = rest
        self.decode = decode or decode_feed
        self.url = url
        self.mode = mode  # "full" carries volume and OI; "ltpc" is price only
        self._ws: Any = None

    async def connect(self) -> None:
        import websockets  # imported lazily so tests never need the dependency

        if not getattr(self.rest, "access_token", None):
            raise ConnectionError("no access token — log in to Upstox first")
        self._ws = await websockets.connect(
            self.url,
            additional_headers={"Authorization": f"Bearer {self.rest.access_token}"},
            max_size=None,
        )

    async def subscribe(self, keys: list[str]) -> None:
        await self._send("sub", keys, mode=self.mode)

    async def unsubscribe(self, keys: list[str]) -> None:
        await self._send("unsub", keys)

    async def _send(self, method: str, keys: list[str], mode: str | None = None) -> None:
        if not self._ws or not keys:
            return
        data: dict[str, Any] = {"instrumentKeys": keys}
        if mode:
            data["mode"] = mode
        payload = {"guid": uuid.uuid4().hex, "method": method, "data": data}
        await self._ws.send(json.dumps(payload).encode())  # binary frame

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        if self._ws is None:
            return
        async for frame in self._ws:
            if isinstance(frame, bytes):
                yield self.decode(frame)
            else:
                yield json.loads(frame)

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


def _system(kind: str, message: str, payload: dict) -> Any:
    from core.contracts import SystemEvent

    return SystemEvent(kind=kind, ts=clock.now(), message=message, payload=payload)


def _as_time(value: Any) -> datetime:
    if value is None:
        return clock.now()
    if isinstance(value, datetime):
        return clock.ist(value)
    if isinstance(value, int | float):
        seconds = float(value) / 1000 if float(value) > 1e11 else float(value)
        return datetime.fromtimestamp(seconds, tz=clock.IST)
    return clock.ist(datetime.fromisoformat(str(value)))


def _as_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _as_float(value: Any) -> float | None:
    return None if value is None else float(value)
