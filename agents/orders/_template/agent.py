"""One-line description of the order agent."""

from __future__ import annotations

from core.contracts import OrderEvent, OrderRequest, OrderStatus
from core.order_base import OrderAgent


class TemplateOrderAgent(OrderAgent):
    name = "_template"
    description = "Describe the execution behaviour in one line"
    live_only = False

    async def place(self, req: OrderRequest) -> OrderEvent:
        """Send to the broker adapter and return the terminal event for this leg.

        The base class routes it to `req.origin_agent_id`.
        """
        return self.event(req, OrderStatus.REJECTED, message="not implemented")
