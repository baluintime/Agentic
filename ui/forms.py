"""Forms generated from an agent's pydantic `Config`.

Nothing here is per-agent: a new agent gets its parameter form for free, which
is the whole point of `Config` doing double duty as schema and UI description.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui
from pydantic import BaseModel

CHOICES = {
    "on_opposite_signal": ["exit", "reverse", "ignore"],
    "evaluate_on": ["close", "every_tick"],
    "target_mode": ["points", "percent"],
    "span_reference": ["projected", "current"],
    "source": ["close", "open", "high", "low"],
    "validity": ["DAY", "IOC"],
    "expiry_rule": ["nearest_weekly", "nearest_monthly"],
    "tick_format": ["parquet", "csv"],
}


def field_label(name: str) -> str:
    return name.replace("_", " ").capitalize()


def config_form(model: type[BaseModel], values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Renders one input per field and returns a live dict of the chosen values."""
    values = dict(values or {})
    state: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        default = values.get(name, field.default)
        hint = field.description or ""
        if name in CHOICES:
            element = ui.select(CHOICES[name], value=default, label=field_label(name))
        elif isinstance(default, bool):
            element = ui.switch(field_label(name), value=default)
        elif isinstance(default, int | float):
            element = ui.number(label=field_label(name), value=default, format="%g")
        else:
            element = ui.input(label=field_label(name), value=default)
        element.classes("w-full")
        if hint:
            element.tooltip(hint)
        state[name] = element
    return state


def form_values(state: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    """Reads the widgets back and coerces them through the pydantic model."""
    raw = {name: element.value for name, element in state.items()}
    for name, field in model.model_fields.items():
        if isinstance(field.default, int) and not isinstance(field.default, bool):
            if raw.get(name) is not None:
                raw[name] = int(raw[name])
    return model(**raw).model_dump()
