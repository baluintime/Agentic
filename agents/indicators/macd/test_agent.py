from __future__ import annotations

import pandas as pd
import pytest

from agents.indicators.macd.agent import Config, MacdAgent
from tests.helpers import ramp_frame


def build(**overrides) -> MacdAgent:
    return MacdAgent("macd-test", Config(**overrides))


def test_matches_hand_computed_emas() -> None:
    agent = build(fast=3, slow=6, signal=3)
    frame = ramp_frame(60)
    out = agent.compute(frame.copy())
    fast = frame["close"].ewm(span=3, adjust=False).mean()
    slow = frame["close"].ewm(span=6, adjust=False).mean()
    expected = fast - slow
    assert out["macd"].iloc[-1] == pytest.approx(expected.iloc[-1])
    assert out["signal"].iloc[-1] == pytest.approx(
        expected.ewm(span=3, adjust=False).mean().iloc[-1]
    )
    assert out["histogram"].iloc[-1] == pytest.approx(out["macd"].iloc[-1] - out["signal"].iloc[-1])


def test_rising_series_gives_positive_macd() -> None:
    out = build().compute(ramp_frame(200))
    assert out["macd"].iloc[-1] > 0


def test_falling_series_gives_negative_macd() -> None:
    frame = ramp_frame(200)
    frame["close"] = frame["close"].iloc[::-1].to_numpy()
    out = build().compute(frame)
    assert out["macd"].iloc[-1] < 0


def test_warmup_bars_follows_config() -> None:
    assert build().warmup_bars() == 3 * 26 + 9
    assert build(slow=10, signal=4).warmup_bars() == 34


def test_no_look_ahead() -> None:
    agent = build()
    frame = ramp_frame(200)
    cut = 150
    partial = agent.compute(frame.iloc[:cut].copy())["macd"].iloc[-1]
    full = agent.compute(frame.copy())["macd"].iloc[cut - 1]
    assert partial == pytest.approx(full)


def test_outputs_are_added_not_replaced() -> None:
    frame = ramp_frame(50)
    out = build().compute(frame.copy())
    assert set(["open", "high", "low", "close", "volume"]).issubset(out.columns)
    assert isinstance(out, pd.DataFrame)
