"""Pipeline builder stepper: instrument -> ... -> Paper/Live.

Only combinations allowed by each agent's `requires` are offered, so a
misconfigured pipeline cannot be built.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from core.contracts import ExecMode, Product, Segment, Timeframe
from core.pipeline import PipelineSpec
from ui.forms import config_form, form_values

SEGMENTS = [s.value for s in Segment]
PRODUCTS = {"Intraday": Product.INTRADAY.value, "Delivery-Overnight": Product.DELIVERY.value}
TIMEFRAMES = [t.value for t in Timeframe if t is not Timeframe.TICK]


class PipelineBuilder:
    """One dialog, one pipeline. `on_create(spec)` receives the finished spec."""

    def __init__(self, engine, on_create) -> None:
        self.engine = engine
        self.on_create = on_create
        self.choice: dict[str, Any] = {
            "segment": Segment.OPTION.value,
            "product": Product.INTRADAY.value,
            "timeframe": Timeframe.M5.value,
            "exec_mode": ExecMode.PAPER.value,
            "expiry_rule": "nearest_weekly",
        }
        self.instrument: dict | None = None
        self.indicator_form: dict[str, Any] = {}
        self.strategy_form: dict[str, Any] = {}
        self.dialog = ui.dialog()
        self._build()

    # -- lookups -------------------------------------------------------------
    def indicators(self) -> list[str]:
        return [spec.name for spec in self.engine.registry.of_kind("indicator")]

    def strategies(self) -> list[str]:
        indicator = self.choice.get("indicator")
        if not indicator:
            return []
        return [
            spec.name
            for spec in self.engine.registry.strategies_for(indicator, self.choice["segment"])
        ]

    def order_agents(self) -> list[str]:
        return [
            spec.name
            for spec in self.engine.registry.of_kind("order")
            if spec.name != "paper"  # paper mode selects the Paper agent automatically
        ]

    # -- layout --------------------------------------------------------------
    def _build(self) -> None:
        with self.dialog, ui.card().classes("w-[680px]"):
            ui.label("New pipeline").classes("text-lg font-medium")
            self.search = ui.input("Instrument", placeholder="NIFTY, RELIANCE, ...").classes(
                "w-full"
            )
            self.search.on("keydown.enter", self.do_search)
            self.results = ui.column().classes("w-full gap-0")
            self.chosen = ui.label("no instrument selected").classes("text-sm text-gray-500")

            with ui.row().classes("w-full gap-4"):
                self.segment = ui.select(
                    SEGMENTS,
                    value=self.choice["segment"],
                    label="Segment",
                    on_change=self.on_segment,
                ).classes("flex-1")
                self.product = ui.select(list(PRODUCTS), value="Intraday", label="Product").classes(
                    "flex-1"
                )
                self.timeframe = ui.select(
                    TIMEFRAMES, value=self.choice["timeframe"], label="Timeframe"
                ).classes("flex-1")

            with ui.row().classes("w-full gap-4"):
                self.indicator = ui.select(
                    self.indicators(), label="Indicator", on_change=self.on_indicator
                ).classes("flex-1")
                self.strategy = ui.select([], label="Strategy", on_change=self.on_strategy).classes(
                    "flex-1"
                )

            with ui.row().classes("w-full gap-4"):
                self.order_agent = ui.select(
                    self.order_agents(), value="gtt", label="Order agent"
                ).classes("flex-1")
                self.exec_mode = ui.select(
                    [m.value for m in ExecMode], value=ExecMode.PAPER.value, label="Execution"
                ).classes("flex-1")
                self.expiry_rule = ui.select(
                    ["nearest_weekly", "nearest_monthly"],
                    value="nearest_weekly",
                    label="Expiry",
                ).classes("flex-1")

            ui.separator()
            ui.label("Parameters").classes("text-sm text-gray-500")
            self.params = ui.column().classes("w-full gap-2")

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=self.dialog.close).props("flat")
                ui.button("Create", on_click=self.create)

    # -- interactions --------------------------------------------------------
    def open(self) -> None:
        self.dialog.open()

    def do_search(self) -> None:
        self.results.clear()
        matches = self.engine.instruments.search(self.search.value or "")
        with self.results:
            if not matches:
                ui.label("nothing found — is the instrument master loaded?").classes(
                    "text-xs text-gray-500"
                )
            for match in matches[:10]:
                ui.button(
                    f"{match['trading_symbol']} · {match['segment']}",
                    on_click=lambda m=match: self.pick(m),
                ).props("flat dense align=left").classes("w-full")

    def pick(self, match: dict) -> None:
        self.instrument = match
        self.chosen.set_text(f"{match['trading_symbol']} ({match['instrument_key']})")
        self.results.clear()

    def on_segment(self, event) -> None:
        self.choice["segment"] = event.value
        self.strategy.set_options(self.strategies())

    def on_indicator(self, event) -> None:
        self.choice["indicator"] = event.value
        self.strategy.set_options(self.strategies())
        self.render_params()

    def on_strategy(self, event) -> None:
        self.choice["strategy"] = event.value
        self.render_params()

    def render_params(self) -> None:
        self.params.clear()
        with self.params:
            if self.choice.get("indicator"):
                spec = self.engine.registry.get("indicator", self.choice["indicator"])
                ui.label(f"{spec.name} parameters").classes("text-xs text-gray-500")
                self.indicator_form = config_form(spec.cls.Config, spec.config_defaults())
            if self.choice.get("strategy"):
                spec = self.engine.registry.get("strategy", self.choice["strategy"])
                ui.label(f"{spec.name} parameters").classes("text-xs text-gray-500")
                self.strategy_form = config_form(spec.cls.Config, spec.config_defaults())

    async def create(self) -> None:
        if not self.instrument:
            ui.notify("pick an instrument first", type="warning")
            return
        if not (self.choice.get("indicator") and self.choice.get("strategy")):
            ui.notify("pick an indicator and a strategy", type="warning")
            return
        indicator_spec = self.engine.registry.get("indicator", self.choice["indicator"])
        strategy_spec = self.engine.registry.get("strategy", self.choice["strategy"])
        spec = PipelineSpec(
            underlying_key=self.instrument["instrument_key"],
            underlying_symbol=self.instrument["trading_symbol"],
            indicator=self.choice["indicator"],
            strategy=self.choice["strategy"],
            segment=Segment(self.segment.value),
            product=Product(PRODUCTS[self.product.value]),
            timeframe=Timeframe(self.timeframe.value),
            order_agent=self.order_agent.value,
            exec_mode=ExecMode(self.exec_mode.value),
            expiry_rule=self.expiry_rule.value,
            indicator_params=form_values(self.indicator_form, indicator_spec.cls.Config),
            strategy_params=form_values(self.strategy_form, strategy_spec.cls.Config),
        )
        if spec.live and not self.engine.armed_live:
            ui.notify("arm the pipeline for LIVE in the header first", type="warning")
            return
        try:
            await self.on_create(spec)
        except Exception as exc:
            ui.notify(str(exc), type="negative")
            return
        self.dialog.close()
        ui.notify(f"pipeline {spec.name} created")
