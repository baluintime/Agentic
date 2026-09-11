"""MACD: the difference between two EMAs, its signal line and the histogram."""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, Field

from core.indicator_base import IndicatorAgent


class Config(BaseModel):
    fast: int = Field(12, ge=1, description="Fast EMA span")
    slow: int = Field(26, ge=2, description="Slow EMA span")
    signal: int = Field(9, ge=1, description="Signal EMA span")
    source: str = Field("close", description="Price column the EMAs run on")


class MacdAgent(IndicatorAgent):
    name = "macd"
    description = "MACD line, signal line and histogram"
    Config = Config
    outputs = ["macd", "signal", "histogram"]

    def warmup_bars(self) -> int:
        # EMAs need roughly 3x their span to settle; the signal line adds its own.
        return 3 * self.config.slow + self.config.signal

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        source = df[self.config.source]
        fast = source.ewm(span=self.config.fast, adjust=False).mean()
        slow = source.ewm(span=self.config.slow, adjust=False).mean()
        df["macd"] = fast - slow
        df["signal"] = df["macd"].ewm(span=self.config.signal, adjust=False).mean()
        df["histogram"] = df["macd"] - df["signal"]
        return df
