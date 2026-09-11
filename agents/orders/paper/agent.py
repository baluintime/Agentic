"""Paper Order Agent: the live interface, simulated from the tick stream.

Fills happen at the last traded price plus slippage, and target/stop-loss legs
are watched on live ticks — so a paper pipeline exercises exactly the same code
path as a live one, and the only difference is this adapter.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from broker.paper import PaperBroker, SimulatedLeg
from core.contracts import OrderEvent, OrderRequest, OrderStatus, Side, SystemEvent, Tick
from core.order_base import OrderAgent


class Config(BaseModel):
    slippage_points: float = Field(0.0, ge=0, description="Absolute slippage per fill")
    slippage_percent: float = Field(0.05, ge=0, description="Percent slippage per fill")


class PaperOrderAgent(OrderAgent):
    name = "paper"
    description = "Simulated fills and simulated target/SL legs"
    Config = Config
    simulated = True
    live_only = False

    def __init__(self, *args: Any, broker: PaperBroker | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.broker = broker or PaperBroker(
            slippage_points=getattr(self.config, "slippage_points", 0.0),
            slippage_percent=getattr(self.config, "slippage_percent", 0.0),
        )
        self.legs: dict[str, SimulatedLeg] = {}  # origin_agent_id -> open leg

    async def start(self) -> None:
        await super().start()
        self.listen("tick.*", self._on_tick)
        self.listen("system.squareoff.start", self._on_squareoff)
        self.listen("system.kill", self._on_squareoff)

    # -- placement -----------------------------------------------------------
    async def place(self, req: OrderRequest) -> OrderEvent:
        reference = self.broker.ltp(req.instrument_key)
        if reference is None:
            reference = float(req.meta.get("reference_price") or 0) or None
        if reference is None:
            return self.event(
                req, OrderStatus.REJECTED, message=f"no LTP yet for {req.instrument_key}"
            )
        price = self.broker.fill_price(req.side, reference)
        order_id = self.broker.next_order_id()
        if req.meta.get("purpose") == "exit":
            self.legs.pop(req.origin_agent_id, None)
        else:
            self._arm_leg(req, price)
        return self.event(
            req,
            OrderStatus.FILLED,
            fill_price=price,
            filled_qty=req.quantity,
            broker_order_id=order_id,
            message=f"paper fill at {price}",
            meta={"tag": self.tag(req.correlation_id)},
        )

    def _arm_leg(self, req: OrderRequest, fill_price: float) -> None:
        target, stop = self.resolve_targets(req, fill_price)
        if target is None and stop is None:
            return
        self.legs[req.origin_agent_id] = SimulatedLeg(
            correlation_id=req.correlation_id,
            instrument_key=req.instrument_key,
            side=req.side,
            quantity=req.quantity,
            entry_price=fill_price,
            target_price=target,
            stoploss_price=stop,
        )

    # -- tick-driven legs ----------------------------------------------------
    async def _on_tick(self, tick: Tick) -> None:
        self.broker.on_tick(tick.instrument_key, tick.ltp)
        for origin, leg in list(self.legs.items()):
            if leg.instrument_key != tick.instrument_key:
                continue
            hit = leg.check(tick.ltp)
            if hit is None:
                continue
            status, price = hit
            leg.done = True
            self.legs.pop(origin, None)
            await self.publish_event(
                OrderEvent(
                    correlation_id=leg.correlation_id,
                    origin_agent_id=origin,
                    status=status,
                    fill_price=price,
                    filled_qty=leg.quantity,
                    broker_order_id=self.broker.next_order_id(),
                    ts=tick.ts,
                    message=f"simulated {status.lower()} at {price}",
                )
            )

    async def _on_squareoff(self, event: SystemEvent) -> None:
        """Close every simulated leg at the last known price."""
        for origin, leg in list(self.legs.items()):
            price = self.broker.ltp(leg.instrument_key) or leg.entry_price
            exit_price = self.broker.fill_price(
                Side.SELL if leg.side is Side.BUY else Side.BUY, price
            )
            self.legs.pop(origin, None)
            await self.publish_event(
                OrderEvent(
                    correlation_id=leg.correlation_id,
                    origin_agent_id=origin,
                    status=OrderStatus.SQUARED_OFF.value,
                    fill_price=exit_price,
                    filled_qty=leg.quantity,
                    broker_order_id=self.broker.next_order_id(),
                    ts=event.ts,
                    message=event.message or "square-off",
                )
            )

    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update({"open_legs": len(self.legs), "instruments": len(self.broker.ltp_book)})
        return base
