"""The NiceGUI console: header, pipelines, positions, logs and settings."""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from nicegui import ui

from core import clock
from core.contracts import Timeframe
from core.pipeline import PipelineSpec
from ui.builder import PipelineBuilder
from ui.widgets import AgentCard, StrategyCard, money

REFRESH_SECONDS = 1.0
LOG_LINES = 400


class LogBuffer(logging.Handler):
    """Keeps the last lines in memory so the logs page never reads a file."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: deque[tuple[str, str, str]] = deque(maxlen=LOG_LINES)

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append((record.name, record.levelname, self.format(record)))


class Console:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.logs = LogBuffer()
        self.logs.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(self.logs)
        self.cards: dict[str, Any] = {}
        self._register()

    # -- pages ---------------------------------------------------------------
    def _register(self) -> None:
        ui.page("/")(self.pipelines_page)
        ui.page("/positions")(self.positions_page)
        ui.page("/logs")(self.logs_page)
        ui.page("/settings")(self.settings_page)

    def header(self) -> None:
        with ui.header().classes("items-center justify-between px-4 py-2"):
            with ui.row().classes("items-center gap-4"):
                ui.label("Upstox Agents").classes("text-lg font-medium")
                self.token_badge = ui.badge("checking token").props("outline")
                self.user_label = ui.label("").classes("text-sm")
                self.clock_label = ui.label("").classes("text-sm")
            with ui.row().classes("items-center gap-4"):
                self.totals_label = ui.label("").classes("text-sm")
                ui.button("Download All", on_click=self.download_all).props("flat dense")
                ui.button("Kill Switch", on_click=self.confirm_kill).props("dense color=negative")
            with ui.row().classes("gap-2"):
                ui.link("Pipelines", "/").classes("text-white text-sm")
                ui.link("Positions", "/positions").classes("text-white text-sm")
                ui.link("Logs", "/logs").classes("text-white text-sm")
                ui.link("Settings", "/settings").classes("text-white text-sm")
        ui.timer(REFRESH_SECONDS, self.refresh_header)

    def refresh_header(self) -> None:
        state = self.engine.auth.state
        self.token_badge.set_text("token ok" if state.valid else (state.message or "no token"))
        self.token_badge.props(f"color={'positive' if state.valid else 'negative'}")
        self.user_label.set_text(state.user_name or "")
        now = clock.now()
        session = "open" if clock.get_clock().is_market_open() else "closed"
        self.clock_label.set_text(f"{now:%H:%M:%S} IST · market {session}")
        totals = self.engine.totals()
        self.totals_label.set_text(
            f"gross {money(totals['gross'])} · charges {money(totals['charges'])} · "
            f"net {money(totals['net'])} · open {money(totals['open_mtm'])}"
        )

    # -- pipelines -----------------------------------------------------------
    def pipelines_page(self) -> None:
        self.header()
        with ui.row().classes("w-full items-center gap-2 p-4"):
            builder = PipelineBuilder(self.engine, self.add_pipeline)
            ui.button("Add pipeline", on_click=builder.open)
            ui.button("Save workspace", on_click=self.save_workspace).props("flat")
            ui.button("Load workspace", on_click=self.load_workspace).props("flat")
            self.arm_switch = ui.switch("Armed LIVE", on_change=self.toggle_live)
        self.pipeline_grid = ui.column().classes("w-full p-4 gap-4")
        self.system_grid = ui.row().classes("w-full p-4 gap-4 flex-wrap")
        self.render_pipelines()
        ui.timer(REFRESH_SECONDS, self.refresh_cards)

    def render_pipelines(self) -> None:
        self.pipeline_grid.clear()
        self.system_grid.clear()
        self.cards.clear()
        with self.pipeline_grid:
            if not self.engine.pipelines:
                ui.label("no pipelines yet — add one to start").classes("text-gray-500")
            for pipeline in self.engine.pipelines.values():
                with ui.row().classes("w-full gap-4 items-start"):
                    with ui.column().classes("flex-1"):
                        self.cards[pipeline.strategy.agent_id] = StrategyCard(
                            pipeline.strategy, self.export_agent, self.remove_pipeline
                        )
                    with ui.column().classes("w-72"):
                        self.cards[pipeline.indicator.agent_id] = AgentCard(
                            pipeline.indicator, self.export_agent
                        )
        with self.system_grid:
            for agent in self.engine.system_agents + list(self.engine.candles.values()):
                with ui.column().classes("w-72"):
                    self.cards[agent.agent_id] = AgentCard(agent, self.export_agent)

    def refresh_cards(self) -> None:
        for card in list(self.cards.values()):
            card.refresh()

    async def add_pipeline(self, spec: PipelineSpec) -> None:
        await self.engine.add_pipeline(spec)
        self.render_pipelines()

    async def remove_pipeline(self, strategy) -> None:
        await self.engine.remove_pipeline(strategy.pipeline_id)
        self.render_pipelines()

    # -- controls ------------------------------------------------------------
    async def toggle_live(self, event) -> None:
        if not event.value:
            await self.engine.disarm_live()
            ui.notify("LIVE disarmed")
            return
        with ui.dialog() as dialog, ui.card():
            ui.label("Arm every pipeline for LIVE trading?").classes("text-base")
            ui.label(
                "Requires a valid token, the Risk agent enabled and a passed reconciliation."
            ).classes("text-xs text-gray-500")
            with ui.row().classes("justify-end gap-2"):
                ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
                ui.button("Arm LIVE", on_click=lambda: dialog.submit(True)).props("color=negative")
        confirmed = await dialog
        armed, message = await self.engine.arm_live(bool(confirmed))
        self.arm_switch.value = armed
        ui.notify(message, type="positive" if armed else "warning")

    async def confirm_kill(self) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label("Kill switch").classes("text-lg")
            ui.label("Cancels pending orders and GTTs, squares off everything, stops strategies.")
            with ui.row().classes("justify-end gap-2"):
                ui.button("Cancel", on_click=lambda: dialog.submit(False)).props("flat")
                ui.button("Kill now", on_click=lambda: dialog.submit(True)).props("color=negative")
        if await dialog:
            await self.engine.kill("console kill switch")
            self.arm_switch.value = False
            ui.notify("kill switch activated", type="negative")

    def download_all(self) -> None:
        path = self.engine.export.export_all()
        ui.download(str(path))
        ui.notify(f"exported {path.name}")

    def export_agent(self, agent) -> None:
        path = self.engine.export.export_agent(agent)
        if path is None:
            ui.notify("nothing to export yet", type="warning")
            return
        ui.download(str(path))

    def save_workspace(self) -> None:
        specs = [p.spec for p in self.engine.pipelines.values()]
        path = self.engine.persistence.save(specs)
        ui.notify(f"saved {path.name}")

    async def load_workspace(self) -> None:
        specs = self.engine.persistence.load()
        for spec in specs:
            if spec.pipeline_id not in self.engine.pipelines:
                await self.engine.add_pipeline(spec)
        self.render_pipelines()
        ui.notify(f"loaded {len(specs)} pipeline(s) — paused; resume each one to trade")

    # -- other pages ---------------------------------------------------------
    def positions_page(self) -> None:
        self.header()
        with ui.column().classes("w-full p-4 gap-4"):
            table = ui.table(
                columns=[
                    {"name": c, "label": c.replace("_", " "), "field": c}
                    for c in (
                        "pipeline",
                        "instrument",
                        "state",
                        "quantity",
                        "entry_price",
                        "ltp",
                        "target",
                        "stoploss",
                        "mtm",
                    )
                ],
                rows=[],
                row_key="pipeline",
            ).classes("w-full")

            def refresh() -> None:
                rows = []
                for pipeline in self.engine.pipelines.values():
                    snapshot = pipeline.strategy.position.snapshot()
                    rows.append({"pipeline": pipeline.spec.name, **snapshot})
                table.rows = rows
                table.update()

            refresh()
            ui.timer(REFRESH_SECONDS, refresh)

    def logs_page(self) -> None:
        self.header()
        with ui.column().classes("w-full p-4 gap-2"):
            filter_input = ui.input("Filter by agent or text").classes("w-full")
            area = ui.log(max_lines=LOG_LINES).classes("w-full h-96 text-xs")
            seen = {"count": 0}

            def refresh() -> None:
                lines = list(self.logs.lines)
                if len(lines) == seen["count"]:
                    return
                needle = (filter_input.value or "").lower()
                area.clear()
                for name, _level, line in lines:
                    if needle and needle not in line.lower() and needle not in name.lower():
                        continue
                    area.push(line)
                seen["count"] = len(lines)

            refresh()
            ui.timer(REFRESH_SECONDS, refresh)

    def settings_page(self) -> None:
        self.header()
        with ui.column().classes("w-full p-4 gap-4"):
            ui.label("Session, risk and charges").classes("text-lg")
            for agent in (self.engine.risk, self.engine.squareoff, self.engine.charges_agent):
                with ui.card().classes("w-full"):
                    ui.label(agent.agent_id).classes("text-base font-medium")
                    AgentCard(agent)
            with ui.card().classes("w-full"):
                ui.label("Data").classes("text-base font-medium")
                ui.label(
                    f"history: {self.engine.data_cfg.get('history_days')} · "
                    f"strike window: ±{self.engine.data_cfg.get('option_strike_window')}"
                ).classes("text-xs")
                ui.label(f"timeframes built locally: {[t.value for t in Timeframe]}").classes(
                    "text-xs"
                )


def build(engine) -> Console:
    return Console(engine)
