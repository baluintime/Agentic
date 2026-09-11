"""Required tests: known values, warm-up NaNs, no look-ahead."""

from __future__ import annotations

import pandas as pd

from agents.indicators._template.agent import Config, TemplateIndicator


def build(**overrides) -> TemplateIndicator:
    return TemplateIndicator("t", Config(**overrides))


def test_computes_and_warms_up() -> None:
    agent = build(period=3)
    df = pd.DataFrame(
        {
            "open": range(10),
            "high": range(10),
            "low": range(10),
            "close": [float(i) for i in range(10)],
            "volume": [1] * 10,
        }
    )
    out = agent.compute(df.copy())
    assert out["value"].isna().sum() == 2
    assert out["value"].iloc[-1] == 8.0


def test_no_look_ahead() -> None:
    agent = build(period=3)
    df = pd.DataFrame(
        {
            "open": range(10),
            "high": range(10),
            "low": range(10),
            "close": [float(i) for i in range(10)],
            "volume": [1] * 10,
        }
    )
    partial = agent.compute(df.iloc[:8].copy())["value"].iloc[-1]
    full = agent.compute(df.copy())["value"].iloc[7]
    assert partial == full
