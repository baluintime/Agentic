"""Ichimoku Kinko Hyo: conversion, base, both clouds and the lagging span.

The spans are displaced 26 bars, so "Span A crosses Span B" is ambiguous. Both
versions are published and the strategy picks one:

* `*_projected` — computed from today's bars and plotted 26 bars *ahead*
  (a cross here is the classic Kumo twist);
* `*_current`  — the cloud under today's price, computed 26 bars *ago*.

Nothing here looks ahead: the current cloud is a backward shift of values that
already existed.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, Field

from core.indicator_base import IndicatorAgent


class Config(BaseModel):
    tenkan: int = Field(9, ge=1, description="Conversion line period")
    kijun: int = Field(26, ge=1, description="Base line period")
    senkou_b: int = Field(52, ge=1, description="Leading Span B period")
    displacement: int = Field(26, ge=1, description="Bars the cloud is displaced by")


class IchimokuAgent(IndicatorAgent):
    name = "ichimoku"
    description = "Tenkan, Kijun, projected and current cloud, lagging span"
    Config = Config
    outputs = [
        "tenkan",
        "kijun",
        "span_a_projected",
        "span_b_projected",
        "span_a_current",
        "span_b_current",
        "chikou",
        "chikou_reference",
    ]

    def warmup_bars(self) -> int:
        # The current cloud is the projected cloud shifted back by `displacement`.
        return self.config.senkou_b + self.config.displacement

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        cfg = self.config
        df["tenkan"] = _midpoint(df, cfg.tenkan)
        df["kijun"] = _midpoint(df, cfg.kijun)
        df["span_a_projected"] = (df["tenkan"] + df["kijun"]) / 2
        df["span_b_projected"] = _midpoint(df, cfg.senkou_b)
        df["span_a_current"] = df["span_a_projected"].shift(cfg.displacement)
        df["span_b_current"] = df["span_b_projected"].shift(cfg.displacement)
        df["chikou"] = df["close"]
        df["chikou_reference"] = df["close"].shift(cfg.displacement)
        return df


def _midpoint(df: pd.DataFrame, period: int) -> pd.Series:
    return (df["high"].rolling(period).max() + df["low"].rolling(period).min()) / 2
