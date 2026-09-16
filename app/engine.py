"""Composition root: builds pipelines out of agents and wires them to the bus.

One pipeline = instrument -> candles -> indicator -> strategy -> risk -> order
agent. Candle agents and order agents are shared and reference counted, so ten
pipelines on the same instrument still mean one WebSocket subscription and one
candle builder (principle P2: fetch once, share many).
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from broker.auth import AuthManager
from broker.instruments import InstrumentMaster
from broker.rest import UpstoxRest
from broker.ws_feed import MarketDataHub, UpstoxFeedClient
from core import clock
from core import config as config_module
from core.base_agent import BaseAgent
from core.bus import EventBus
from core.charges import ChargesCalculator
from core.contracts import ExecMode, Segment, SystemEvent, Tick, Timeframe, system_topic
from core.indicator_base import IndicatorAgent
from core.pipeline import PipelineSpec
from core.registry import Registry, get_registry
from core.store import Store
from core.strategy_base import StrategyAgent
from system_agents.analytics import AnalyticsAgent
from system_agents.candles import CandleAgent
from system_agents.charges import ChargesAgent
from system_agents.export import ExportAgent
from system_agents.health import HealthAgent
from system_agents.notifications import NotificationAgent
from system_agents.persistence import PersistenceAgent
from system_agents.recorder import RecorderAgent
from system_agents.replay import ReplayAgent
from system_agents.risk import RiskAgent, RiskConfig
from system_agents.squareoff import SquareOffAgent, SquareOffConfig

log = logging.getLogger("engine")

SOURCE_TIMEFRAME = {Timeframe.M5: Timeframe.M1}


@dataclass
class Pipeline:
    spec: PipelineSpec
    indicator: IndicatorAgent
    strategy: StrategyAgent
    candle_keys: list[tuple[str, str]] = field(default_factory=list)
    subscribed: list[str] = field(default_factory=list)

    @property
    def agents(self) -> list[BaseAgent]:
        return [self.indicator, self.strategy]


class Engine:
    """Owns the bus, the shared agents and every pipeline."""

    def __init__(
        self,
        bus: EventBus | None = None,
        store: Store | None = None,
        registry: Registry | None = None,
        rest: UpstoxRest | None = None,
        instruments: InstrumentMaster | None = None,
        hub: MarketDataHub | None = None,
        auth: AuthManager | None = None,
    ) -> None:
        self.bus = bus or EventBus()
        self.store = store or Store()
        self.registry = registry or get_registry()
        self.rest = rest or UpstoxRest()
        self.instruments = instruments or InstrumentMaster(cache_dir=self.store.base / "cache")
        self.auth = auth or AuthManager()
        self.hub = hub or MarketDataHub("market-data", None, self.bus, self.store)
        self.pipelines: dict[str, Pipeline] = {}
        self.candles: dict[tuple[str, str], CandleAgent] = {}
        self.candle_refs: Counter[tuple[str, str]] = Counter()
        self.order_agents: dict[str, BaseAgent] = {}
        self.armed_live = False
        self.started = False
        self.data_cfg = config_module.load("data")
        self.session_cfg = config_module.load("session")
        self._option_windows: dict[str, tuple[float, list[str]]] = {}

        self.charges_agent = ChargesAgent("charges", None, self.bus, self.store)
        self.charges: ChargesCalculator = self.charges_agent.calculator
        self.risk = RiskAgent(
            "risk", RiskConfig(**config_module.load("risk")), self.bus, self.store
        )
        self.squareoff = SquareOffAgent(
            "squareoff",
            SquareOffConfig(**_subset(SquareOffConfig, self.session_cfg)),
            self.bus,
            self.store,
            rest=self.rest,
            auth=self.auth,
        )
        self.health = HealthAgent(
            "health",
            None,
            self.bus,
            self.store,
            hub=self.hub,
            auth=self.auth,
        )
        self.recorder = RecorderAgent("recorder", None, self.bus, self.store)
        self.persistence = PersistenceAgent(
            "persistence", None, self.bus, self.store, rest=self.rest
        )
        self.analytics = AnalyticsAgent("analytics", None, self.bus, self.store)
        self.notifications = NotificationAgent("notifications", None, self.bus, self.store)
        self.replay = ReplayAgent("replay", None, self.bus, self.store)
        self.export = ExportAgent("export", None, self.bus, self.store, agents=self.all_agents)

    # -- lifecycle -----------------------------------------------------------
    @property
    def system_agents(self) -> list[BaseAgent]:
        return [
            self.hub,
            self.risk,
            self.squareoff,
            self.health,
            self.recorder,
            self.charges_agent,
            self.persistence,
            self.analytics,
            self.notifications,
            self.export,
        ]

    def all_agents(self) -> list[BaseAgent]:
        agents: list[BaseAgent] = list(self.system_agents)
        agents += list(self.candles.values())
        agents += list(self.order_agents.values())
        for pipeline in self.pipelines.values():
            agents += pipeline.agents
        return agents

    async def start(self) -> None:
        await self.bus.start()
        for agent in self.system_agents:
            await agent.start()
        self.bus.subscribe("tick.*", self._on_underlying_tick, "engine")
        self.started = True
        log.info("engine started (%d system agents)", len(self.system_agents))

    async def stop(self) -> None:
        for pipeline in list(self.pipelines):
            await self.remove_pipeline(pipeline)
        for agent in list(self.order_agents.values()) + list(self.system_agents):
            await agent.stop()
        await self.bus.stop()
        self.started = False

    async def connect(self) -> dict[str, Any]:
        """Token -> instruments -> reconciliation. Safe to call again later."""
        report: dict[str, Any] = {}
        state = self.auth.load()
        state = await self.auth.validate(self.rest)
        report["token"] = state.redacted()
        # The instrument file is a public asset — load it even when logged out, so
        # the pipeline builder can offer instruments before the daily login.
        try:
            report["instruments"] = await self.instruments.load()
        except Exception as exc:
            report["instruments_error"] = str(exc)
            log.warning("instrument master not loaded: %s", exc)
        if state.valid:
            report["feed"] = self.attach_feed()
            report["reconciliation"] = await self.persistence.reconcile()
        return report

    def attach_feed(self) -> str:
        """Give the hub a real WebSocket once there is a token to authenticate it.

        The hub's connect loop is already running and picks the client up on its
        next pass, resubscribing everything the pipelines asked for.
        """
        if self.hub.client is not None:
            return "already attached"
        self.hub.client = UpstoxFeedClient(self.rest)
        log.info("market data feed attached (%d subscriptions)", len(self.hub.subscriptions))
        return "attached"

    def label_for(self, instrument_key: str) -> str:
        """A human name for an instrument key, for the console's price list."""
        row = self.instruments.row(instrument_key)
        if row is None:
            return instrument_key
        return str(row["trading_symbol"] or row["name"] or instrument_key)

    # -- pipelines -----------------------------------------------------------
    async def add_pipeline(self, spec: PipelineSpec) -> Pipeline:
        indicator_spec = self.registry.get("indicator", spec.indicator)
        strategy_spec = self.registry.get("strategy", spec.strategy)
        _check_compatible(strategy_spec.requires, spec)

        await self._ensure_candles(spec)
        indicator = indicator_spec.cls(
            agent_id=f"{spec.pipeline_id}-{spec.indicator}",
            config=indicator_spec.build_config(spec.indicator_params),
            bus=self.bus,
            store=self.store,
            pipeline_id=spec.pipeline_id,
            instrument_key=spec.underlying_key,
            timeframe=spec.timeframe,
            evaluate_on=spec.evaluate_on,
        )
        strategy = strategy_spec.cls(
            agent_id=f"{spec.pipeline_id}-{spec.strategy}",
            config=strategy_spec.build_config(spec.strategy_params),
            bus=self.bus,
            store=self.store,
            pipeline_id=spec.pipeline_id,
            spec=spec,
            resolver=self.instruments,
            charges=self.charges,
        )
        candle_agent = self.candles.get((spec.underlying_key, spec.timeframe.value))
        if candle_agent is not None:
            candle_agent.warmup_bars = max(candle_agent.warmup_bars, indicator.warmup_bars())
            indicator.seed(candle_agent.frame)
        await indicator.start()
        await strategy.start()
        await self._ensure_order_agent(spec)

        pipeline = Pipeline(spec=spec, indicator=indicator, strategy=strategy)
        pipeline.candle_keys = self._candle_keys(spec)
        self.pipelines[spec.pipeline_id] = pipeline
        await self._subscribe_market_data(pipeline)
        self.apply_session_state(strategy)
        if spec.paused:
            strategy.pause()
        log.info("pipeline %s added (%s)", spec.pipeline_id, spec.name)
        return pipeline

    async def remove_pipeline(self, pipeline_id: str) -> None:
        pipeline = self.pipelines.pop(pipeline_id, None)
        if pipeline is None:
            return
        await pipeline.strategy.stop_trading("pipeline removed")
        for agent in pipeline.agents:
            await agent.stop()
        await self.hub.unsubscribe_many(pipeline.subscribed)
        self._option_windows.pop(pipeline_id, None)
        for key in pipeline.candle_keys:
            self.candle_refs[key] -= 1
            if self.candle_refs[key] <= 0:
                agent = self.candles.pop(key, None)
                self.candle_refs.pop(key, None)
                if agent:
                    await agent.stop()

    def pipeline(self, pipeline_id: str) -> Pipeline | None:
        return self.pipelines.get(pipeline_id)

    def apply_session_state(self, strategy: StrategyAgent) -> None:
        """Catch a new strategy up on system events it was not alive to hear.

        A pipeline added after the square-off cut-off, or while the kill switch
        is on, must not start taking entries because it missed the broadcast.
        """
        today = clock.now().date()
        if self.squareoff.no_new_entries_done == today or self.squareoff.squareoff_done == today:
            strategy.new_entries_blocked = True
        if self.risk.blocked:
            strategy.new_entries_blocked = True
        if self.risk.killed:
            strategy.halted = True
            strategy.new_entries_blocked = True

    # -- shared agents -------------------------------------------------------
    def _candle_keys(self, spec: PipelineSpec) -> list[tuple[str, str]]:
        chain = [spec.timeframe]
        source = SOURCE_TIMEFRAME.get(spec.timeframe)
        if source:
            chain.append(source)
        return [(spec.underlying_key, tf.value) for tf in chain]

    async def _ensure_candles(self, spec: PipelineSpec) -> None:
        indicator_spec = self.registry.get("indicator", spec.indicator)
        probe = indicator_spec.cls(
            "probe", indicator_spec.build_config(spec.indicator_params), timeframe=spec.timeframe
        )
        warmup = probe.warmup_bars()
        for key in reversed(self._candle_keys(spec)):  # source timeframe first
            instrument_key, timeframe = key
            self.candle_refs[key] += 1
            if key in self.candles:
                continue
            agent = CandleAgent(
                f"candles-{instrument_key}-{timeframe}",
                instrument_key=instrument_key,
                timeframe=Timeframe(timeframe),
                bus=self.bus,
                store=self.store,
                rest=self.rest,
                history_days=int((self.data_cfg.get("history_days") or {}).get(timeframe, 5)),
                warmup_bars=warmup if timeframe == spec.timeframe.value else 0,
            )
            self.candles[key] = agent
            await agent.start()
            if self.auth.state.valid:
                await agent.load_history()

    async def _ensure_order_agent(self, spec: PipelineSpec) -> None:
        name = spec.effective_order_agent
        if name in self.order_agents:
            self.order_agents[name].armed_live = self.armed_live  # type: ignore[attr-defined]
            return
        order_spec = self.registry.get("order", name)
        kwargs: dict[str, Any] = {}
        if name != "paper":
            kwargs["rest"] = self.rest
        agent = order_spec.cls(
            agent_id=f"order-{name}",
            config=order_spec.build_config(),
            bus=self.bus,
            store=self.store,
            armed_live=self.armed_live,
            **kwargs,
        )
        self.order_agents[name] = agent
        await agent.start()

    # -- market data ---------------------------------------------------------
    async def _subscribe_market_data(self, pipeline: Pipeline) -> None:
        spec = pipeline.spec
        keys = [spec.underlying_key]
        await self.hub.subscribe_many(keys)
        pipeline.subscribed = list(keys)

    async def _on_underlying_tick(self, tick: Tick) -> None:
        """Keep the option window centred on the ATM strike as spot moves."""
        for pipeline in list(self.pipelines.values()):
            spec = pipeline.spec
            if spec.segment is not Segment.OPTION or tick.instrument_key != spec.underlying_key:
                continue
            await self._recentre(pipeline, tick.ltp)

    async def _recentre(self, pipeline: Pipeline, spot: float) -> None:
        spec = pipeline.spec
        try:
            step = self.instruments.strike_step(spec.underlying_key, spec.expiry_rule)
        except Exception:
            return
        centre, keys = self._option_windows.get(spec.pipeline_id, (None, []))
        if centre is not None and abs(spot - centre) < step:
            return
        window = int(self.data_cfg.get("option_strike_window", 2))
        try:
            fresh = self.instruments.strike_window(
                spec.underlying_key, spot, window, spec.expiry_rule
            )
        except Exception as exc:
            log.debug("strike window unavailable: %s", exc)
            return
        stale = [k for k in keys if k not in fresh]
        added = [k for k in fresh if k not in keys]
        await self.hub.subscribe_many(added)
        await self.hub.unsubscribe_many(stale)
        pipeline.subscribed = [spec.underlying_key] + fresh
        self._option_windows[spec.pipeline_id] = (spot, fresh)

    # -- controls ------------------------------------------------------------
    async def arm_live(self, confirmed: bool) -> tuple[bool, str]:
        """Live needs a confirmation, a valid token, Risk enabled and reconciliation."""
        if not confirmed:
            return False, "confirmation required"
        if not self.auth.state.valid:
            return False, "token is not valid"
        if not getattr(self.risk.config, "enabled", True):
            return False, "risk agent is disabled"
        if not self.persistence.passed:
            return False, "broker reconciliation has not passed"
        for pipeline in self.pipelines.values():
            if not pipeline.spec.live:
                continue
            unlocked, reason = self.analytics.live_unlocked(pipeline.spec.strategy)
            if not unlocked:
                return False, f"{pipeline.spec.strategy}: {reason}"
        self.armed_live = True
        for agent in self.order_agents.values():
            agent.armed_live = True  # type: ignore[attr-defined]
        return True, "armed for LIVE"

    async def disarm_live(self) -> None:
        self.armed_live = False
        for agent in self.order_agents.values():
            agent.armed_live = False  # type: ignore[attr-defined]

    async def kill(self, reason: str = "kill switch") -> None:
        """Cancel GTTs, square off everything, stop every strategy."""
        await self.disarm_live()
        await self.bus.publish(
            system_topic("squareoff.cancel_gtt"),
            SystemEvent("squareoff.cancel_gtt", clock.now(), reason),
        )
        await self.risk.kill(reason)
        for pipeline in self.pipelines.values():
            pipeline.strategy.pause()

    def totals(self) -> dict[str, float]:
        gross = charges = net = open_mtm = 0.0
        for pipeline in self.pipelines.values():
            log_ = pipeline.strategy.trades
            gross += log_.gross
            charges += log_.charges
            net += log_.net
            open_mtm += pipeline.strategy.position.unrealised()
        return {
            "gross": round(gross, 2),
            "charges": round(charges, 2),
            "net": round(net, 2),
            "open_mtm": round(open_mtm, 2),
        }

    def status(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "armed_live": self.armed_live,
            "mode": ExecMode.LIVE.value if self.armed_live else ExecMode.PAPER.value,
            "pipelines": len(self.pipelines),
            "totals": self.totals(),
            "bus": self.bus.stats(),
            "agents": {agent.agent_id: agent.status() for agent in self.all_agents()},
        }


def _check_compatible(requires: list[str], spec: PipelineSpec) -> None:
    for requirement in requires:
        if requirement.startswith("segment:"):
            wanted = requirement.split(":", 1)[1]
            if spec.segment.value != wanted:
                raise ValueError(
                    f"{spec.strategy} requires segment {wanted}, got {spec.segment.value}"
                )
        elif ":" not in requirement and requirement != spec.indicator:
            raise ValueError(
                f"{spec.strategy} requires indicator {requirement}, got {spec.indicator}"
            )


def _subset(model: type, values: dict) -> dict:
    return {k: v for k, v in values.items() if k in model.model_fields}
