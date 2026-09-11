"""Engine wiring: shared agents, compatibility checks and live arming."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.engine import Engine
from broker.auth import AuthManager
from broker.instruments import InstrumentMaster
from core import clock
from core.bus import EventBus
from core.clock import SimClock, set_clock
from core.contracts import ExecMode, Segment, Timeframe
from core.pipeline import PipelineSpec
from core.store import Store
from tests.helpers import NIFTY_KEY, FakeRest, nifty_records


@pytest.fixture
def sim_clock():
    previous = set_clock(SimClock(datetime(2026, 9, 11, 10, 0)))
    yield clock.get_clock()
    set_clock(previous)


async def build(tmp_path) -> Engine:
    engine = Engine(
        bus=EventBus(),
        store=Store(tmp_path / "runtime"),
        instruments=InstrumentMaster.from_records(nifty_records()),
        auth=AuthManager(token_path=tmp_path / "token.json", env={}),
        rest=FakeRest(),  # every test is offline: no broker, no network
    )
    await engine.start()
    return engine


def spec(**overrides) -> PipelineSpec:
    values = dict(
        underlying_key=NIFTY_KEY,
        underlying_symbol="NIFTY",
        indicator="macd",
        strategy="macd_s1_cross_option",
        segment=Segment.OPTION,
        timeframe=Timeframe.M5,
        exec_mode=ExecMode.PAPER,
    )
    values.update(overrides)
    return PipelineSpec(**values)


@pytest.mark.asyncio
async def test_five_minute_pipeline_builds_the_one_minute_chain(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    await engine.add_pipeline(spec())
    assert set(engine.candles) == {(NIFTY_KEY, "5m"), (NIFTY_KEY, "1m")}
    await engine.stop()


@pytest.mark.asyncio
async def test_candle_agents_are_shared_between_pipelines(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    first = await engine.add_pipeline(spec())
    await engine.add_pipeline(spec(strategy="macd_s2_momentum_option"))
    assert len(engine.candles) == 2  # one 1m and one 5m for both pipelines
    assert engine.candle_refs[(NIFTY_KEY, "5m")] == 2
    await engine.remove_pipeline(first.spec.pipeline_id)
    assert engine.candle_refs[(NIFTY_KEY, "5m")] == 1
    assert (NIFTY_KEY, "5m") in engine.candles  # still used by the second pipeline
    await engine.stop()


@pytest.mark.asyncio
async def test_removing_the_last_pipeline_stops_its_candle_agents(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    pipeline = await engine.add_pipeline(spec())
    await engine.remove_pipeline(pipeline.spec.pipeline_id)
    assert engine.candles == {}
    assert engine.hub.subscriptions == []
    await engine.stop()


@pytest.mark.asyncio
async def test_incompatible_strategy_is_refused(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    with pytest.raises(ValueError, match="segment"):
        await engine.add_pipeline(spec(strategy="macd_s3_momentum_stock"))
    with pytest.raises(ValueError, match="indicator"):
        await engine.add_pipeline(spec(strategy="ichimoku_s1_cloud_option"))
    await engine.stop()


@pytest.mark.asyncio
async def test_stock_pipeline_is_accepted(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    pipeline = await engine.add_pipeline(
        spec(
            strategy="macd_s3_momentum_stock",
            segment=Segment.STOCK,
            underlying_key="NSE_EQ|INE002A01018",
            underlying_symbol="RELIANCE",
        )
    )
    assert pipeline.strategy.name == "macd_s3_momentum_stock"
    assert engine.hub.subscriptions == ["NSE_EQ|INE002A01018"]  # no option window
    await engine.stop()


@pytest.mark.asyncio
async def test_live_arming_requires_confirmation_and_a_token(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    assert await engine.arm_live(False) == (False, "confirmation required")
    armed, reason = await engine.arm_live(True)
    assert not armed and "token" in reason
    engine.auth.state.valid = True
    armed, reason = await engine.arm_live(True)
    assert not armed and "reconciliation" in reason
    engine.persistence.reconciliation = {"checked": True, "errors": []}
    armed, reason = await engine.arm_live(True)
    assert armed and engine.armed_live
    await engine.stop()


@pytest.mark.asyncio
async def test_live_arming_honours_the_paper_first_rule(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    engine.analytics.config.min_paper_sessions = 3
    engine.auth.state.valid = True
    engine.persistence.reconciliation = {"checked": True, "errors": []}
    await engine.add_pipeline(spec(exec_mode=ExecMode.LIVE, order_agent="gtt"))
    armed, reason = await engine.arm_live(True)
    assert not armed and "paper sessions" in reason
    await engine.stop()


@pytest.mark.asyncio
async def test_order_agents_are_shared_and_follow_the_armed_flag(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    await engine.add_pipeline(spec())
    await engine.add_pipeline(spec(strategy="macd_s2_momentum_option"))
    assert list(engine.order_agents) == ["paper"]
    assert engine.order_agents["paper"].armed_live is False
    await engine.stop()


@pytest.mark.asyncio
async def test_totals_and_status(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    await engine.add_pipeline(spec())
    status = engine.status()
    assert status["pipelines"] == 1 and status["mode"] == "paper"
    assert status["totals"] == {"gross": 0.0, "charges": 0.0, "net": 0.0, "open_mtm": 0.0}
    assert "risk" in status["agents"]
    await engine.stop()


@pytest.mark.asyncio
async def test_kill_disarms_and_pauses_every_strategy(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    pipeline = await engine.add_pipeline(spec())
    engine.armed_live = True
    await engine.kill("test")
    await engine.bus.drain()
    assert not engine.armed_live and engine.risk.killed and pipeline.strategy.paused
    await engine.stop()


@pytest.mark.asyncio
async def test_a_pipeline_added_after_the_cutoff_cannot_enter(tmp_path, sim_clock) -> None:
    """Starting the console at 15:34 must not leave a new pipeline free to enter."""
    engine = await build(tmp_path)
    await engine.squareoff.tick(datetime(2026, 9, 11, 15, 20, tzinfo=clock.IST))
    pipeline = await engine.add_pipeline(spec())
    assert pipeline.strategy.new_entries_blocked
    await engine.stop()


@pytest.mark.asyncio
async def test_a_pipeline_added_while_killed_starts_halted(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    await engine.kill("test")
    await engine.bus.drain()
    pipeline = await engine.add_pipeline(spec())
    assert pipeline.strategy.halted and pipeline.strategy.new_entries_blocked
    await engine.stop()


@pytest.mark.asyncio
async def test_a_pipeline_added_while_risk_is_blocked_cannot_enter(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    await engine.risk.block("daily loss limit hit")
    pipeline = await engine.add_pipeline(spec())
    assert pipeline.strategy.new_entries_blocked
    await engine.stop()


@pytest.mark.asyncio
async def test_a_pipeline_added_in_a_normal_session_is_free_to_trade(tmp_path, sim_clock) -> None:
    engine = await build(tmp_path)
    pipeline = await engine.add_pipeline(spec())
    assert not pipeline.strategy.new_entries_blocked and not pipeline.strategy.halted
    await engine.stop()
