"""Pipeline builder stepper: instrument -> ... -> Paper/Live.

Only combinations allowed by each agent's `requires` are offered, so a
misconfigured pipeline cannot be built.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui
from pydantic import ValidationError

from core.contracts import ExecMode, Product, Segment, Timeframe
from core.pipeline import PipelineSpec
from ui.forms import config_form, field_label, form_values

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
        self.labels: dict[str, dict] = {}
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
            with ui.row().classes("w-full items-end gap-2"):
                # One searchable dropdown over every tradable underlying: type to
                # filter, or open it and scroll. No separate search step.
                self.instrument_select = (
                    ui.select(
                        options={},
                        label="Instrument",
                        with_input=True,
                        on_change=self.on_instrument,
                    )
                    .classes("flex-1")
                    .props('clearable hide-selected fill-input input-debounce="0"')
                )
                self.reload_button = ui.button(
                    icon="refresh", on_click=self.load_instruments
                ).props("flat dense")
                self.reload_button.tooltip("Download today's instrument file")
            self.instrument_note = ui.label("").classes("text-xs text-gray-500")
            self.refresh_instruments()

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
        self.refresh_instruments()  # a login may have loaded them since last time
        self.dialog.open()

    def refresh_instruments(self) -> None:
        """Rebuild the dropdown from the Instrument Master."""
        rows = self.engine.instruments.tradable()
        self.instrument_select.set_options({row["instrument_key"]: row["label"] for row in rows})
        self.labels = {row["instrument_key"]: row for row in rows}
        if rows:
            self.instrument_note.set_text(f"{len(rows):,} instruments — type to filter")
        else:
            self.instrument_note.set_text(
                "no instruments loaded — press refresh to download today's instrument file"
            )

    async def load_instruments(self) -> None:
        self.reload_button.props("loading")
        try:
            count = await self.engine.instruments.load(force=True)
        except Exception as exc:
            ui.notify(f"could not download the instrument file: {exc}", type="negative")
            return
        finally:
            self.reload_button.props(remove="loading")
        self.refresh_instruments()
        ui.notify(f"loaded {count:,} instruments")

    def on_instrument(self, event) -> None:
        self.instrument = self.labels.get(event.value)

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
            ui.notify(
                "pick an instrument from the dropdown"
                if self.labels
                else "no instruments loaded — press refresh next to the dropdown",
                type="warning",
            )
            return
        if not (self.choice.get("indicator") and self.choice.get("strategy")):
            ui.notify("pick an indicator and a strategy", type="warning")
            return
        indicator_spec = self.engine.registry.get("indicator", self.choice["indicator"])
        strategy_spec = self.engine.registry.get("strategy", self.choice["strategy"])
        try:
            indicator_params = form_values(self.indicator_form, indicator_spec.cls.Config)
            strategy_params = form_values(self.strategy_form, strategy_spec.cls.Config)
        except (ValueError, ValidationError) as exc:
            ui.notify(_parameter_problem(exc), type="negative", timeout=8000)
            return
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
            indicator_params=indicator_params,
            strategy_params=strategy_params,
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


def _parameter_problem(exc: Exception) -> str:
    """Name the offending parameter rather than dumping a validation traceback."""
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first["loc"]) or "parameter"
        return f"{field_label(field)}: {first['msg']}"
    return str(exc)
