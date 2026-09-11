"""End-to-end: ticks -> candles -> indicator -> strategy -> risk -> paper fill -> trade log.

This is the Phase 3 exit criterion in miniature: one pipeline, one round trip,
a correct trade row and net P&L — entirely offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from app.engine import Engine
from broker.auth import AuthManager
from broker.instruments import InstrumentMaster
from core import clock
from core.bus import EventBus
from core.clock import SimClock, set_clock
from core.contracts import ExecMode, Segment, Tick, Timeframe, tick_topic
from core.pipeline import PipelineSpec
from core.position import PositionState
from core.store import Store
from tests.helpers import NIFTY_KEY, nifty_records

START = datetime(2026, 9, 11, 9, 15)


@pytest.fixture
def sim_clock():
    previous = set_clock(SimClock(START))
    yield clock.get_clock()
    set_clock(previous)


async def build_engine(tmp_path) -> Engine:
    store = Store(tmp_path / "runtime")
    engine = Engine(
        bus=EventBus(),
        store=store,
        instruments=InstrumentMaster.from_records(nifty_records()),
        auth=AuthManager(token_path=tmp_path / "token.json", env={}),
    )
    await engine.start()
    return engine


def declining_history(bars: int = 160, start_price: float = 25_100.0) -> pd.DataFrame:
    """Enough 5-minute bars for MACD to warm up, with MACD safely below signal."""
    closes = [start_price - 4 * i for i in range(bars)]
    index = [clock.ist(START) - timedelta(minutes=5 * (bars - i)) for i in range(bars)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 2 for c in closes],
            "low": [c - 2 for c in closes],
            "close": closes,
            "volume": [1000] * bars,
        },
        index=pd.DatetimeIndex(index, name="start"),
    )


async def feed(engine: Engine, key: str, price: float, moment: datetime, volume: int = 0) -> None:
    await engine.bus.publish_and_drain(tick_topic(key), Tick(key, moment, price, cum_volume=volume))


@pytest.mark.asyncio
async def test_paper_round_trip_produces_a_trade(tmp_path, sim_clock) -> None:
    engine = await build_engine(tmp_path)
    spec = PipelineSpec(
        underlying_key=NIFTY_KEY,
        underlying_symbol="NIFTY",
        indicator="macd",
        strategy="macd_s1_cross_option",
        segment=Segment.OPTION,
        timeframe=Timeframe.M5,
        order_agent="gtt",  # paper mode routes to the Paper agent regardless
        exec_mode=ExecMode.PAPER,
        strategy_params={"target_points": 20, "stoploss_points": 10, "lots": 1},
    )
    pipeline = await engine.add_pipeline(spec)
    assert "paper" in engine.order_agents  # paper mode never builds a live agent

    pipeline.indicator.seed(declining_history())
    strategy = pipeline.strategy

    # A rising underlying drives MACD up through its signal line.
    moment = clock.ist(START)
    price = 24_460.0
    option_key = "NSE_FO|24500CE"
    for bar in range(14):
        price += 55
        for step in range(5):
            moment = clock.ist(START) + timedelta(minutes=5 * bar + step)
            sim_clock.set(moment)
            await feed(engine, NIFTY_KEY, price + step, moment)
            await feed(engine, option_key, 100.0, moment)
        if strategy.position.state is PositionState.OPEN:
            break

    assert strategy.position.state is PositionState.OPEN, "MACD cross should have opened a position"
    assert strategy.position.instrument is not None
    assert strategy.position.instrument.option_type.value == "CE"
    assert strategy.position.quantity == 75  # one lot
    assert strategy.position.target_price == pytest.approx(120.05, abs=0.5)

    # Premium runs to the target: the Paper agent's simulated leg fires.
    traded = strategy.position.instrument.instrument_key
    moment += timedelta(minutes=1)
    sim_clock.set(moment)
    await feed(engine, traded, 125.0, moment)

    assert len(strategy.trades) == 1
    trade = strategy.trades.rows[0]
    assert trade.exit_reason == "target"
    assert trade.direction == "long"
    assert trade.quantity == 75
    assert trade.gross_pnl == pytest.approx((trade.exit_price - trade.entry_price) * 75)
    assert trade.net_pnl == pytest.approx(trade.gross_pnl - trade.charges)
    assert trade.mode == "paper"
    assert trade.indicator_snapshot["macd"] > trade.indicator_snapshot["signal"]
    assert strategy.position.is_flat
    assert engine.totals()["net"] == trade.net_pnl
    await engine.stop()


@pytest.mark.asyncio
async def test_blocked_risk_stops_entries_at_the_strategy(tmp_path, sim_clock) -> None:
    """A risk block reaches the strategy, so no order is even attempted."""
    engine = await build_engine(tmp_path)
    spec = PipelineSpec(
        underlying_key=NIFTY_KEY,
        underlying_symbol="NIFTY",
        indicator="macd",
        strategy="macd_s1_cross_option",
        segment=Segment.OPTION,
        timeframe=Timeframe.M5,
        exec_mode=ExecMode.PAPER,
    )
    pipeline = await engine.add_pipeline(spec)
    pipeline.indicator.seed(declining_history())
    await engine.risk.block("daily loss limit hit")
    await engine.bus.drain()

    moment = clock.ist(START)
    price = 24_460.0
    for bar in range(14):
        price += 55
        for step in range(5):
            moment = clock.ist(START) + timedelta(minutes=5 * bar + step)
            sim_clock.set(moment)
            await feed(engine, NIFTY_KEY, price + step, moment)
            await feed(engine, "NSE_FO|24500CE", 100.0, moment)

    assert pipeline.strategy.new_entries_blocked
    assert pipeline.strategy.position.is_flat
    assert len(pipeline.strategy.trades) == 0
    assert engine.risk.approved == 0
    await engine.stop()


@pytest.mark.asyncio
async def test_kill_switch_squares_off_an_open_position(tmp_path, sim_clock) -> None:
    engine = await build_engine(tmp_path)
    spec = PipelineSpec(
        underlying_key=NIFTY_KEY,
        underlying_symbol="NIFTY",
        indicator="macd",
        strategy="macd_s1_cross_option",
        segment=Segment.OPTION,
        timeframe=Timeframe.M5,
        exec_mode=ExecMode.PAPER,
    )
    pipeline = await engine.add_pipeline(spec)
    pipeline.indicator.seed(declining_history())
    strategy = pipeline.strategy

    moment = clock.ist(START)
    price = 24_460.0
    for bar in range(14):
        price += 55
        for step in range(5):
            moment = clock.ist(START) + timedelta(minutes=5 * bar + step)
            sim_clock.set(moment)
            await feed(engine, NIFTY_KEY, price + step, moment)
            await feed(engine, "NSE_FO|24500CE", 100.0, moment)
        if strategy.position.state is PositionState.OPEN:
            break
    assert strategy.position.state is PositionState.OPEN

    await engine.kill("manual kill switch")
    await engine.bus.drain()

    assert engine.risk.killed
    assert not engine.armed_live
    assert len(strategy.trades) == 1
    assert strategy.trades.rows[0].exit_reason == "squareoff"
    assert strategy.position.is_flat
    await engine.stop()


@pytest.mark.asyncio
async def test_option_window_follows_spot(tmp_path, sim_clock) -> None:
    engine = await build_engine(tmp_path)
    spec = PipelineSpec(
        underlying_key=NIFTY_KEY,
        underlying_symbol="NIFTY",
        indicator="macd",
        strategy="macd_s1_cross_option",
        segment=Segment.OPTION,
        timeframe=Timeframe.M5,
        exec_mode=ExecMode.PAPER,
    )
    await engine.add_pipeline(spec)
    await feed(engine, NIFTY_KEY, 24_873.0, clock.ist(START))
    first = set(engine.hub.subscriptions)
    assert "NSE_FO|24850CE" in first and "NSE_FO|24850PE" in first
    assert len(first) == 1 + 5 * 2  # underlying + ATM +/- 2 strikes, CE and PE

    await feed(engine, NIFTY_KEY, 25_100.0, clock.ist(START) + timedelta(minutes=1))
    second = set(engine.hub.subscriptions)
    assert "NSE_FO|25100CE" in second
    assert "NSE_FO|24750CE" not in second  # far strikes are dropped
    assert len(second) == 11
    await engine.stop()
