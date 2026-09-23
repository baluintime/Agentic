"""Parameter forms: what the user types is what the agent gets."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from agents.indicators.macd.agent import Config as MacdConfig
from agents.strategies.macd_s2_momentum_option.agent import Config as MacdS2Config
from core.strategy_base import StrategyConfig
from ui.forms import form_values, is_integer_field


class Element:
    def __init__(self, value) -> None:
        self.value = value


def widgets(model: type[BaseModel], **typed) -> dict[str, Element]:
    state = {name: Element(field.default) for name, field in model.model_fields.items()}
    for name, value in typed.items():
        state[name] = Element(value)
    return state


def test_integer_fields_are_read_from_the_annotation_not_the_default() -> None:
    """`target_points: float = Field(20, ...)` has an int literal as its default."""
    fields = StrategyConfig.model_fields
    assert is_integer_field(fields["lots"])
    assert not is_integer_field(fields["target_points"])
    assert not is_integer_field(fields["capital"])
    assert not is_integer_field(fields["stoploss_points"])


@pytest.mark.parametrize("value", [0.5, 0.25, 12.75, 0.05])
def test_a_fractional_target_survives_the_form(value) -> None:
    """The reported bug: a 0.5 target was silently rounded down to 0."""
    values = form_values(widgets(MacdS2Config, target_points=value), MacdS2Config)
    assert values["target_points"] == value


def test_a_fractional_stoploss_survives_the_form() -> None:
    values = form_values(widgets(MacdS2Config, stoploss_points=0.25), MacdS2Config)
    assert values["stoploss_points"] == 0.25


def test_a_zeroed_target_would_have_disabled_the_leg() -> None:
    """Why the truncation mattered: 0 means no target leg is placed at all."""
    from core.strategy_base import StrategyAgent

    agent = StrategyAgent.__new__(StrategyAgent)
    agent.config = StrategyConfig(target_points=0.5, stoploss_points=0.25)
    assert agent._target_stop(100.0) == (0.5, 0.25)
    agent.config = StrategyConfig(target_points=0, stoploss_points=0)
    assert agent._target_stop(100.0) == (None, None)


def test_whole_number_fields_still_come_back_as_ints() -> None:
    values = form_values(widgets(MacdS2Config, lots=3.0), MacdS2Config)
    assert values["lots"] == 3 and isinstance(values["lots"], int)


def test_a_fractional_lot_is_refused_with_a_readable_message() -> None:
    with pytest.raises(ValueError, match="Lots must be a whole number"):
        form_values(widgets(MacdS2Config, lots=2.5), MacdS2Config)


def test_indicator_periods_stay_whole() -> None:
    values = form_values(widgets(MacdConfig, fast=8.0), MacdConfig)
    assert values["fast"] == 8
    with pytest.raises(ValueError, match="whole number"):
        form_values(widgets(MacdConfig, slow=26.5), MacdConfig)


def test_an_empty_box_falls_back_to_the_default() -> None:
    values = form_values(widgets(MacdS2Config, target_points=None), MacdS2Config)
    assert values["target_points"] == MacdS2Config.model_fields["target_points"].default


def test_model_validation_still_applies() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        form_values(widgets(MacdS2Config, lots=0), MacdS2Config)  # lots has ge=1


def test_a_float_default_written_as_an_int_is_not_an_int_field() -> None:
    class Model(BaseModel):
        ratio: float = Field(2, description="written as an int literal")
        count: int = Field(2)

    assert not is_integer_field(Model.model_fields["ratio"])
    assert is_integer_field(Model.model_fields["count"])
    assert form_values(widgets(Model, ratio=0.5), Model)["ratio"] == 0.5
