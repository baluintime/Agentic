"""Base class for indicator agents.

An indicator author writes only `warmup_bars()` and a pure `compute(df) -> df`.
Everything else — subscribing to the candle agent, evaluating on closed
candles, attaching previous values, publishing `IndicatorResult`, export — is
here.

Recomputing over the last few hundred bars on every candle close is cheap and
avoids the bugs of hand-written incremental updates.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel

from core.base_agent import BaseAgent
from core.contracts import Candle, IndicatorResult, Timeframe, candle_topic, indicator_topic

OHLCV = ["open", "high", "low", "close", "volume"]


class IndicatorAgent(BaseAgent):
    kind: ClassVar[str] = "indicator"
    outputs: ClassVar[list[str]] = []

    def __init__(
        self,
        agent_id: str,
        config: BaseModel | None = None,
        bus=None,
        store=None,
        pipeline_id: str = "",
        instrument_key: str = "",
        timeframe: Timeframe = Timeframe.M5,
        evaluate_on: str = "close",
    ) -> None:
        super().__init__(agent_id, config, bus, store, pipeline_id)
        self.instrument_key = instrument_key
        self.timeframe = timeframe
        self.evaluate_on = evaluate_on
        self.frame = pd.DataFrame(columns=OHLCV, dtype=float)
        self.frame.index.name = "start"
        self.last_result: IndicatorResult | None = None
        self.evaluations = 0

    # -- author API ----------------------------------------------------------
    def warmup_bars(self) -> int:
        """Bars needed before the output is trustworthy."""
        raise NotImplementedError

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Pure: add `self.outputs` columns to an OHLCV frame. No I/O, no state."""
        raise NotImplementedError

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        await super().start()
        self.listen(candle_topic(self.timeframe, self.instrument_key), self._on_candle)

    def seed(self, history: pd.DataFrame) -> None:
        """Load the morning history before live candles start arriving."""
        if history is None or history.empty:
            return
        df = history.copy()
        if "start" in df.columns:
            df = df.set_index("start")
        df = df[[c for c in OHLCV if c in df.columns]].astype(float)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        self.frame = df
        self.frame.index.name = "start"

    # -- bus ----------------------------------------------------------------
    async def _on_candle(self, candle: Candle) -> None:
        if candle.instrument_key != self.instrument_key or candle.timeframe != self.timeframe:
            return
        self.upsert(candle)
        if candle.is_closed or self.evaluate_on == "every_tick":
            await self.evaluate()

    def upsert(self, candle: Candle) -> None:
        self.frame.loc[candle.start, OHLCV] = [
            candle.open,
            candle.high,
            candle.low,
            candle.close,
            float(candle.volume),
        ]
        self.frame = self.frame.sort_index()

    # -- evaluation ----------------------------------------------------------
    @property
    def ready(self) -> bool:
        return len(self.frame) >= self.warmup_bars()

    def window(self) -> int:
        return max(self.warmup_bars() * 2, 300)

    def values_frame(self) -> pd.DataFrame:
        """Full frame with indicator columns; the export and the UI use this."""
        if self.frame.empty:
            return self.frame.copy()
        return self.compute(self.frame.copy())

    async def evaluate(self) -> IndicatorResult | None:
        if len(self.frame) < 2 or not self.ready:
            return None
        df = self.compute(self.frame.tail(self.window()).copy())
        if len(df) < 2:
            return None
        cur, prev = df.iloc[-1], df.iloc[-2]
        result = IndicatorResult(
            pipeline_id=self.pipeline_id,
            indicator=self.name,
            timeframe=self.timeframe,
            candle=_candle(self.instrument_key, self.timeframe, df.index[-1], cur, True),
            prev_candle=_candle(self.instrument_key, self.timeframe, df.index[-2], prev, True),
            values=_row_values(cur, self.outputs),
            prev_values=_row_values(prev, self.outputs),
        )
        # Both halves must be real numbers: a NaN previous value would make every
        # "crossed above" comparison silently False.
        if any(pd.isna(v) for v in (*result.values.values(), *result.prev_values.values())):
            return None
        self.last_result = result
        self.evaluations += 1
        await self.emit(indicator_topic(self.pipeline_id), result)
        return result

    # -- UI / export ---------------------------------------------------------
    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {
                "instrument": self.instrument_key,
                "timeframe": self.timeframe.value,
                "bars": len(self.frame),
                "warmup_bars": self.warmup_bars(),
                "ready": self.ready,
                "evaluations": self.evaluations,
                "values": _rounded(self.last_result),
            }
        )
        return base

    def export_frames(self) -> dict[str, pd.DataFrame]:
        df = self.values_frame()
        if df.empty:
            return {}
        return {f"{self.name}_{self.timeframe.value}": df.reset_index()}


def _rounded(result: IndicatorResult | None) -> dict[str, float]:
    return {k: round(v, 4) for k, v in (result.values if result else {}).items()}


def _row_values(row: pd.Series, outputs: list[str]) -> dict[str, float]:
    values = {name: float(row[name]) for name in outputs if name in row}
    for ohlc in ("open", "high", "low", "close"):
        values[ohlc] = float(row[ohlc])
    return values


def _candle(key: str, tf: Timeframe, start, row: pd.Series, closed: bool) -> Candle:
    return Candle(
        instrument_key=key,
        timeframe=tf,
        start=start.to_pydatetime() if hasattr(start, "to_pydatetime") else start,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row.get("volume", 0) or 0),
        is_closed=closed,
    )
