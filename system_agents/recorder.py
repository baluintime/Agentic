"""Recorder: every tick and every closed candle to Parquet.

The historical API supplies candles, not ticks, so tick history has to be
self-recorded. After a few sessions this folder is your own tick archive and the
Replay agent can drive the whole pipeline from it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, ClassVar

import pandas as pd

from core import clock
from core.base_agent import BaseAgent
from core.contracts import Candle, Tick


class RecorderAgent(BaseAgent):
    kind: ClassVar[str] = "system"
    name: ClassVar[str] = "recorder"
    description: ClassVar[str] = "Writes ticks and closed candles to Parquet"

    def __init__(self, *args: Any, flush_every: int = 500, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.flush_every = flush_every
        self.tick_buffer: dict[tuple[str, date], list[dict]] = defaultdict(list)
        self.candle_buffer: dict[tuple[str, str, date], list[dict]] = defaultdict(list)
        self.ticks_written = 0
        self.candles_written = 0
        self._pending = 0

    async def start(self) -> None:
        await super().start()
        self.listen("tick.*", self._on_tick)
        self.listen("candle.*.*", self._on_candle)

    async def stop(self) -> None:
        self.flush()
        await super().stop()

    async def _on_tick(self, tick: Tick) -> None:
        day = clock.ist(tick.ts).date()
        self.tick_buffer[(tick.instrument_key, day)].append(
            {"ts": tick.ts, "ltp": tick.ltp, "cum_volume": tick.cum_volume, "oi": tick.oi}
        )
        self._pending += 1
        if self._pending >= self.flush_every:
            self.flush()

    async def _on_candle(self, candle: Candle) -> None:
        if not candle.is_closed:
            return
        day = clock.ist(candle.start).date()
        key = (candle.instrument_key, candle.timeframe.value, day)
        self.candle_buffer[key].append(
            {
                "start": candle.start,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
        )
        self._pending += 1

    def flush(self) -> None:
        if self.store is None:
            self.tick_buffer.clear()
            self.candle_buffer.clear()
            self._pending = 0
            return
        for (instrument, day), rows in list(self.tick_buffer.items()):
            if rows:
                _append(self.store.tick_path(instrument, day), rows)
                self.ticks_written += len(rows)
            del self.tick_buffer[(instrument, day)]
        for (instrument, timeframe, day), rows in list(self.candle_buffer.items()):
            if rows:
                _append(self.store.candle_path(instrument, timeframe, day), rows)
                self.candles_written += len(rows)
            del self.candle_buffer[(instrument, timeframe, day)]
        self._pending = 0

    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {
                "ticks_written": self.ticks_written,
                "candles_written": self.candles_written,
                "buffered": self._pending,
            }
        )
        return base


def _append(path, rows: list[dict]) -> None:
    frame = pd.DataFrame(rows)
    if path.exists():
        frame = pd.concat([pd.read_parquet(path), frame], ignore_index=True)
    frame.to_parquet(path, index=False)
