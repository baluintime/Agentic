"""One-line description of the indicator."""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel

from core.indicator_base import IndicatorAgent


class Config(BaseModel):
    period: int = 14


class TemplateIndicator(IndicatorAgent):
    name = "_template"
    description = "Describe the indicator in one line"
    Config = Config
    outputs = ["value"]

    def warmup_bars(self) -> int:
        return self.config.period * 3

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Pure: no I/O, no state, no bus. Adds `outputs` columns to an OHLCV frame."""
        df["value"] = df["close"].rolling(self.config.period).mean()
        return df
