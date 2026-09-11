"""Charges agent: itemised brokerage and statutory charges for every leg.

Rates come only from `config/charges.yaml`, which carries an `effective_from`
date because statutory rates change. Until that file is filled in, the agent
reports `configured: false` and charges of zero rather than inventing rates.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel

from core import config as config_module
from core.base_agent import BaseAgent
from core.charges import ChargeBreakup, ChargesCalculator
from core.contracts import Product, Segment, Side


class ChargesConfig(BaseModel):
    config_name: str = "charges"


class ChargesAgent(BaseAgent):
    kind: ClassVar[str] = "system"
    name: ClassVar[str] = "charges"
    Config: ClassVar[type[BaseModel]] = ChargesConfig
    description: ClassVar[str] = "Brokerage, STT, exchange, SEBI, stamp duty and GST"

    def __init__(self, *args: Any, rates: dict | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calculator = ChargesCalculator(
            rates if rates is not None else config_module.load("charges")
        )

    def reload(self) -> None:
        config_module.reload()
        self.calculator = ChargesCalculator(config_module.load("charges"))

    def estimate_round_trip(
        self,
        *,
        segment: Segment,
        product: Product,
        entry_side: Side,
        price: float,
        quantity: int,
        target_points: float = 0.0,
    ) -> ChargeBreakup:
        """Round-trip charges the strategy widget shows before trading."""
        return self.calculator.round_trip(
            segment=segment,
            product=product,
            entry_side=entry_side,
            entry_price=price,
            exit_price=price + target_points,
            quantity=quantity,
        )

    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {
                "configured": self.calculator.configured,
                "effective_from": str(self.calculator.effective_from or "not set"),
                "note": "" if self.calculator.configured else "fill config/charges.yaml",
            }
        )
        return base

    def export_frames(self) -> dict[str, pd.DataFrame]:
        rates = self.calculator.rates
        if not rates:
            return {}
        rows = [{"key": k, "value": str(v)} for k, v in _flatten(rates)]
        return {"charge_rates": pd.DataFrame(rows)}


def _flatten(node: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(node, dict):
        out: list[tuple[str, Any]] = []
        for key, value in node.items():
            out.extend(_flatten(value, f"{prefix}.{key}" if prefix else str(key)))
        return out
    return [(prefix, node)]
