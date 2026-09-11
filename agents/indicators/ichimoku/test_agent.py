from __future__ import annotations

import pandas as pd
import pytest

from agents.indicators.ichimoku.agent import Config, IchimokuAgent
from tests.helpers import ramp_frame, wave_frame


def build(**overrides) -> IchimokuAgent:
    return IchimokuAgent("ichimoku-test", Config(**overrides))


def test_hand_checked_midpoints() -> None:
    agent = build(tenkan=3, kijun=4, senkou_b=6, displacement=2)
    frame = ramp_frame(20)  # close 100..119, high = close + 1, low = close - 1
    out = agent.compute(frame.copy())
    # last 3 bars: highs 118,119,120 lows 116,117,118 -> (120 + 116) / 2
    assert out["tenkan"].iloc[-1] == pytest.approx(118.0)
    # last 4 bars: high 120, low 115
    assert out["kijun"].iloc[-1] == pytest.approx(117.5)
    assert out["span_a_projected"].iloc[-1] == pytest.approx((118.0 + 117.5) / 2)
    # last 6 bars: high 120, low 113
    assert out["span_b_projected"].iloc[-1] == pytest.approx(116.5)


def test_current_cloud_is_the_projected_cloud_shifted_back() -> None:
    agent = build()
    out = agent.compute(ramp_frame(200))
    assert out["span_a_current"].iloc[-1] == pytest.approx(out["span_a_projected"].iloc[-27])
    assert out["span_b_current"].iloc[-1] == pytest.approx(out["span_b_projected"].iloc[-27])


def test_warmup_rows_are_nan() -> None:
    agent = build()
    out = agent.compute(ramp_frame(100))
    warmup = agent.warmup_bars()
    assert out["span_b_current"].iloc[: warmup - 1].isna().all()
    assert not pd.isna(out["span_b_current"].iloc[warmup - 1])


def test_no_look_ahead() -> None:
    agent = build()
    frame = wave_frame(200)
    cut = 150
    partial = agent.compute(frame.iloc[:cut].copy())
    full = agent.compute(frame.copy())
    for column in agent.outputs:
        if column == "chikou_reference":
            continue
        assert partial[column].iloc[-1] == pytest.approx(full[column].iloc[cut - 1], nan_ok=True)


def test_chikou_does_not_look_ahead() -> None:
    agent = build()
    out = agent.compute(ramp_frame(100))
    assert out["chikou"].iloc[-1] == out["close"].iloc[-1]
    assert out["chikou_reference"].iloc[-1] == out["close"].iloc[-27]
