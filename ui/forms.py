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


def is_integer_field(field: Any) -> bool:
    """Read the declared annotation, never the default's Python type.

    `target_points: float = Field(20, ...)` has an int literal as its default;
    judging by that truncated every decimal the user typed.
    """
    return field.annotation is int


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
        elif is_integer_field(field):
            element = ui.number(label=field_label(name), value=default, step=1, precision=0)
        elif isinstance(default, int | float):
            # Decimals matter here: a 0.5 target is a normal thing to want.
            element = ui.number(label=field_label(name), value=default, step=0.05)
        else:
            element = ui.input(label=field_label(name), value=default)
        element.classes("w-full")
        if hint:
            element.tooltip(hint)
        state[name] = element
    return state


def form_values(state: dict[str, Any], model: type[BaseModel]) -> dict[str, Any]:
    """Read the widgets back and validate them through the pydantic model.

    Nothing is silently rounded: a value the model rejects raises, so the caller
    can tell the user which field is wrong instead of trading on a changed number.
    """
    raw: dict[str, Any] = {}
    for name, element in state.items():
        value = element.value
        if value is None:
            continue  # an empty box means "use the default"
        if is_integer_field(model.model_fields[name]) and isinstance(value, float):
            if value != int(value):
                raise ValueError(f"{field_label(name)} must be a whole number, got {value:g}")
            value = int(value)
        raw[name] = value
    return model(**raw).model_dump()
