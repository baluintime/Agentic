"""Loaders for the offline fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

FIXTURES = Path(__file__).resolve().parent


def candles(timeframe: str = "1m") -> pd.DataFrame:
    """One session of NIFTY candles, indexed by candle start."""
    frame = pd.read_parquet(FIXTURES / f"nifty_{timeframe}_2026-09-10.parquet")
    return frame.set_index("start")


def ticks() -> pd.DataFrame:
    return pd.read_parquet(FIXTURES / "nifty_atm_ce_ticks_2026-09-10.parquet")


def broker_sample(name: str) -> Any:
    return json.loads((FIXTURES / "broker" / f"{name}.json").read_text())
