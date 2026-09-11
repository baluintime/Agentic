"""Payload building and leg-status mapping for the GTT agent.

Kept beside `agent.py` so the agent itself stays small and this is the one file
to touch when the Upstox GTT payload shape changes.
Docs: https://upstox.com/developer/api-documentation/place-gtt-order/
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.contracts import OrderRequest, OrderStatus, Side


@dataclass
class OpenGtt:
    """A GTT whose legs are live at the broker."""

    correlation_id: str
    origin_agent_id: str
    gtt_order_id: str
    instrument_key: str
    side: Side  # side of the *entry*
    quantity: int
    entry_price: float
    target_price: float | None
    stoploss_price: float | None


def entry_payload(req: OrderRequest, quantity: int, tag: str, validity: str) -> dict[str, Any]:
    return {
        "quantity": quantity,
        "product": req.product,
        "validity": validity,
        "price": 0,
        "tag": tag,
        "instrument_token": req.instrument_key,
        "order_type": "MARKET",
        "transaction_type": req.side.value,
        "disclosed_quantity": 0,
        "trigger_price": 0,
        "is_amo": False,
        "slice": False,  # sliced by the agent so every slice keeps its own tag
    }


def gtt_payload(
    req: OrderRequest, quantity: int, target: float | None, stop: float | None, tag: str
) -> dict[str, Any]:
    """Exit legs: a target trigger and a stop-loss trigger on the opposite side."""
    rules: list[dict[str, Any]] = []
    if target is not None:
        rules.append(
            {
                "strategy": "ENTRY",
                "trigger_type": "ABOVE" if req.side is Side.BUY else "BELOW",
                "trigger_price": round(target, 2),
            }
        )
    if stop is not None:
        rules.append(
            {"strategy": "STOPLOSS", "trigger_type": "IMMEDIATE", "trigger_price": round(stop, 2)}
        )
    return {
        "type": "MULTIPLE" if len(rules) > 1 else "SINGLE",
        "quantity": quantity,
        "product": req.product,
        "instrument_token": req.instrument_key,
        "transaction_type": req.side.opposite.value,
        "tag": tag,
        "rules": rules,
    }


def leg_status(update: dict[str, Any], gtt: OpenGtt) -> str | None:
    """Which leg fired, or None when the update is not terminal."""
    raw = str(update.get("status", "")).lower()
    if raw in ("cancelled", "canceled"):
        return OrderStatus.CANCELLED.value
    if raw not in ("complete", "filled", "triggered"):
        return None
    leg = str(update.get("rule") or update.get("strategy") or "").upper()
    if leg == "STOPLOSS":
        return OrderStatus.SL_HIT.value
    if leg in ("ENTRY", "TARGET"):
        return OrderStatus.TARGET_HIT.value
    # No leg name: decide from whichever level the fill is closer to.
    price = float(update.get("average_price") or 0)
    if gtt.stoploss_price is not None and gtt.target_price is not None and price:
        to_target = abs(price - gtt.target_price)
        to_stop = abs(price - gtt.stoploss_price)
        return OrderStatus.SL_HIT.value if to_stop < to_target else OrderStatus.TARGET_HIT.value
    if gtt.target_price is not None:
        return OrderStatus.TARGET_HIT.value
    return OrderStatus.SL_HIT.value


def first(response: Any, *keys: str) -> Any:
    """The first present value among `keys`, looking inside a `data` envelope."""
    if not isinstance(response, dict):
        return None
    for key in keys:
        value = response.get(key)
        if isinstance(value, list) and value:
            return value[0]
        if value:
            return value
    data = response.get("data")
    return first(data, *keys) if isinstance(data, dict) else None
