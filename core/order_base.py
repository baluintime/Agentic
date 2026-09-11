"""Base class for order agents.

An order-agent author writes `place()` (and optionally `cancel()` /
`on_broker_update()`). Routing every event back to `origin_agent_id`, refusing
to trade live unless the pipeline is armed, freeze-quantity slicing and
target/stop resolution are handled here.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

import pandas as pd

from core import clock
from core.base_agent import BaseAgent
from core.contracts import (
    ORDER_APPROVED_TOPIC,
    ExecMode,
    OrderEvent,
    OrderRequest,
    OrderStatus,
    Side,
    order_event_topic,
)

TAG_MAX_LEN = 20  # Upstox order tag limit; correlation ids are 16 hex chars


class OrderAgent(BaseAgent):
    kind: ClassVar[str] = "order"
    live_only: ClassVar[bool] = False  # live agents refuse unless armed LIVE
    simulated: ClassVar[bool] = False

    def __init__(self, *args: Any, armed_live: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.armed_live = armed_live
        self.handled = 0
        self.events: list[OrderEvent] = []

    # -- author API ----------------------------------------------------------
    async def place(self, req: OrderRequest) -> OrderEvent:
        raise NotImplementedError

    async def cancel(self, correlation_id: str) -> OrderEvent | None:
        return None

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        await super().start()
        self.listen(ORDER_APPROVED_TOPIC, self._on_approved)

    async def _on_approved(self, req: OrderRequest) -> None:
        if req.meta.get("order_agent") != self.name:
            return
        self.handled += 1
        guard = self.guard(req)
        if guard is not None:
            await self.publish_event(guard)
            return
        try:
            event = await self.place(req)
        except Exception as exc:  # never let a broker error kill the agent
            self.fail(f"place failed: {exc}")
            event = self.event(req, OrderStatus.REJECTED, message=str(exc))
        if event is not None:
            await self.publish_event(event)

    # -- guards --------------------------------------------------------------
    def guard(self, req: OrderRequest) -> OrderEvent | None:
        """Refuse the order, or return None to let it through."""
        live = req.meta.get("exec_mode") == ExecMode.LIVE.value
        if self.live_only and not (live and self.armed_live):
            return self.event(
                req, OrderStatus.REJECTED, message="live order agent is not armed for LIVE"
            )
        if self.simulated and live:
            return self.event(req, OrderStatus.REJECTED, message="paper agent refuses LIVE orders")
        if req.quantity <= 0:
            return self.event(req, OrderStatus.REJECTED, message="quantity must be positive")
        return None

    # -- helpers -------------------------------------------------------------
    def event(
        self,
        req: OrderRequest,
        status: OrderStatus,
        *,
        fill_price: float | None = None,
        filled_qty: int = 0,
        broker_order_id: str | None = None,
        message: str = "",
        meta: dict[str, Any] | None = None,
    ) -> OrderEvent:
        return OrderEvent(
            correlation_id=req.correlation_id,
            origin_agent_id=req.origin_agent_id,
            status=status.value,
            fill_price=fill_price,
            filled_qty=filled_qty,
            broker_order_id=broker_order_id,
            ts=clock.now(),
            message=message,
            meta=meta or {},
        )

    async def publish_event(self, event: OrderEvent) -> None:
        self.events.append(event)
        if self.store:
            self.store.record_order(event)
        await self.emit(order_event_topic(event.origin_agent_id), event)

    @staticmethod
    def tag(correlation_id: str) -> str:
        """Written into the broker order tag so positions re-attach after a restart."""
        return correlation_id[:TAG_MAX_LEN]

    @staticmethod
    def slices(quantity: int, freeze_quantity: int | None) -> list[int]:
        """Split an order above the exchange freeze quantity into legal slices."""
        if not freeze_quantity or freeze_quantity <= 0 or quantity <= freeze_quantity:
            return [quantity]
        count = math.ceil(quantity / freeze_quantity)
        base = [freeze_quantity] * (count - 1)
        return base + [quantity - freeze_quantity * (count - 1)]

    @staticmethod
    def resolve_targets(req: OrderRequest, fill_price: float) -> tuple[float | None, float | None]:
        """Absolute target and stop-loss prices from the request and the actual fill.

        Percent mode is applied to the fill price, which is only known here.
        """
        mode = req.meta.get("target_mode", "points")
        if mode == "percent":
            target_pts = fill_price * float(req.meta.get("target_value") or 0) / 100
            stop_pts = fill_price * float(req.meta.get("stoploss_value") or 0) / 100
        else:
            target_pts = req.target_points or 0.0
            stop_pts = req.stoploss_points or 0.0
        sign = 1 if req.side is Side.BUY else -1
        target = fill_price + sign * target_pts if target_pts else None
        stop = fill_price - sign * stop_pts if stop_pts else None
        return target, stop

    # -- UI / export ---------------------------------------------------------
    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {"handled": self.handled, "events": len(self.events), "armed_live": self.armed_live}
        )
        return base

    def export_frames(self) -> dict[str, pd.DataFrame]:
        if not self.events:
            return {}
        rows = [
            {
                "correlation_id": e.correlation_id,
                "origin_agent_id": e.origin_agent_id,
                "status": e.status,
                "fill_price": e.fill_price,
                "filled_qty": e.filled_qty,
                "broker_order_id": e.broker_order_id,
                "ts": e.ts,
                "message": e.message,
            }
            for e in self.events
        ]
        return {f"orders_{self.name}": pd.DataFrame(rows)}
