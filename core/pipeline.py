"""One pipeline = one row of user choices in the console stepper."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from core.contracts import ExecMode, Product, Segment, Timeframe


@dataclass
class PipelineSpec:
    underlying_key: str
    underlying_symbol: str
    indicator: str
    strategy: str
    pipeline_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    segment: Segment = Segment.OPTION
    product: Product = Product.INTRADAY
    timeframe: Timeframe = Timeframe.M5
    order_agent: str = "gtt"
    exec_mode: ExecMode = ExecMode.PAPER
    expiry_rule: str = "nearest_weekly"  # nearest_weekly | nearest_monthly
    evaluate_on: str = "close"  # close | every_tick
    indicator_params: dict[str, Any] = field(default_factory=dict)
    strategy_params: dict[str, Any] = field(default_factory=dict)
    paused: bool = False

    def __post_init__(self) -> None:
        self.segment = Segment(self.segment)
        self.product = Product(self.product)
        self.timeframe = Timeframe(self.timeframe)
        self.exec_mode = ExecMode(self.exec_mode)
        if not self.name:
            self.name = f"{self.underlying_symbol} {self.strategy} {self.timeframe.value}"

    @property
    def live(self) -> bool:
        return self.exec_mode is ExecMode.LIVE

    @property
    def effective_order_agent(self) -> str:
        """Paper mode always routes to the Paper Order Agent."""
        return self.order_agent if self.live else "paper"

    @property
    def intraday(self) -> bool:
        return self.product is Product.INTRADAY

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("segment", "product", "timeframe", "exec_mode"):
            data[key] = getattr(self, key).value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineSpec:
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)
