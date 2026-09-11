"""Risk Agent: the only path from a strategy's OrderRequest to an order agent.

Every request is approved or rejected here. A daily-loss breach squares off
everything and blocks new entries; the console's kill switch does the same
immediately.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from core import clock
from core.base_agent import BaseAgent
from core.contracts import (
    ORDER_APPROVED_TOPIC,
    ORDER_REQUEST_TOPIC,
    OrderEvent,
    OrderRequest,
    OrderStatus,
    Segment,
    SystemEvent,
    order_event_topic,
    system_topic,
)


class RiskConfig(BaseModel):
    max_daily_loss_total: float = 5000
    max_daily_loss_per_pipeline: float = 2000
    max_trades_per_day_per_pipeline: int = 10
    max_open_positions: int = 5
    max_lots_per_order: int = 5
    max_capital_per_stock_order: float = 100_000
    max_orders_per_second: float = Field(5, description="stay under the broker/SEBI limit")
    live_requires_confirmation: bool = True
    trading_start: str = "09:15"
    trading_end: str = "15:30"
    duplicate_window_seconds: float = 2.0
    enabled: bool = True


class RiskAgent(BaseAgent):
    kind: ClassVar[str] = "system"
    name: ClassVar[str] = "risk"
    Config: ClassVar[type[BaseModel]] = RiskConfig
    description: ClassVar[str] = "Pre-trade checks, daily loss limits and the kill switch"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.blocked = False
        self.block_reason = ""
        self.killed = False
        self.open_positions: dict[str, int] = defaultdict(int)
        self.entries_today: dict[str, int] = defaultdict(int)
        self.approved = 0
        self.rejected = 0
        self.rejections: list[str] = []
        self._recent: list[float] = []
        self._last_request: dict[tuple[str, str, str], datetime] = {}

    async def start(self) -> None:
        await super().start()
        self.listen(ORDER_REQUEST_TOPIC, self.on_request)
        self.listen("system.session.new_day", self._on_new_day)

    # -- gate ----------------------------------------------------------------
    async def on_request(self, req: OrderRequest) -> None:
        reason = self.check(req)
        if reason:
            self.rejected += 1
            self.rejections.append(reason)
            self.log.warning("rejected %s: %s", req.correlation_id, reason)
            await self.emit(
                order_event_topic(req.origin_agent_id),
                OrderEvent(
                    correlation_id=req.correlation_id,
                    origin_agent_id=req.origin_agent_id,
                    status=OrderStatus.REJECTED.value,
                    fill_price=None,
                    filled_qty=0,
                    broker_order_id=None,
                    ts=clock.now(),
                    message=reason,
                ),
            )
            return
        self.approved += 1
        self._record(req)
        await self.emit(ORDER_APPROVED_TOPIC, req)

    def check(self, req: OrderRequest) -> str | None:
        """Returns a rejection reason, or None when the order may proceed."""
        cfg: RiskConfig = self.config  # type: ignore[assignment]
        if not cfg.enabled:
            return "risk agent is disabled"
        purpose = req.meta.get("purpose", "entry")
        if self.killed:
            return "kill switch is active"
        if self.blocked and purpose == "entry":
            return self.block_reason or "new entries are blocked"
        if not self._within_hours(cfg) and purpose == "entry":
            return "outside trading hours"
        if self._rate_exceeded(cfg):
            return f"order rate above {cfg.max_orders_per_second}/s"
        if self._is_duplicate(req, cfg):
            return "duplicate order suppressed"
        if purpose == "entry":
            if sum(self.open_positions.values()) >= cfg.max_open_positions:
                return f"max open positions ({cfg.max_open_positions}) reached"
            if self.entries_today[req.pipeline_id] >= cfg.max_trades_per_day_per_pipeline:
                return f"max trades/day ({cfg.max_trades_per_day_per_pipeline}) for this pipeline"
            size_error = self._size_error(req, cfg)
            if size_error:
                return size_error
            loss_error = self._loss_error(req, cfg)
            if loss_error:
                return loss_error
        return None

    def _within_hours(self, cfg: RiskConfig) -> bool:
        now = clock.now().time()
        return clock.parse_time(cfg.trading_start) <= now <= clock.parse_time(cfg.trading_end)

    def _rate_exceeded(self, cfg: RiskConfig) -> bool:
        import time as _time

        now = _time.monotonic()
        self._recent = [t for t in self._recent if now - t < 1.0]
        if len(self._recent) >= cfg.max_orders_per_second:
            return True
        self._recent.append(now)
        return False

    def _is_duplicate(self, req: OrderRequest, cfg: RiskConfig) -> bool:
        key = (req.pipeline_id, req.instrument_key, req.side.value)
        last = self._last_request.get(key)
        now = clock.now()
        self._last_request[key] = now
        return last is not None and (now - last).total_seconds() < cfg.duplicate_window_seconds

    def _size_error(self, req: OrderRequest, cfg: RiskConfig) -> str | None:
        segment = req.meta.get("segment")
        if segment == Segment.STOCK.value:
            price = float(req.meta.get("reference_price") or 0)
            if price and price * req.quantity > cfg.max_capital_per_stock_order:
                return f"order value above {cfg.max_capital_per_stock_order:,.0f}"
            return None
        lot_size = int(req.meta.get("lot_size") or 1)
        lots = req.quantity / max(1, lot_size)
        if lots > cfg.max_lots_per_order:
            return f"{lots:g} lots above the limit of {cfg.max_lots_per_order}"
        return None

    def _loss_error(self, req: OrderRequest, cfg: RiskConfig) -> str | None:
        total, per_pipeline = self.realised_pnl()
        if total <= -abs(cfg.max_daily_loss_total):
            return f"daily loss limit hit (net {total:,.0f})"
        if per_pipeline.get(req.pipeline_id, 0.0) <= -abs(cfg.max_daily_loss_per_pipeline):
            return f"pipeline daily loss limit hit ({per_pipeline[req.pipeline_id]:,.0f})"
        return None

    def realised_pnl(self) -> tuple[float, dict[str, float]]:
        if self.store is None:
            return 0.0, {}
        per_pipeline: dict[str, float] = defaultdict(float)
        total = 0.0
        for trade in self.store.trades(clock.now().date()):
            net = float(trade.get("net_pnl") or 0)
            total += net
            per_pipeline[trade.get("pipeline_id", "")] += net
        return round(total, 2), dict(per_pipeline)

    def _record(self, req: OrderRequest) -> None:
        if req.meta.get("purpose") == "entry":
            self.entries_today[req.pipeline_id] += 1
            self.open_positions[req.pipeline_id] += 1
        else:
            self.open_positions[req.pipeline_id] = max(0, self.open_positions[req.pipeline_id] - 1)

    async def _on_new_day(self, _: SystemEvent) -> None:
        self.entries_today.clear()
        self.open_positions.clear()
        self.blocked = False
        self.block_reason = ""

    # -- controls ------------------------------------------------------------
    async def block(self, reason: str) -> None:
        self.blocked, self.block_reason = True, reason
        await self.emit(system_topic("risk.block"), _event("risk.block", reason))

    async def unblock(self) -> None:
        self.blocked, self.block_reason = False, ""

    async def kill(self, reason: str = "kill switch") -> None:
        """Cancel everything, square off everything, stop all strategies."""
        self.killed = True
        self.blocked, self.block_reason = True, reason
        self.log.error("KILL SWITCH: %s", reason)
        await self.emit(system_topic("kill"), _event("kill", reason))

    async def reset_kill(self) -> None:
        self.killed = False
        self.blocked, self.block_reason = False, ""

    async def enforce_daily_loss(self) -> None:
        """Called periodically: a breach squares off everything."""
        cfg: RiskConfig = self.config  # type: ignore[assignment]
        total, _ = self.realised_pnl()
        if total <= -abs(cfg.max_daily_loss_total) and not self.blocked:
            await self.block(f"daily loss limit hit (net {total:,.0f})")
            await self.emit(
                system_topic("squareoff.start"),
                _event("squareoff.start", "daily loss limit"),
            )

    def status(self) -> dict[str, Any]:
        total, per_pipeline = self.realised_pnl()
        base = super().status()
        base.update(
            {
                "enabled": getattr(self.config, "enabled", True),
                "blocked": self.blocked,
                "block_reason": self.block_reason,
                "killed": self.killed,
                "approved": self.approved,
                "rejected": self.rejected,
                "open_positions": sum(self.open_positions.values()),
                "net_today": total,
                "per_pipeline": per_pipeline,
                "last_rejection": self.rejections[-1] if self.rejections else "",
            }
        )
        return base


def _event(kind: str, message: str) -> SystemEvent:
    return SystemEvent(kind=kind, ts=clock.now(), message=message)
