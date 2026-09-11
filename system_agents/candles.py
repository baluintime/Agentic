"""Candle agents: tick -> 1m -> 5m, plus the daily candle.

Candles align to the NSE session start (09:15 IST), volume comes from the delta
of the feed's cumulative day volume, and every candle emits `is_closed=False`
updates followed by exactly one `is_closed=True` event.

History is loaded once per morning and cached as Parquet; after a WebSocket
reconnect the gap is back-filled from the intraday candle API so indicators
never run on candles with missing ticks.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import date, datetime, timedelta
from typing import Any, ClassVar

import pandas as pd

from core import clock
from core.base_agent import BaseAgent
from core.contracts import Candle, Tick, Timeframe, candle_topic, tick_topic
from core.indicator_base import OHLCV

UNIT_FOR = {Timeframe.M1: ("minutes", 1), Timeframe.M5: ("minutes", 5), Timeframe.D1: ("days", 1)}


class CandleAgent(BaseAgent):
    """One instrument, one timeframe. 5m is built from closed 1m candles."""

    kind: ClassVar[str] = "data"
    name: ClassVar[str] = "candles"

    def __init__(
        self,
        agent_id: str,
        *,
        instrument_key: str,
        timeframe: Timeframe,
        bus=None,
        store=None,
        rest=None,
        history_days: int = 5,
        warmup_bars: int = 0,
        auto_close: bool = True,
    ) -> None:
        super().__init__(agent_id, None, bus, store)
        self.instrument_key = instrument_key
        self.timeframe = timeframe
        self.rest = rest
        self.history_days = history_days
        self.warmup_bars = warmup_bars
        self.auto_close = auto_close
        self.frame = pd.DataFrame(columns=OHLCV, dtype=float)
        self.frame.index.name = "start"
        self.current: Candle | None = None
        self.history_loaded = False
        self.closed_count = 0
        self._last_cum_volume: int | None = None
        self._volume_day: date | None = None
        self._closer: asyncio.Task | None = None

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        await super().start()
        if self.timeframe is Timeframe.M5:
            self.listen(candle_topic(Timeframe.M1, self.instrument_key), self._on_source_candle)
        else:
            self.listen(tick_topic(self.instrument_key), self._on_tick)
        self.listen("system.feed.reconnected", self._on_reconnect)
        if self.auto_close and self.timeframe is not Timeframe.D1:
            self._closer = asyncio.create_task(self._close_loop(), name=f"close:{self.agent_id}")

    async def stop(self) -> None:
        if self._closer:
            self._closer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._closer
            self._closer = None
        await super().stop()

    # -- bucketing -----------------------------------------------------------
    def bucket(self, moment: datetime) -> datetime:
        if self.timeframe is Timeframe.D1:
            return clock.combine(clock.ist(moment).date(), clock.MARKET_OPEN)
        return clock.floor_to(moment, self.timeframe.seconds)

    def bucket_end(self, start: datetime) -> datetime:
        if self.timeframe is Timeframe.D1:
            return clock.combine(start.date(), clock.MARKET_CLOSE)
        return start + timedelta(seconds=self.timeframe.seconds)

    # -- ingestion -----------------------------------------------------------
    async def _on_tick(self, tick: Tick) -> None:
        if tick.instrument_key != self.instrument_key:
            return
        await self.add_tick(tick)

    async def add_tick(self, tick: Tick) -> None:
        start = self.bucket(tick.ts)
        volume = self._volume_delta(tick)
        if self.current is not None and start > self.current.start:
            await self._close_current()
        if self.current is None or start != self.current.start:
            self.current = Candle(
                instrument_key=self.instrument_key,
                timeframe=self.timeframe,
                start=start,
                open=tick.ltp,
                high=tick.ltp,
                low=tick.ltp,
                close=tick.ltp,
                volume=volume,
            )
        else:
            cur = self.current
            self.current = Candle(
                instrument_key=cur.instrument_key,
                timeframe=cur.timeframe,
                start=cur.start,
                open=cur.open,
                high=max(cur.high, tick.ltp),
                low=min(cur.low, tick.ltp),
                close=tick.ltp,
                volume=cur.volume + volume,
            )
        self._store(self.current)
        await self.emit(candle_topic(self.timeframe, self.instrument_key), self.current)

    async def _on_source_candle(self, candle: Candle) -> None:
        """5-minute candles are aggregated from closed 1-minute candles."""
        if candle.instrument_key != self.instrument_key or not candle.is_closed:
            return
        start = self.bucket(candle.start)
        if self.current is not None and start > self.current.start:
            await self._close_current()
        if self.current is None or start != self.current.start:
            self.current = Candle(
                instrument_key=self.instrument_key,
                timeframe=self.timeframe,
                start=start,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
        else:
            cur = self.current
            self.current = Candle(
                instrument_key=cur.instrument_key,
                timeframe=cur.timeframe,
                start=cur.start,
                open=cur.open,
                high=max(cur.high, candle.high),
                low=min(cur.low, candle.low),
                close=candle.close,
                volume=cur.volume + candle.volume,
            )
        self._store(self.current)
        await self.emit(candle_topic(self.timeframe, self.instrument_key), self.current)
        source_end = candle.start + timedelta(seconds=candle.timeframe.seconds)
        if source_end >= self.bucket_end(self.current.start):
            await self._close_current()

    def _volume_delta(self, tick: Tick) -> int:
        """Feed volume is cumulative for the day; candles want the delta."""
        if tick.cum_volume is None:
            return 0
        day = clock.ist(tick.ts).date()
        if self._volume_day != day:
            self._volume_day, self._last_cum_volume = day, tick.cum_volume
            return 0
        previous = self._last_cum_volume
        self._last_cum_volume = tick.cum_volume
        if previous is None or tick.cum_volume < previous:
            return 0
        return tick.cum_volume - previous

    # -- closing -------------------------------------------------------------
    async def _close_current(self) -> None:
        if self.current is None:
            return
        closed = Candle(**{**self.current.__dict__, "is_closed": True})
        self.current = None
        self._store(closed)
        self.closed_count += 1
        await self.emit(candle_topic(self.timeframe, self.instrument_key), closed)

    async def close_due(self, now: datetime | None = None) -> None:
        """Close a candle whose window has elapsed even if no tick arrived."""
        now = now or clock.now()
        if self.current is not None and now >= self.bucket_end(self.current.start):
            await self._close_current()

    async def _close_loop(self) -> None:
        """Cadence is real time; the decision uses the clock, so replay never spins."""
        while True:
            await asyncio.sleep(1.0)
            await self.close_due()

    def _store(self, candle: Candle) -> None:
        self.frame.loc[candle.start, OHLCV] = [
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            float(candle.volume),
        ]
        self.frame = self.frame.sort_index()

    # -- history -------------------------------------------------------------
    def required_bars(self) -> int:
        """History length is driven by the attached indicators' warm-up."""
        per_day = {Timeframe.M1: 375, Timeframe.M5: 75, Timeframe.D1: 1}[self.timeframe]
        needed_days = -(-self.warmup_bars // per_day) if self.warmup_bars else 0
        return max(self.history_days, needed_days) * per_day

    def history_days_needed(self) -> int:
        per_day = {Timeframe.M1: 375, Timeframe.M5: 75, Timeframe.D1: 1}[self.timeframe]
        return max(self.history_days, -(-self.warmup_bars // per_day) if self.warmup_bars else 0)

    async def load_history(self, rest=None, today: date | None = None) -> int:
        """Parquet cache first; only then the historical + intraday APIs."""
        rest = rest or self.rest
        today = today or clock.now().date()
        cached = self._cached_history(today)
        if cached is not None:
            self._merge(cached)
            self.history_loaded = True
            return len(cached)
        if rest is None:
            return 0
        unit, interval = UNIT_FOR[self.timeframe]
        start = today - timedelta(days=max(2, int(self.history_days_needed() * 1.6)))
        frame = pd.DataFrame()
        try:
            rows = await rest.historical_candles(self.instrument_key, unit, interval, today, start)
            frame = candles_to_frame(rows)
        except Exception as exc:
            self.fail(f"history load failed: {exc}")
        try:
            intraday = candles_to_frame(
                await rest.intraday_candles(self.instrument_key, unit, interval)
            )
            frame = _concat(frame, intraday)
        except Exception as exc:
            self.log.info("intraday load skipped: %s", exc)
        if frame.empty:
            return 0
        frame = frame.tail(self.required_bars())
        self._merge(frame)
        self._cache_history(frame, today)
        self.history_loaded = True
        return len(frame)

    async def backfill(self, rest=None, since: datetime | None = None) -> int:
        """After a reconnect, replace today's candles from the intraday API."""
        rest = rest or self.rest
        if rest is None:
            return 0
        unit, interval = UNIT_FOR[self.timeframe]
        try:
            frame = candles_to_frame(
                await rest.intraday_candles(self.instrument_key, unit, interval)
            )
        except Exception as exc:
            self.fail(f"backfill failed: {exc}")
            return 0
        if since is not None:
            frame = frame[frame.index >= since]
        if frame.empty:
            return 0
        self._merge(frame)
        return len(frame)

    async def _on_reconnect(self, event: Any) -> None:
        last = self.frame.index[-1].to_pydatetime() if len(self.frame) else None
        await self.backfill(since=last)

    def _merge(self, frame: pd.DataFrame) -> None:
        merged = pd.concat([self.frame, frame])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        self.frame = merged
        self.frame.index.name = "start"

    def _cached_history(self, today: date) -> pd.DataFrame | None:
        if self.store is None:
            return None
        path = self.store.history_path(self.instrument_key, self.timeframe.value, today)
        if not path.exists():
            return None
        frame = pd.read_parquet(path)
        return frame.set_index("start") if "start" in frame.columns else frame

    def _cache_history(self, frame: pd.DataFrame, today: date) -> None:
        if self.store is None:
            return
        path = self.store.history_path(self.instrument_key, self.timeframe.value, today)
        frame.reset_index().to_parquet(path, index=False)

    # -- UI / export ---------------------------------------------------------
    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {
                "instrument": self.instrument_key,
                "timeframe": self.timeframe.value,
                "bars": len(self.frame),
                "closed": self.closed_count,
                "history_loaded": self.history_loaded,
                "last_close": float(self.frame["close"].iloc[-1]) if len(self.frame) else None,
            }
        )
        return base

    def export_frames(self) -> dict[str, pd.DataFrame]:
        if self.frame.empty:
            return {}
        return {f"candles_{self.timeframe.value}": self.frame.reset_index()}


def candles_to_frame(rows: list[list[Any]]) -> pd.DataFrame:
    """Upstox candle rows: [timestamp, open, high, low, close, volume, oi]."""
    if not rows:
        return pd.DataFrame(columns=OHLCV, dtype=float)
    frame = pd.DataFrame(
        [r[:6] for r in rows], columns=["start", "open", "high", "low", "close", "volume"]
    )
    frame["start"] = pd.to_datetime(frame["start"], format="mixed", utc=True).dt.tz_convert(
        clock.IST
    )
    frame = frame.set_index("start").astype(float).sort_index()
    frame.index.name = "start"
    return frame


def _concat(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    if left.empty:
        return right
    if right.empty:
        return left
    merged = pd.concat([left, right])
    return merged[~merged.index.duplicated(keep="last")].sort_index()
