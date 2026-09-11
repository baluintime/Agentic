"""Normal Order Agent: market/limit orders with the fill confirmation.

Docs: https://upstox.com/developer/api-documentation/v3/place-order/
Orders above the exchange freeze quantity are split into slices. Nothing is ever
re-placed on a timeout — the order is polled instead, because a timed-out
placement may already have reached the exchange.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from core.contracts import OrderEvent, OrderRequest, OrderStatus
from core.order_base import OrderAgent


class Config(BaseModel):
    validity: str = Field("DAY", description="DAY | IOC")
    poll_attempts: int = Field(20, ge=1, description="Order-detail polls before giving up")
    poll_gap_seconds: float = Field(0.5, gt=0)


class NormalOrderAgent(OrderAgent):
    name = "normal"
    description = "Market/limit order with fill confirmation and freeze-quantity slicing"
    Config = Config
    live_only = True

    def __init__(self, *args: Any, rest=None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rest = rest

    async def place(self, req: OrderRequest) -> OrderEvent:
        if self.rest is None:
            return self.event(req, OrderStatus.REJECTED, message="no broker connection")
        freeze = req.meta.get("freeze_quantity")
        slices = self.slices(req.quantity, int(freeze) if freeze else None)
        filled_qty, notional, order_ids, errors = 0, 0.0, [], []
        for quantity in slices:
            try:
                response = await self.rest.place_order(self.payload(req, quantity))
            except Exception as exc:
                errors.append(str(exc))
                break
            order_id = _order_id(response)
            if not order_id:
                errors.append(f"no order id in response: {response}")
                break
            order_ids.append(order_id)
            details = await self.await_fill(
                self.rest,
                order_id,
                attempts=self.config.poll_attempts,
                gap_seconds=self.config.poll_gap_seconds,
            )
            status = str(details.get("status", "")).lower()
            leg_qty = int(details.get("filled_quantity") or 0)
            price = float(details.get("average_price") or 0)
            filled_qty += leg_qty
            notional += leg_qty * price
            if status in ("rejected", "cancelled", "canceled"):
                errors.append(str(details.get("status_message") or status))
                break
        if filled_qty == 0:
            return self.event(
                req,
                OrderStatus.REJECTED,
                broker_order_id=order_ids[0] if order_ids else None,
                message="; ".join(errors) or "no quantity filled",
            )
        average = round(notional / filled_qty, 2)
        status = OrderStatus.FILLED if filled_qty >= req.quantity else OrderStatus.PARTIAL
        return self.event(
            req,
            status,
            fill_price=average,
            filled_qty=filled_qty,
            broker_order_id=order_ids[0] if order_ids else None,
            message="; ".join(errors),
            meta={"order_ids": order_ids, "slices": slices, "tag": self.tag(req.correlation_id)},
        )

    def payload(self, req: OrderRequest, quantity: int) -> dict[str, Any]:
        return {
            "quantity": quantity,
            "product": req.product,
            "validity": self.config.validity,
            "price": 0 if req.order_type == "MARKET" else float(req.meta.get("limit_price") or 0),
            "tag": self.tag(req.correlation_id),
            "instrument_token": req.instrument_key,
            "order_type": req.order_type,
            "transaction_type": req.side.value,
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
            "slice": False,  # sliced here so every slice keeps its own correlation tag
        }

    async def cancel(self, correlation_id: str) -> OrderEvent | None:
        if self.rest is None:
            return None
        for event in reversed(self.events):
            if event.correlation_id == correlation_id and event.broker_order_id:
                await self.rest.cancel_order(event.broker_order_id)
                return OrderEvent(
                    correlation_id=correlation_id,
                    origin_agent_id=event.origin_agent_id,
                    status=OrderStatus.CANCELLED.value,
                    fill_price=None,
                    filled_qty=0,
                    broker_order_id=event.broker_order_id,
                    ts=event.ts,
                    message="cancelled",
                )
        return None


def _order_id(response: Any) -> str | None:
    if isinstance(response, dict):
        for key in ("order_id", "order_ids"):
            value = response.get(key)
            if isinstance(value, list) and value:
                return str(value[0])
            if value:
                return str(value)
        data = response.get("data")
        if isinstance(data, dict):
            return _order_id(data)
    return None
