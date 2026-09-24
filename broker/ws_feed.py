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


class FeedUnavailable(RuntimeError):
    """The feed cannot work until something is fixed by hand — do not retry."""


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
        self.fatal_error = ""
        self.bad_messages = 0
        self.last_disconnect: datetime | None = None
        self.last_disconnect_reason = ""
        self.connected_since: datetime | None = None
        self.base_backoff = 1.0
        self.max_backoff = 60.0
        self._task: asyncio.Task | None = None
        self._stopping = False

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        await super().start()
        self._stopping = False
        self.fatal_error = ""
        self._task = asyncio.create_task(self._run(), name="market-data-hub")

    async def restart(self) -> None:
        """Retry after a fatal stop, once whatever caused it has been fixed."""
        await self.stop()
        await self.start()

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
        backoff = self.base_backoff
        while not self._stopping:
            if self.client is None:
                await asyncio.sleep(0.5)
                continue
            try:
                await self.client.connect()
                self.connected = True
                self.connected_since = clock.now()
                backoff = self.base_backoff
                if self.subscriptions:
                    await self.client.subscribe(self.subscriptions)
                async for message in self.client.messages():
                    await self._handle(message)
                raise ConnectionError("feed stream ended")
            except asyncio.CancelledError:
                raise
            except FeedUnavailable as exc:
                # Retrying cannot install a package or mint a token: stop and say so.
                self.connected = False
                self.fatal_error = str(exc)
                self.fail(f"feed unavailable: {exc}")
                await self.emit(
                    system_topic("feed.unavailable"),
                    _system("feed.unavailable", str(exc), {}),
                )
                return
            except Exception as exc:
                self.connected = False
                if self._stopping:
                    return
                self.reconnects += 1
                self.last_disconnect = clock.now()
                self.last_disconnect_reason = describe(exc)
                self.log.warning(
                    "feed disconnected after %s (%s); reconnecting in %.0fs",
                    _held_for(self.connected_since),
                    self.last_disconnect_reason,
                    backoff,
                )
                await self.emit(
                    system_topic("feed.disconnected"),
                    _system(
                        "feed.disconnected",
                        self.last_disconnect_reason,
                        {"reconnects": self.reconnects},
                    ),
                )
                await asyncio.sleep(backoff)
                backoff = min(self.max_backoff, backoff * 2)
            finally:
                # Always hand the socket back. Without this every retry leaked a
                # live connection, and the broker's connection cap then answered
                # 403 to every further attempt.
                self.connected = False
                with contextlib.suppress(Exception):
                    await self.client.close()

    async def _handle(self, message: dict[str, Any]) -> None:
        try:
            ticks = ticks_from(message)
        except Exception:  # a malformed frame is not a reason to drop the feed
            self.log.exception("could not read a feed message")
            self.bad_messages += 1
            return
        for tick in ticks:
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
                "error": self.fatal_error,
                "last_disconnect": (
                    self.last_disconnect.strftime("%H:%M:%S") if self.last_disconnect else None
                ),
                "last_disconnect_reason": self.last_disconnect_reason,
                "bad_messages": self.bad_messages or None,
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
    response = _protobuf().FeedResponse.FromString(frame)
    feeds: dict[str, dict[str, Any]] = {}
    for key, feed in response.feeds.items():
        payload = _feed_payload(feed)
        if payload is not None:
            feeds[key] = payload
    return {"feeds": feeds}


def _protobuf() -> Any:
    """The generated v3 schema, or a message saying exactly how to get it."""
    try:
        from upstox_client.feeder.proto import MarketDataFeedV3_pb2 as pb
    except ImportError as exc:  # noqa: F841
        raise FeedUnavailable(
            'the market feed decoder is missing — run: pip install -e ".[dev]" '
            "(it installs upstox-python-sdk, which ships the v3 protobuf schema)"
        ) from exc
    return pb


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
        ping_interval: float = 20,
        ping_timeout: float = 20,
    ) -> None:
        self.rest = rest
        self.decode = decode or decode_feed
        self.url = url
        self.mode = mode  # "full" carries volume and OI; "ltpc" is price only
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.undecodable = 0
        self._ws: Any = None

    async def connect(self) -> None:
        import websockets  # imported lazily so tests never need the dependency

        if self.decode is decode_feed:
            _protobuf()  # fail before opening a socket, not after the first frame
        if not getattr(self.rest, "access_token", None):
            raise FeedUnavailable("no access token — log in to Upstox first")
        self._ws = await websockets.connect(
            self.url,
            additional_headers={"Authorization": f"Bearer {self.rest.access_token}"},
            max_size=None,
            # Detect a half-open connection quickly instead of sitting on a dead
            # socket, and give the broker room to answer before declaring it lost.
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
            close_timeout=5,
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
            try:
                yield self.decode(frame) if isinstance(frame, bytes) else json.loads(frame)
            except FeedUnavailable:
                raise
            except Exception:
                # Skip the frame, keep the connection: dropping it here would
                # reconnect on every message the decoder does not recognise.
                self.undecodable += 1
                continue

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None


def describe(exc: Exception) -> str:
    """Spell out a WebSocket close so the next report can be diagnosed.

    `str(exc)` on a closed connection is often empty or "no close frame
    received or sent", which says nothing about why the broker hung up.
    """
    code = getattr(getattr(exc, "rcvd", None), "code", None) or getattr(exc, "code", None)
    reason = getattr(getattr(exc, "rcvd", None), "reason", "") or getattr(exc, "reason", "")
    text = str(exc) or type(exc).__name__
    if code:
        return f"{text} [close {code}{': ' + reason if reason else ''}]"
    return text


def _held_for(since: datetime | None) -> str:
    if since is None:
        return "no connection"
    seconds = (clock.now() - since).total_seconds()
    return f"{seconds:.0f}s" if seconds < 120 else f"{seconds / 60:.1f}m"


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
