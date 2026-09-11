"""Base class for strategy agents.

A strategy author writes only `decide(result, pos) -> Action | None`.
Instrument resolution (ATM CE/PE, current-month future, stock), sizing, the
position state machine, order routing through the Risk Agent, the trade log,
charges, P&L, pause/kill/square-off handling and the UI widget live here.
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel, Field

from core import clock
from core.base_agent import BaseAgent
from core.charges import ChargesCalculator
from core.contracts import (
    ORDER_REQUEST_TOPIC,
    Action,
    ExecMode,
    IndicatorResult,
    Instrument,
    OrderEvent,
    OrderRequest,
    OrderStatus,
    Product,
    Segment,
    Side,
    Signal,
    SystemEvent,
    Tick,
    indicator_topic,
    order_event_topic,
    signal_topic,
)
from core.pipeline import PipelineSpec
from core.position import Direction, Position, PositionState
from core.resolver import InstrumentResolver
from core.trade_log import TradeLog, build_row


class StrategyConfig(BaseModel):
    """Standard fields every strategy exposes; subclasses add their own."""

    target_points: float = Field(20, description="Target on the traded instrument")
    stoploss_points: float = Field(10, description="Stop-loss on the traded instrument")
    target_mode: str = Field("points", description="points | percent")
    lots: int = Field(1, ge=1, description="Lots for F&O")
    capital: float = Field(100_000, description="Capital per stock trade")
    on_opposite_signal: str = Field("exit", description="exit | reverse | ignore")
    evaluate_on: str = Field("close", description="close | every_tick")


class StrategyAgent(BaseAgent):
    kind: ClassVar[str] = "strategy"
    Config: ClassVar[type[BaseModel]] = StrategyConfig

    def __init__(
        self,
        agent_id: str,
        config: BaseModel | None = None,
        bus=None,
        store=None,
        pipeline_id: str = "",
        spec: PipelineSpec | None = None,
        resolver: InstrumentResolver | None = None,
        charges: ChargesCalculator | None = None,
    ) -> None:
        pipeline_id = pipeline_id or (spec.pipeline_id if spec else "")
        super().__init__(agent_id, config, bus, store, pipeline_id)
        self.spec = spec
        self.resolver = resolver
        self.charges = charges or ChargesCalculator()
        self.position = Position()
        self.trades = TradeLog()
        self.paused = False
        self.halted = False  # kill switch / risk breach: no new entries at all
        self.new_entries_blocked = False  # square-off cut-off reached
        self.underlying_ltp: float | None = None
        self.last_signal_candle = None
        self.last_reason = ""
        self._pending: dict[str, str] = {}  # correlation_id -> "entry" | "exit"
        self._exit_reason = ""
        self._pending_reverse: tuple[Direction, IndicatorResult] | None = None

    # -- author API ----------------------------------------------------------
    def decide(self, result: IndicatorResult, pos: Position) -> Action | None:
        """The trading rule. Pure: no orders, no sizing, no I/O."""
        raise NotImplementedError

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        await super().start()
        self.listen(indicator_topic(self.pipeline_id), self._on_indicator)
        self.listen(order_event_topic(self.agent_id), self._on_order_event)
        self.listen("tick.*", self._on_tick)
        self.listen("system.*", self._on_system)

    # -- inbound -------------------------------------------------------------
    async def _on_indicator(self, result: IndicatorResult) -> None:
        if result.pipeline_id != self.pipeline_id or not self.running:
            return
        if self.paused or self.position.is_busy:
            return
        if result.candle.start == self.last_signal_candle:
            return  # one decision per candle: ignore duplicate signals
        self.last_signal_candle = result.candle.start
        self.underlying_ltp = result.candle.close
        action = self.decide(result, self.position)
        if action is None:
            return
        await self.emit(
            signal_topic(self.pipeline_id),
            Signal(self.pipeline_id, self.name, action, self.last_reason, clock.now()),
        )
        await self._apply(action, result)

    async def _apply(self, action: Action, result: IndicatorResult) -> None:
        pos = self.position
        if action is Action.EXIT:
            if pos.is_open:
                await self._exit("signal")
            return
        wanted = Direction.LONG if action is Action.ENTER_LONG else Direction.SHORT
        if action is Action.REVERSE:
            wanted = pos.direction.opposite if pos.direction else Direction.LONG
        if pos.is_flat:
            await self._enter(wanted, result)
            return
        if pos.direction is wanted and action is not Action.REVERSE:
            return  # already in that direction
        mode = "reverse" if action is Action.REVERSE else self.cfg("on_opposite_signal", "exit")
        if mode == "ignore":
            return
        if mode == "reverse":
            # Enter the other side only once the exit has actually filled, or the
            # entry would overwrite a position that is still closing.
            self._pending_reverse = (wanted, result)
            await self._exit("reverse")
            return
        await self._exit("signal")

    async def _on_tick(self, tick: Tick) -> None:
        pos = self.position
        if pos.instrument and tick.instrument_key == pos.instrument.instrument_key:
            pos.ltp = tick.ltp
        if self.spec and tick.instrument_key == self.spec.underlying_key:
            self.underlying_ltp = tick.ltp

    async def _on_system(self, event: SystemEvent) -> None:
        kind = event.kind
        if kind == "squareoff.no_new_entries":
            self.new_entries_blocked = True
        elif kind in ("squareoff.start", "kill"):
            reason = "kill_switch" if kind == "kill" else "squareoff"
            self.halted = kind == "kill"
            self.new_entries_blocked = True
            self._pending_reverse = None
            if self.position.is_open and self._squareoff_applies():
                await self._exit(reason)
        elif kind == "risk.block":
            self.new_entries_blocked = True
        elif kind == "session.new_day":
            self.new_entries_blocked = False
            self.halted = False

    def _squareoff_applies(self) -> bool:
        return bool(self.spec and self.spec.intraday)

    # -- orders --------------------------------------------------------------
    async def _enter(self, direction: Direction, result: IndicatorResult) -> None:
        if self.halted or self.new_entries_blocked or self.paused:
            return
        if self.spec is None or self.resolver is None:
            self.fail("no pipeline spec / resolver attached")
            return
        spot = self.underlying_ltp or result.candle.close
        try:
            instrument = self.resolver.resolve(
                self.spec.underlying_key,
                self.spec.segment,
                spot=spot,
                bullish=direction is Direction.LONG,
                expiry_rule=self.spec.expiry_rule,
            )
        except Exception as exc:  # resolution failure must not kill the agent
            self.fail(f"instrument resolution failed: {exc}")
            return
        if self.spec.segment is Segment.STOCK and not self.spec.intraday:
            if direction is Direction.SHORT:
                self.log.info("delivery mode is long-only; short signal ignored")
                return
        side = _entry_side(self.spec.segment, direction)
        price = self._reference_price(instrument, spot)
        quantity = self.size(instrument, price)
        if quantity <= 0:
            self.fail(f"computed quantity {quantity} for {instrument.label}")
            return
        pos = self.position
        pos.state = PositionState.ENTERING
        pos.direction, pos.side = direction, side
        pos.instrument, pos.quantity = instrument, quantity
        pos.underlying_at_entry = spot
        pos.indicator_snapshot = dict(result.values)
        target, stop = self._target_stop(price)
        await self._send(instrument, side, quantity, "entry", target, stop)

    async def _exit(self, reason: str) -> None:
        pos = self.position
        if not pos.is_open or pos.instrument is None or pos.side is None:
            return
        self._exit_reason = reason
        pos.state = PositionState.EXITING
        await self._send(pos.instrument, pos.side.opposite, pos.filled_qty, "exit", None, None)

    async def _send(
        self,
        instrument: Instrument,
        side: Side,
        quantity: int,
        purpose: str,
        target: float | None,
        stop: float | None,
    ) -> None:
        correlation_id = uuid.uuid4().hex[:16]
        self._pending[correlation_id] = purpose
        if purpose == "entry":
            self.position.entry_correlation_id = correlation_id
        else:
            self.position.exit_correlation_id = correlation_id
        request = OrderRequest(
            correlation_id=correlation_id,
            origin_agent_id=self.agent_id,
            pipeline_id=self.pipeline_id,
            instrument_key=instrument.instrument_key,
            side=side,
            quantity=quantity,
            product=self.spec.product.value if self.spec else "I",
            order_type="MARKET",
            target_points=target,
            stoploss_points=stop,
            meta={
                "order_agent": self.spec.effective_order_agent if self.spec else "paper",
                "exec_mode": (self.spec.exec_mode if self.spec else ExecMode.PAPER).value,
                "segment": (self.spec.segment if self.spec else Segment.OPTION).value,
                "purpose": purpose,
                "instrument_label": instrument.label,
                "lot_size": instrument.lot_size,
                "freeze_quantity": instrument.freeze_quantity,
                "strategy": self.name,
                "target_mode": self.cfg("target_mode", "points"),
                "target_value": self.cfg("target_points", 0),
                "stoploss_value": self.cfg("stoploss_points", 0),
            },
        )
        self.log.info(
            "order %s %s %s x%d (%s)",
            purpose,
            side.value,
            instrument.label,
            quantity,
            correlation_id,
        )
        await self.emit(ORDER_REQUEST_TOPIC, request)

    async def _on_order_event(self, event: OrderEvent) -> None:
        """Entry fills, rejections, and the exit — including broker-side GTT legs,
        which report against the *entry* correlation id."""
        purpose = self._pending.get(event.correlation_id)
        if purpose is None:
            return
        pos, status = self.position, event.status
        if event.broker_order_id and event.broker_order_id not in pos.broker_order_ids:
            pos.broker_order_ids.append(event.broker_order_id)
        if status == OrderStatus.PLACED.value:
            return
        if status == OrderStatus.REJECTED.value:
            self._pending.pop(event.correlation_id, None)
            self.fail(f"order rejected: {event.message}")
            self._pending_reverse = None
            if purpose == "entry":
                pos.reset()
            else:
                pos.state = PositionState.OPEN
            return
        if status in (
            OrderStatus.TARGET_HIT.value,
            OrderStatus.SL_HIT.value,
            OrderStatus.SQUARED_OFF.value,
        ):
            reason = {
                OrderStatus.TARGET_HIT.value: "target",
                OrderStatus.SL_HIT.value: "stoploss",
                OrderStatus.SQUARED_OFF.value: "squareoff",
            }[status]
            self._close_trade(event, reason)
            await self._run_pending_reverse()
            return
        if purpose == "entry":
            if status in (OrderStatus.FILLED.value, OrderStatus.PARTIAL.value):
                self._record_entry_fill(event)
            elif status == OrderStatus.CANCELLED.value:
                self._pending.pop(event.correlation_id, None)
                pos.reset()
            return
        if status == OrderStatus.FILLED.value:
            self._close_trade(event, self._exit_reason or "signal")
            await self._run_pending_reverse()
        elif status == OrderStatus.CANCELLED.value:
            self._pending.pop(event.correlation_id, None)
            pos.state = PositionState.OPEN

    async def _run_pending_reverse(self) -> None:
        queued, self._pending_reverse = self._pending_reverse, None
        if queued is not None and self.position.is_flat:
            await self._enter(*queued)

    def _record_entry_fill(self, event: OrderEvent) -> None:
        pos = self.position
        pos.entry_price = event.fill_price
        pos.filled_qty = event.filled_qty or pos.quantity
        pos.entry_time = event.ts
        pos.ltp = event.fill_price
        if event.fill_price is not None:
            target, stop = self._target_stop(event.fill_price)
            sign = 1 if pos.side is Side.BUY else -1
            pos.target_price = event.fill_price + sign * (target or 0)
            pos.stoploss_price = event.fill_price - sign * (stop or 0)
        pos.state = PositionState.OPEN

    def _close_trade(self, event: OrderEvent, reason: str) -> None:
        pos = self.position
        if pos.instrument is None or pos.entry_price is None or pos.side is None:
            pos.reset()
            return
        exit_price = event.fill_price if event.fill_price is not None else (pos.ltp or 0.0)
        row = build_row(
            pipeline_id=self.pipeline_id,
            strategy=self.name,
            position=pos,
            spec=self.spec,
            exit_price=exit_price,
            exit_time=event.ts,
            exit_reason=reason,
            underlying_exit=self.underlying_ltp,
            session_date=clock.now().date(),
        )
        breakup = self.charges.round_trip(
            segment=pos.instrument.segment,
            product=self.spec.product if self.spec else Product.INTRADAY,
            entry_side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=row.quantity,
        )
        row.finalise(breakup.total, breakup.to_dict())
        self.trades.add(row)
        if self.store:
            self.store.record_trade(row)
        self.log.info(
            "closed %s %s net=%.2f (%s)", row.instrument, row.direction, row.net_pnl, reason
        )
        for correlation_id in (pos.entry_correlation_id, pos.exit_correlation_id):
            self._pending.pop(correlation_id or "", None)
        pos.reset()
        self._exit_reason = ""

    # -- sizing --------------------------------------------------------------
    def size(self, instrument: Instrument, price: float) -> int:
        if instrument.segment is Segment.STOCK:
            capital = float(self.cfg("capital", 100_000))
            return int(capital // price) if price > 0 else 0
        return int(self.cfg("lots", 1)) * max(1, instrument.lot_size)

    def _reference_price(self, instrument: Instrument, spot: float) -> float:
        """Best known price of the traded instrument before the fill arrives."""
        if instrument.segment is Segment.STOCK:
            return spot
        return self.position.ltp or spot

    def _target_stop(self, price: float) -> tuple[float | None, float | None]:
        target = float(self.cfg("target_points", 0) or 0)
        stop = float(self.cfg("stoploss_points", 0) or 0)
        if self.cfg("target_mode", "points") == "percent" and price:
            target, stop = price * target / 100, price * stop / 100
        return (target or None, stop or None)

    def cfg(self, key: str, default: Any = None) -> Any:
        return getattr(self.config, key, default)

    # -- control -------------------------------------------------------------
    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    async def stop_trading(self, reason: str = "manual") -> None:
        self.paused = True
        if self.position.is_open:
            await self._exit(reason)

    # -- UI / export ---------------------------------------------------------
    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {
                "pipeline_id": self.pipeline_id,
                "paused": self.paused,
                "halted": self.halted,
                "new_entries_blocked": self.new_entries_blocked,
                "mode": self.spec.exec_mode.value if self.spec else "paper",
                "position": self.position.snapshot(),
                "open_mtm": round(self.position.unrealised(), 2),
                **self.trades.totals(),
            }
        )
        return base

    def export_frames(self) -> dict[str, pd.DataFrame]:
        frame = self.trades.to_frame()
        return {f"trades_{self.pipeline_id}": frame} if not frame.empty else {}


def _entry_side(segment: Segment, direction: Direction) -> Side:
    """Options express both views by BUYing (CE for long, PE for short)."""
    if segment is Segment.OPTION:
        return Side.BUY
    return Side.BUY if direction is Direction.LONG else Side.SELL
