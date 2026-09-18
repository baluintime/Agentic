"""Risk, square-off, health, recorder, persistence and export."""

from __future__ import annotations

from datetime import datetime

import pytest

from core import clock
from core.bus import EventBus
from core.clock import SimClock, set_clock
from core.contracts import Candle, ExecMode, OrderStatus, Segment, Tick, Timeframe
from core.pipeline import PipelineSpec
from core.store import Store
from system_agents.export import ExportAgent
from system_agents.health import HealthAgent, HealthConfig
from system_agents.persistence import PersistenceAgent
from system_agents.recorder import RecorderAgent
from system_agents.risk import RiskAgent, RiskConfig
from system_agents.squareoff import SquareOffAgent, SquareOffConfig
from tests.helpers import Collector, FakeRest, make_request

START = datetime(2026, 9, 11, 10, 0)


@pytest.fixture
def sim_clock():
    previous = set_clock(SimClock(START))
    yield clock.get_clock()
    set_clock(previous)


# -- risk --------------------------------------------------------------------
def risk(bus: EventBus, store=None, **overrides) -> RiskAgent:
    config = RiskConfig(max_orders_per_second=1000, duplicate_window_seconds=0, **overrides)
    return RiskAgent("risk", config, bus, store)


@pytest.mark.asyncio
async def test_risk_approves_a_normal_order(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    approved = Collector(bus, "order.approved")
    agent = risk(bus)
    await agent.start()
    await bus.publish_and_drain("order.request", make_request())
    assert len(approved.messages) == 1
    assert agent.approved == 1
    await bus.stop()


@pytest.mark.asyncio
async def test_risk_rejects_outside_trading_hours() -> None:
    previous = set_clock(SimClock(datetime(2026, 9, 11, 16, 0)))
    try:
        bus = EventBus()
        await bus.start()
        events = Collector(bus, "order.event.*")
        agent = risk(bus)
        await agent.start()
        await bus.publish_and_drain("order.request", make_request())
        assert "outside trading hours" in events.last.message
        await bus.stop()
    finally:
        set_clock(previous)


@pytest.mark.asyncio
async def test_risk_rejects_too_many_lots(sim_clock) -> None:
    bus = EventBus()
    agent = risk(bus, max_lots_per_order=2)
    reason = agent.check(make_request(quantity=75 * 5))
    assert reason is not None and "lots" in reason


@pytest.mark.asyncio
async def test_risk_rejects_too_much_stock_capital(sim_clock) -> None:
    agent = risk(EventBus(), max_capital_per_stock_order=50_000)
    request = make_request(
        quantity=100,
        meta={"segment": Segment.STOCK.value, "purpose": "entry", "reference_price": 1000},
    )
    assert "order value above" in (agent.check(request) or "")


@pytest.mark.asyncio
async def test_risk_caps_trades_per_day(sim_clock) -> None:
    agent = risk(EventBus(), max_trades_per_day_per_pipeline=1)
    agent._record(make_request())
    assert "max trades/day" in (agent.check(make_request()) or "")


@pytest.mark.asyncio
async def test_risk_caps_open_positions(sim_clock) -> None:
    agent = risk(EventBus(), max_open_positions=1)
    agent._record(make_request())
    assert "max open positions" in (agent.check(make_request(pipeline_id="p2")) or "")


@pytest.mark.asyncio
async def test_risk_suppresses_duplicates(sim_clock) -> None:
    agent = RiskAgent("risk", RiskConfig(duplicate_window_seconds=5), EventBus())
    assert agent.check(make_request()) is None
    assert agent.check(make_request()) == "duplicate order suppressed"


@pytest.mark.asyncio
async def test_risk_rate_limit(sim_clock) -> None:
    agent = RiskAgent(
        "risk", RiskConfig(max_orders_per_second=2, duplicate_window_seconds=0), EventBus()
    )
    assert agent.check(make_request()) is None
    assert agent.check(make_request()) is None
    assert "order rate above" in (agent.check(make_request()) or "")


@pytest.mark.asyncio
async def test_kill_switch_blocks_everything(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    system = Collector(bus, "system.*")
    agent = risk(bus)
    await agent.start()
    await agent.kill("panic")
    await bus.drain()
    assert agent.check(make_request()) == "kill switch is active"
    assert any(e.kind == "kill" for e in system.messages)
    await bus.stop()


@pytest.mark.asyncio
async def test_daily_loss_limit_blocks_new_entries(tmp_path, sim_clock) -> None:
    bus = EventBus()
    store = Store(tmp_path)
    store.record_trade(
        {
            "trade_id": "t1",
            "pipeline_id": "p1",
            "strategy": "s",
            "session_date": clock.now().date().isoformat(),
            "net_pnl": -6000,
        }
    )
    agent = risk(bus, store=store, max_daily_loss_total=5000)
    assert "daily loss limit" in (agent.check(make_request()) or "")


@pytest.mark.asyncio
async def test_daily_loss_breach_triggers_squareoff(tmp_path, sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    system = Collector(bus, "system.*")
    store = Store(tmp_path)
    store.record_trade(
        {
            "trade_id": "t1",
            "pipeline_id": "p1",
            "strategy": "s",
            "session_date": clock.now().date().isoformat(),
            "net_pnl": -6000,
        }
    )
    agent = risk(bus, store=store, max_daily_loss_total=5000)
    await agent.start()
    await agent.enforce_daily_loss()
    await bus.drain()
    assert any(e.kind == "squareoff.start" for e in system.messages)
    assert agent.blocked
    await bus.stop()


@pytest.mark.asyncio
async def test_exit_orders_pass_even_when_entries_are_blocked(sim_clock) -> None:
    agent = risk(EventBus())
    await agent.block("loss limit")
    exit_request = make_request(meta={"purpose": "exit", "segment": "option"})
    assert agent.check(exit_request) is None


# -- square-off --------------------------------------------------------------
@pytest.mark.asyncio
async def test_squareoff_fires_both_times_once(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    system = Collector(bus, "system.*")
    agent = SquareOffAgent("squareoff", SquareOffConfig(), bus, rest=FakeRest())
    await agent.tick(datetime(2026, 9, 11, 14, 59, tzinfo=clock.IST))
    assert system.messages == []
    await agent.tick(datetime(2026, 9, 11, 15, 0, tzinfo=clock.IST))
    await agent.tick(datetime(2026, 9, 11, 15, 1, tzinfo=clock.IST))
    await bus.drain()
    kinds = [e.kind for e in system.messages]
    assert kinds.count("squareoff.no_new_entries") == 1
    await agent.tick(datetime(2026, 9, 11, 15, 15, tzinfo=clock.IST))
    await bus.drain()
    kinds = [e.kind for e in system.messages]
    assert kinds.index("squareoff.cancel_gtt") < kinds.index("squareoff.start")
    assert "squareoff.done" in kinds
    assert agent.flat_verified is True
    await bus.stop()


@pytest.mark.asyncio
async def test_squareoff_reports_positions_still_open(sim_clock) -> None:
    class Stuck(FakeRest):
        async def positions(self):
            return [{"quantity": 75, "product": "I"}]

    agent = SquareOffAgent(
        "squareoff",
        SquareOffConfig(verify_attempts=2, verify_gap_seconds=0),
        EventBus(),
        rest=Stuck(),
    )
    assert await agent.verify_flat() is False


@pytest.mark.asyncio
async def test_squareoff_ignores_delivery_positions(sim_clock) -> None:
    class Delivery(FakeRest):
        async def positions(self):
            return [{"quantity": 75, "product": "D"}]

    agent = SquareOffAgent("squareoff", SquareOffConfig(), EventBus(), rest=Delivery())
    assert await agent.verify_flat() is True


# -- health ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_health_detects_stale_ticks_and_blocks_entries(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    system = Collector(bus, "system.*")
    agent = HealthAgent("health", HealthConfig(stale_tick_seconds=10), bus)
    await agent.start()
    await bus.publish_and_drain("tick.X", Tick("X", clock.now(), 100.0))
    sim_clock.advance(30)
    await agent.check()
    await bus.drain()
    assert agent.stale
    assert any(e.kind == "risk.block" for e in system.messages)
    await bus.stop()


@pytest.mark.asyncio
async def test_health_clears_when_ticks_resume(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = HealthAgent("health", HealthConfig(stale_tick_seconds=10), bus)
    await agent.start()
    await bus.publish_and_drain("tick.X", Tick("X", clock.now(), 100.0))
    sim_clock.advance(30)
    await agent.check()
    sim_clock.advance(1)
    await bus.publish_and_drain("tick.X", Tick("X", clock.now(), 101.0))
    assert not agent.stale
    await bus.stop()


@pytest.mark.asyncio
async def test_health_reports_token_expiry(sim_clock) -> None:
    class ExpiredAuth:
        def expiry_warning(self, now=None):
            return "token expired — daily login required"

    agent = HealthAgent("health", HealthConfig(), EventBus(), auth=ExpiredAuth())
    report = await agent.check()
    assert "expired" in report["token"]


# -- recorder ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_recorder_writes_ticks_and_closed_candles(tmp_path, sim_clock) -> None:
    import pandas as pd

    bus = EventBus()
    await bus.start()
    store = Store(tmp_path)
    agent = RecorderAgent("recorder", None, bus, store, flush_every=1000)
    await agent.start()
    await bus.publish_and_drain("tick.X", Tick("X", clock.now(), 100.0, cum_volume=10))
    open_candle = Candle("X", Timeframe.M1, clock.now(), 1, 2, 0, 1, 5, False)
    closed = Candle("X", Timeframe.M1, clock.now(), 1, 2, 0, 1, 5, True)
    await bus.publish_and_drain("candle.1m.X", open_candle)
    await bus.publish_and_drain("candle.1m.X", closed)
    agent.flush()
    assert agent.ticks_written == 1
    assert agent.candles_written == 1  # the open candle is not recorded
    ticks = pd.read_parquet(store.tick_path("X", clock.now().date()))
    assert len(ticks) == 1 and ticks["ltp"].iloc[0] == 100.0
    await bus.stop()


# -- persistence -------------------------------------------------------------
def test_workspace_round_trip_reloads_paused(tmp_path, sim_clock) -> None:
    store = Store(tmp_path)
    agent = PersistenceAgent("persistence", None, None, store)
    spec = PipelineSpec(
        underlying_key="NSE_INDEX|Nifty 50",
        underlying_symbol="NIFTY",
        indicator="macd",
        strategy="macd_s1_cross_option",
        exec_mode=ExecMode.LIVE,
    )
    agent.save([spec], "desk")
    assert "desk" in agent.list_workspaces()
    loaded = agent.load("desk")
    assert len(loaded) == 1
    assert loaded[0].underlying_key == spec.underlying_key
    assert loaded[0].paused is True  # never resumes trading by itself
    assert loaded[0].strategy == "macd_s1_cross_option"


def test_loading_a_missing_workspace_is_empty(tmp_path) -> None:
    agent = PersistenceAgent("persistence", None, None, Store(tmp_path))
    assert agent.load("nope") == []


@pytest.mark.asyncio
async def test_reconciliation_reports_broker_state(tmp_path, sim_clock) -> None:
    class WithPositions(FakeRest):
        async def positions(self):
            return [{"quantity": 75, "product": "I", "tag": "corr0001"}]

        async def gtt_orders(self):
            return [{"tag": "corr0001"}]

    store = Store(tmp_path)
    agent = PersistenceAgent("persistence", None, None, store, rest=WithPositions())
    report = await agent.reconcile()
    assert report["checked"] and report["open_positions"] == 1 and report["gtts"] == 1
    assert agent.passed


@pytest.mark.asyncio
async def test_reconciliation_without_a_broker_does_not_pass(tmp_path) -> None:
    agent = PersistenceAgent("persistence", None, None, Store(tmp_path))
    report = await agent.reconcile()
    assert not report["checked"] and not agent.passed


# -- export ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_export_all_bundles_workbook_and_ticks(tmp_path, sim_clock) -> None:
    import zipfile

    import pandas as pd

    store = Store(tmp_path)
    (store.base / "ticks" / "X_2026-09-11.parquet").write_bytes(b"")
    pd.DataFrame({"ltp": [1.0]}).to_parquet(store.base / "ticks" / "X_2026-09-11.parquet")

    class Dummy(RecorderAgent):
        def export_frames(self):
            return {"rows": pd.DataFrame({"a": [1, 2], "ts": pd.to_datetime(["2026-09-11"] * 2)})}

    agent = Dummy("dummy", None, None, store)
    export = ExportAgent("export", None, None, store, agents=lambda: [agent])
    archive = export.export_all()
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert any(n.endswith(".xlsx") for n in names)
    assert any(n.startswith("ticks/") for n in names)


def test_export_agent_skips_empty_frames(tmp_path) -> None:
    store = Store(tmp_path)
    agent = RecorderAgent("recorder", None, None, store)
    export = ExportAgent("export", None, None, store, agents=lambda: [agent])
    assert export.export_agent(agent) is None


@pytest.mark.asyncio
async def test_squareoff_skips_the_positions_check_when_not_logged_in(sim_clock) -> None:
    """Starting the console after the cut-off must not 401 against the broker."""

    class Refusing(FakeRest):
        async def positions(self):
            raise AssertionError("must not call the broker without a valid token")

    class LoggedOut:
        state = type("State", (), {"valid": False})()

    agent = SquareOffAgent(
        "squareoff", SquareOffConfig(), EventBus(), rest=Refusing(), auth=LoggedOut()
    )
    assert await agent.verify_flat() is None
    assert not agent.errors  # a logged-out check is expected, not an error
    assert "not logged in" in agent.last_message


@pytest.mark.asyncio
async def test_squareoff_checks_positions_once_logged_in(sim_clock) -> None:
    class LoggedIn:
        state = type("State", (), {"valid": True})()

    agent = SquareOffAgent(
        "squareoff", SquareOffConfig(), EventBus(), rest=FakeRest(), auth=LoggedIn()
    )
    assert await agent.verify_flat() is True


@pytest.mark.asyncio
async def test_squareoff_without_an_auth_manager_still_checks(sim_clock) -> None:
    agent = SquareOffAgent("squareoff", SquareOffConfig(), EventBus(), rest=FakeRest())
    assert await agent.verify_flat() is True


@pytest.mark.asyncio
async def test_squareoff_verifies_later_once_the_token_arrives(sim_clock) -> None:
    """Square-off at 15:15 while logged out, login at 15:40: verify then."""

    class Auth:
        state = type("State", (), {"valid": False})()

    bus = EventBus()
    await bus.start()
    system = Collector(bus, "system.*")
    auth = Auth()
    agent = SquareOffAgent("squareoff", SquareOffConfig(), bus, rest=FakeRest(), auth=auth)

    await agent.tick(datetime(2026, 9, 11, 15, 16, tzinfo=clock.IST))
    await bus.drain()
    assert agent.squareoff_done is not None and agent.flat_verified is None

    auth.state.valid = True
    await agent.tick(datetime(2026, 9, 11, 15, 40, tzinfo=clock.IST))
    await bus.drain()
    assert agent.flat_verified is True
    assert [e.message for e in system.messages if e.kind == "squareoff.done"][-1] == (
        "deferred verification"
    )
    await bus.stop()


@pytest.mark.asyncio
async def test_squareoff_does_not_re_run_after_a_successful_verification(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    system = Collector(bus, "system.*")
    agent = SquareOffAgent("squareoff", SquareOffConfig(), bus, rest=FakeRest())
    await agent.tick(datetime(2026, 9, 11, 15, 16, tzinfo=clock.IST))
    await agent.tick(datetime(2026, 9, 11, 15, 40, tzinfo=clock.IST))
    await bus.drain()
    assert len([e for e in system.messages if e.kind == "squareoff.start"]) == 1
    assert len([e for e in system.messages if e.kind == "squareoff.done"]) == 1
    await bus.stop()


# -- open-position accounting ------------------------------------------------
async def approve(bus: EventBus, agent: RiskAgent, **overrides):
    """Push one request through the gate and return it."""
    request = make_request(**overrides)
    await bus.publish_and_drain("order.request", request)
    return request


async def report(bus: EventBus, correlation_id: str, status: OrderStatus, origin="strategy-1"):
    from core.contracts import OrderEvent

    await bus.publish_and_drain(
        f"order.event.{origin}",
        OrderEvent(correlation_id, origin, status.value, 100.0, 75, "B1", clock.now()),
    )


def entry_meta(**extra) -> dict:
    return {"purpose": "entry", "segment": "option", "lot_size": 75, **extra}


def exit_meta(**extra) -> dict:
    return {"purpose": "exit", "segment": "option", "lot_size": 75, **extra}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "closing",
    [OrderStatus.TARGET_HIT, OrderStatus.SL_HIT, OrderStatus.SQUARED_OFF],
)
async def test_a_broker_leg_closing_a_position_frees_the_slot(sim_clock, closing) -> None:
    """Target/SL legs report on the *entry* correlation and send no exit request."""
    bus = EventBus()
    await bus.start()
    agent = risk(bus)
    await agent.start()

    request = await approve(bus, agent, meta=entry_meta())
    await report(bus, request.correlation_id, OrderStatus.FILLED)
    assert agent.status()["open_positions"] == 1

    await report(bus, request.correlation_id, closing)
    assert agent.status()["open_positions"] == 0
    await bus.stop()


@pytest.mark.asyncio
async def test_repeated_target_exits_do_not_exhaust_the_limit(sim_clock) -> None:
    """The reported bug: five target-closed trades blocked every later entry."""
    bus = EventBus()
    await bus.start()
    agent = risk(bus, max_open_positions=5)
    await agent.start()
    for round_trip in range(8):
        request = await approve(bus, agent, correlation_id=f"c{round_trip}", meta=entry_meta())
        await report(bus, request.correlation_id, OrderStatus.FILLED)
        await report(bus, request.correlation_id, OrderStatus.TARGET_HIT)
    assert agent.status()["open_positions"] == 0
    assert agent.rejected == 0
    assert agent.approved == 8
    await bus.stop()


@pytest.mark.asyncio
async def test_an_exit_fill_frees_the_slot(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = risk(bus)
    await agent.start()
    entry = await approve(bus, agent, correlation_id="e1", meta=entry_meta())
    await report(bus, entry.correlation_id, OrderStatus.FILLED)
    exit_request = await approve(bus, agent, correlation_id="x1", meta=exit_meta())
    assert agent.status()["open_positions"] == 1  # still open until the exit fills
    await report(bus, exit_request.correlation_id, OrderStatus.FILLED)
    assert agent.status()["open_positions"] == 0
    await bus.stop()


@pytest.mark.asyncio
async def test_a_rejected_entry_never_counts(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = risk(bus)
    await agent.start()
    entry = await approve(bus, agent, meta=entry_meta())
    await report(bus, entry.correlation_id, OrderStatus.REJECTED)
    assert agent.status()["open_positions"] == 0
    await bus.stop()


@pytest.mark.asyncio
async def test_a_rejected_exit_leaves_the_position_open(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = risk(bus)
    await agent.start()
    entry = await approve(bus, agent, correlation_id="e1", meta=entry_meta())
    await report(bus, entry.correlation_id, OrderStatus.FILLED)
    exit_request = await approve(bus, agent, correlation_id="x1", meta=exit_meta())
    await report(bus, exit_request.correlation_id, OrderStatus.REJECTED)
    assert agent.status()["open_positions"] == 1  # the exit failed; we are still in
    await bus.stop()


@pytest.mark.asyncio
async def test_the_limit_still_blocks_genuinely_open_positions(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = risk(bus, max_open_positions=2)
    await agent.start()
    for index in range(2):
        request = await approve(
            bus, agent, correlation_id=f"c{index}", pipeline_id=f"p{index}", meta=entry_meta()
        )
        await report(bus, request.correlation_id, OrderStatus.FILLED)
    reason = agent.check(make_request(correlation_id="c9", pipeline_id="p9", meta=entry_meta()))
    assert reason is not None and "max_open_positions=2" in reason
    assert "config/risk.yaml" in reason  # the message names where to change it
    await bus.stop()


@pytest.mark.asyncio
async def test_lot_limit_names_the_setting(sim_clock) -> None:
    agent = risk(EventBus(), max_lots_per_order=5)
    reason = agent.check(make_request(quantity=75 * 6, meta=entry_meta()))
    assert reason is not None
    assert "max_lots_per_order=5" in reason and "config/risk.yaml" in reason


@pytest.mark.asyncio
async def test_sync_corrects_drift_against_the_strategies(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = risk(bus)
    await agent.start()
    for index in range(3):
        request = await approve(
            bus, agent, correlation_id=f"c{index}", pipeline_id=f"p{index}", meta=entry_meta()
        )
        await report(bus, request.correlation_id, OrderStatus.FILLED)
    assert agent.status()["open_positions"] == 3
    assert agent.sync_open_positions({"p1"}) == 1  # only p1 is really holding
    assert agent.status()["open_positions"] == 1
    await bus.stop()
