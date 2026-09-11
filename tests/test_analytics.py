"""Analytics arithmetic and the alert agent."""

from __future__ import annotations

from datetime import datetime

import pytest

from core import clock
from core.bus import EventBus
from core.clock import SimClock, set_clock
from core.contracts import OrderEvent, OrderStatus, SystemEvent
from core.store import Store
from system_agents.analytics import AnalyticsAgent, AnalyticsConfig, compute
from system_agents.notifications import NotificationAgent, NotificationConfig


@pytest.fixture
def sim_clock():
    previous = set_clock(SimClock(datetime(2026, 9, 11, 10, 0)))
    yield clock.get_clock()
    set_clock(previous)


def trade(
    net: float,
    gross: float | None = None,
    strategy: str = "s1",
    day: str = "2026-09-11",
    mode: str = "paper",
) -> dict:
    return {
        "trade_id": f"t{net}{day}{strategy}",
        "pipeline_id": "p1",
        "strategy": strategy,
        "session_date": day,
        "net_pnl": net,
        "gross_pnl": gross if gross is not None else net + 60,
        "charges": 60,
        "mode": mode,
    }


def test_empty_trade_log() -> None:
    stats = compute([])
    assert stats.trades == 0 and stats.profit_factor == 0 and stats.expectancy == 0


def test_stats_on_a_mixed_log() -> None:
    stats = compute([trade(300), trade(-100), trade(200), trade(-200)])
    assert stats.trades == 4 and stats.wins == 2 and stats.losses == 2
    assert stats.win_rate == 50.0
    assert stats.net == 200
    assert stats.average_win == 250.0
    assert stats.average_loss == 150.0
    assert stats.profit_factor == pytest.approx(500 / 300, abs=0.01)
    assert stats.expectancy == pytest.approx(0.5 * 250 - 0.5 * 150)
    assert stats.best == 300 and stats.worst == -200


def test_max_drawdown_is_peak_to_trough() -> None:
    # equity: 100, 400, 150, 250
    stats = compute([trade(100), trade(300), trade(-250), trade(100)])
    assert stats.max_drawdown == 250.0


def test_profit_factor_without_losses_reports_gross_profit() -> None:
    stats = compute([trade(100), trade(50)])
    assert stats.losses == 0 and stats.profit_factor == 150.0


def test_analytics_groups_by_strategy(tmp_path, sim_clock) -> None:
    store = Store(tmp_path)
    for row in (trade(100, strategy="s1"), trade(-50, strategy="s2"), trade(70, strategy="s1")):
        store.record_trade(row)
    agent = AnalyticsAgent("analytics", AnalyticsConfig(), None, store)
    grouped = agent.by_strategy()
    assert set(grouped) == {"s1", "s2"}
    assert grouped["s1"].trades == 2 and grouped["s1"].net == 170
    assert grouped["s2"].losses == 1


def test_paper_first_promotion_rule(tmp_path, sim_clock) -> None:
    store = Store(tmp_path)
    agent = AnalyticsAgent("analytics", AnalyticsConfig(min_paper_sessions=3), None, store)
    unlocked, message = agent.live_unlocked("s1")
    assert not unlocked and "0 of 3" in message
    for day in ("2026-09-09", "2026-09-10", "2026-09-11"):
        store.record_trade(trade(10, day=day))
    assert agent.paper_sessions("s1") == 3
    unlocked, message = agent.live_unlocked("s1")
    assert unlocked


def test_live_trades_do_not_count_towards_the_paper_rule(tmp_path, sim_clock) -> None:
    store = Store(tmp_path)
    store.record_trade(trade(10, day="2026-09-10", mode="live"))
    agent = AnalyticsAgent("analytics", AnalyticsConfig(min_paper_sessions=1), None, store)
    assert agent.paper_sessions("s1") == 0


# -- notifications -----------------------------------------------------------
async def build_notifier(bus: EventBus, enabled: bool = True) -> NotificationAgent:
    sent: list[str] = []

    async def sender(message: str) -> None:
        sent.append(message)

    agent = NotificationAgent(
        "notify", NotificationConfig(enabled=enabled), bus, sender=sender, env={}
    )
    agent.delivered = sent  # type: ignore[attr-defined]
    await agent.start()
    return agent


@pytest.mark.asyncio
async def test_notifies_on_fills_and_stop_losses(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = await build_notifier(bus)
    await bus.publish_and_drain(
        "order.event.s1",
        OrderEvent("c1", "s1", OrderStatus.SL_HIT.value, 90.0, 75, "B1", clock.now()),
    )
    assert any("SL_HIT" in message for message in agent.delivered)
    await bus.stop()


@pytest.mark.asyncio
async def test_ignores_intermediate_statuses(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = await build_notifier(bus)
    await bus.publish_and_drain(
        "order.event.s1",
        OrderEvent("c1", "s1", OrderStatus.PLACED.value, None, 0, "B1", clock.now()),
    )
    assert agent.sent == []
    await bus.stop()


@pytest.mark.asyncio
async def test_notifies_on_risk_and_kill_events(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = await build_notifier(bus)
    await bus.publish_and_drain("system.kill", SystemEvent("kill", clock.now(), "panic"))
    await bus.publish_and_drain("system.health.ok", SystemEvent("health.ok", clock.now(), "fine"))
    assert len(agent.sent) == 1 and "kill" in agent.sent[0]
    await bus.stop()


@pytest.mark.asyncio
async def test_disabled_agent_records_but_does_not_send(sim_clock) -> None:
    bus = EventBus()
    await bus.start()
    agent = await build_notifier(bus, enabled=False)
    await bus.publish_and_drain("system.kill", SystemEvent("kill", clock.now(), "panic"))
    assert agent.sent and agent.delivered == []
    await bus.stop()


@pytest.mark.asyncio
async def test_a_failing_transport_never_breaks_trading(sim_clock) -> None:
    async def broken(_message: str) -> None:
        raise RuntimeError("telegram down")

    agent = NotificationAgent(
        "notify", NotificationConfig(enabled=True), EventBus(), sender=broken, env={}
    )
    await agent.notify("hello")
    assert agent.failures == 1
