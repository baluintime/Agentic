"""GTT Order Agent: market entry plus broker-side target and stop-loss legs.

Docs: https://upstox.com/developer/api-documentation/place-gtt-order/
The strategy sends target and stop-loss as points. This agent enters at market,
reads the *actual* fill price, and only then computes absolute target and
stop-loss levels, because points are meaningless until the fill is known.

Leg support differs between brokers and changes over time: verify the supported
rule combinations against the current Upstox GTT documentation. If a combined
entry+target+SL trigger is unavailable, this agent's shape is already the
fallback the docs describe — market entry followed by a two-leg GTT.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agents.orders.gtt.legs import OpenGtt, entry_payload, first, gtt_payload, leg_status
from core import clock
from core.contracts import OrderEvent, OrderRequest, OrderStatus
from core.order_base import OrderAgent


class Config(BaseModel):
    validity: str = Field("DAY", description="DAY | IOC for the entry leg")
    poll_attempts: int = Field(20, ge=1)
    poll_gap_seconds: float = Field(0.5, gt=0)
    cancel_sibling_leg: bool = Field(
        True, description="Cancel the other leg when one triggers, if the broker does not"
    )


class GttOrderAgent(OrderAgent):
    name = "gtt"
    description = "Market entry with broker-side target and stop-loss legs"
    Config = Config
    live_only = True

    def __init__(self, *args: Any, rest=None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rest = rest
        self.open_gtts: dict[str, OpenGtt] = {}  # correlation_id -> live GTT

    async def start(self) -> None:
        await super().start()
        self.listen("system.squareoff.cancel_gtt", self._on_cancel_all)
        self.listen("system.kill", self._on_cancel_all)

    # -- placement -----------------------------------------------------------
    async def place(self, req: OrderRequest) -> OrderEvent:
        if self.rest is None:
            return self.event(req, OrderStatus.REJECTED, message="no broker connection")
        entry = await self._market(req)
        if entry.status != OrderStatus.FILLED.value or entry.fill_price is None:
            return entry
        if req.meta.get("purpose") == "exit":
            await self._cancel_for_origin(req.origin_agent_id)
            return entry
        target, stop = self.resolve_targets(req, entry.fill_price)
        if target is None and stop is None:
            return entry
        try:
            response = await self.rest.place_gtt(
                self.gtt_payload(req, entry.filled_qty, target, stop)
            )
        except Exception as exc:
            self.fail(f"GTT legs not placed: {exc}")
            return self.event(
                req,
                OrderStatus.FILLED,
                fill_price=entry.fill_price,
                filled_qty=entry.filled_qty,
                broker_order_id=entry.broker_order_id,
                message=f"entry filled but GTT legs failed: {exc}",
            )
        gtt_id = str(first(response, "gtt_order_id", "order_id") or "")
        self.open_gtts[req.correlation_id] = OpenGtt(
            correlation_id=req.correlation_id,
            origin_agent_id=req.origin_agent_id,
            gtt_order_id=gtt_id,
            instrument_key=req.instrument_key,
            side=req.side,
            quantity=entry.filled_qty,
            entry_price=entry.fill_price,
            target_price=target,
            stoploss_price=stop,
        )
        return self.event(
            req,
            OrderStatus.FILLED,
            fill_price=entry.fill_price,
            filled_qty=entry.filled_qty,
            broker_order_id=entry.broker_order_id,
            message=f"entry filled; GTT target {target} / SL {stop}",
            meta={"gtt_order_id": gtt_id, "target": target, "stoploss": stop},
        )

    async def _market(self, req: OrderRequest) -> OrderEvent:
        freeze = req.meta.get("freeze_quantity")
        slices = self.slices(req.quantity, int(freeze) if freeze else None)
        filled, notional, order_ids, errors = 0, 0.0, [], []
        for quantity in slices:
            try:
                response = await self.rest.place_order(self.entry_payload(req, quantity))
            except Exception as exc:
                errors.append(str(exc))
                break
            order_id = str(first(response, "order_id") or "")
            if not order_id:
                errors.append("no order id returned")
                break
            order_ids.append(order_id)
            details = await self.await_fill(
                self.rest,
                order_id,
                attempts=self.config.poll_attempts,
                gap_seconds=self.config.poll_gap_seconds,
            )
            leg_qty = int(details.get("filled_quantity") or 0)
            filled += leg_qty
            notional += leg_qty * float(details.get("average_price") or 0)
            if str(details.get("status", "")).lower() in ("rejected", "cancelled", "canceled"):
                errors.append(str(details.get("status_message") or details.get("status")))
                break
        if filled == 0:
            return self.event(
                req,
                OrderStatus.REJECTED,
                broker_order_id=order_ids[0] if order_ids else None,
                message="; ".join(errors) or "entry not filled",
            )
        return self.event(
            req,
            OrderStatus.FILLED,
            fill_price=round(notional / filled, 2),
            filled_qty=filled,
            broker_order_id=order_ids[0] if order_ids else None,
            message="; ".join(errors),
        )

    # -- payloads ------------------------------------------------------------
    def entry_payload(self, req: OrderRequest, quantity: int) -> dict[str, Any]:
        return entry_payload(req, quantity, self.tag(req.correlation_id), self.config.validity)

    def gtt_payload(
        self, req: OrderRequest, quantity: int, target: float | None, stop: float | None
    ) -> dict[str, Any]:
        return gtt_payload(req, quantity, target, stop, self.tag(req.correlation_id))

    # -- broker updates ------------------------------------------------------
    async def on_broker_update(self, update: dict[str, Any]) -> None:
        """Map an order-update message onto TARGET_HIT / SL_HIT / CANCELLED."""
        tag = str(update.get("tag") or "")
        gtt = next((g for g in self.open_gtts.values() if self.tag(g.correlation_id) == tag), None)
        if gtt is None:
            return
        status = leg_status(update, gtt)
        if status is None:
            return
        self.open_gtts.pop(gtt.correlation_id, None)
        if self.config.cancel_sibling_leg and gtt.gtt_order_id:
            try:
                await self.rest.cancel_gtt(gtt.gtt_order_id)
            except Exception as exc:
                self.log.info("sibling leg already gone: %s", exc)
        price = float(update.get("average_price") or 0) or gtt.target_price or gtt.entry_price
        await self.publish_event(
            OrderEvent(
                correlation_id=gtt.correlation_id,
                origin_agent_id=gtt.origin_agent_id,
                status=status,
                fill_price=price,
                filled_qty=int(update.get("filled_quantity") or gtt.quantity),
                broker_order_id=str(update.get("order_id") or gtt.gtt_order_id),
                ts=clock.now(),
                message=f"GTT {status.lower()}",
            )
        )

    async def _cancel_for_origin(self, origin_agent_id: str) -> None:
        await self._cancel(lambda gtt: gtt.origin_agent_id == origin_agent_id)

    async def _on_cancel_all(self, _event: Any) -> None:
        """Square-off and the kill switch cancel open GTTs before positions are exited."""
        await self._cancel(lambda _gtt: True)

    async def _cancel(self, matches) -> None:
        for correlation_id, gtt in list(self.open_gtts.items()):
            if not matches(gtt):
                continue
            self.open_gtts.pop(correlation_id, None)
            if self.rest and gtt.gtt_order_id:
                try:
                    await self.rest.cancel_gtt(gtt.gtt_order_id)
                except Exception as exc:
                    self.log.info("GTT cancel failed: %s", exc)

    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update({"open_gtts": len(self.open_gtts)})
        return base
