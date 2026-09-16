"""Agent widgets: a card per agent, built from its `status()` dict.

The UI is a client of the bus and the registry. It holds no trading logic and
must never block the event loop, so every card just re-reads `status()`.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from core import clock
from core.base_agent import BaseAgent
from core.strategy_base import StrategyAgent

HIDE = {"agent_id", "name", "per_pipeline", "position", "alerts", "reconciliation", "values"}


def money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"₹{value:,.2f}"


def status_rows(status: dict[str, Any]) -> list[tuple[str, str]]:
    rows = []
    for key, value in status.items():
        if key in HIDE or value is None or value == "":
            continue
        if isinstance(value, float):
            value = f"{value:,.2f}"
        rows.append((key.replace("_", " "), str(value)))
    return rows


class AgentCard:
    """A generic card: title, a key/value table and an Export button."""

    def __init__(self, agent: BaseAgent, on_export=None) -> None:
        self.agent = agent
        self.on_export = on_export
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(agent.agent_id).classes("text-base font-medium")
                ui.badge(agent.kind).props("outline")
            self.body = ui.column().classes("w-full gap-0 text-xs")
            if on_export:
                ui.button("Export", on_click=lambda: on_export(agent)).props("flat dense")
        self.refresh()

    def refresh(self) -> None:
        self.body.clear()
        with self.body:
            for key, value in status_rows(self.agent.status()):
                with ui.row().classes("w-full justify-between"):
                    ui.label(key).classes("text-gray-500")
                    ui.label(value)


class StrategyCard:
    """Trades closed today, the open trade's MTM, P&L and Pause/Stop/Export."""

    def __init__(self, agent: StrategyAgent, on_export=None, on_remove=None) -> None:
        self.agent = agent
        self.on_export = on_export
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(agent.spec.name if agent.spec else agent.agent_id).classes(
                    "text-base font-medium"
                )
                self.mode = ui.badge("paper").props("outline")
            self.price_label = ui.label("").classes("text-sm text-gray-600")
            self.summary = ui.row().classes("w-full gap-6 text-sm")
            self.position = ui.column().classes("w-full gap-0 text-xs")
            self.table = ui.table(
                columns=[
                    {"name": c, "label": c.replace("_", " "), "field": c}
                    for c in (
                        "instrument",
                        "direction",
                        "entry_price",
                        "exit_price",
                        "exit_reason",
                        "net_pnl",
                    )
                ],
                rows=[],
                row_key="instrument",
            ).classes("w-full text-xs")
            with ui.row().classes("gap-2"):
                self.pause_button = ui.button("Pause", on_click=self.toggle_pause).props(
                    "flat dense"
                )
                ui.button("Stop", on_click=self.stop).props("flat dense color=negative")
                if on_export:
                    ui.button("Export", on_click=lambda: on_export(agent)).props("flat dense")
                if on_remove:
                    ui.button("Remove", on_click=lambda: on_remove(agent)).props("flat dense")
        self.refresh()

    def _price_line(self) -> str:
        """What this pipeline is actually watching, and at what price."""
        spec = self.agent.spec
        if spec is None:
            return ""
        spot = self.agent.underlying_ltp
        line = f"{spec.underlying_symbol}: " + (f"{spot:,.2f}" if spot else "waiting for a tick")
        position = self.agent.position
        if position.instrument and position.ltp:
            line += f"   ·   {position.instrument.label}: {position.ltp:,.2f}"
        return line

    def toggle_pause(self) -> None:
        self.agent.resume() if self.agent.paused else self.agent.pause()
        self.refresh()

    async def stop(self) -> None:
        await self.agent.stop_trading("stopped from the console")
        self.refresh()

    def refresh(self) -> None:
        status = self.agent.status()
        self.mode.set_text(status.get("mode", "paper"))
        self.price_label.set_text(self._price_line())
        self.pause_button.set_text("Resume" if self.agent.paused else "Pause")
        self.summary.clear()
        with self.summary:
            for label, value in (
                ("Trades", status.get("trades", 0)),
                ("Wins", status.get("wins", 0)),
                ("Gross", money(status.get("gross"))),
                ("Charges", money(status.get("charges"))),
                ("Net", money(status.get("net"))),
                ("Open MTM", money(status.get("open_mtm"))),
            ):
                with ui.column().classes("gap-0"):
                    ui.label(label).classes("text-gray-500 text-xs")
                    ui.label(str(value)).classes("text-sm")
        self.position.clear()
        with self.position:
            snapshot = status.get("position", {})
            if snapshot.get("state") == "FLAT":
                ui.label("flat").classes("text-gray-500")
            else:
                for key, value in snapshot.items():
                    if value in (None, ""):
                        continue
                    with ui.row().classes("w-full justify-between"):
                        ui.label(key).classes("text-gray-500")
                        ui.label(str(value))
            if status.get("paused"):
                ui.label("paused").classes("text-orange-600")
            if status.get("new_entries_blocked"):
                ui.label("new entries blocked").classes("text-orange-600")
        self.table.rows = [
            {
                "instrument": row.instrument,
                "direction": row.direction,
                "entry_price": row.entry_price,
                "exit_price": row.exit_price,
                "exit_reason": row.exit_reason,
                "net_pnl": row.net_pnl,
            }
            for row in self.agent.trades.rows[-20:]
        ]
        self.table.update()


class PricesCard:
    """Live last-traded prices for everything the feed is subscribed to.

    This is the console's "is it actually working?" panel: if the feed is up,
    prices tick here within a second or two of the market moving.
    """

    def __init__(self, engine) -> None:
        self.engine = engine
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Live prices").classes("text-base font-medium")
                self.feed_badge = ui.badge("").props("outline")
            self.note = ui.label("").classes("text-xs text-gray-500")
            self.table = ui.table(
                columns=[
                    {
                        "name": "instrument",
                        "label": "instrument",
                        "field": "instrument",
                        "align": "left",
                    },
                    {"name": "ltp", "label": "last price", "field": "ltp"},
                    {"name": "age", "label": "updated", "field": "age"},
                ],
                rows=[],
                row_key="instrument",
            ).classes("w-full text-sm")
        self.refresh()

    def refresh(self) -> None:
        hub = self.engine.hub
        connected = bool(hub.connected)
        self.feed_badge.set_text("feed live" if connected else "feed down")
        self.feed_badge.props(f"color={'positive' if connected else 'negative'}")
        self.note.set_text(self._note(hub, connected))
        now = clock.now()
        rows = []
        for key in self._ordered(hub.subscriptions):
            tick = hub.prices.get(key)
            rows.append(
                {
                    "instrument": self.engine.label_for(key),
                    "ltp": f"{tick.ltp:,.2f}" if tick else "—",
                    "age": _age(now, tick.ts) if tick else "no tick yet",
                }
            )
        self.table.rows = rows
        self.table.update()

    def _ordered(self, keys: list[str]) -> list[str]:
        """Pipeline underlyings first: the option strikes around them are detail."""
        underlyings = [p.spec.underlying_key for p in self.engine.pipelines.values()]
        leading = [k for k in underlyings if k in keys]
        return leading + [k for k in keys if k not in leading]

    def _note(self, hub, connected: bool) -> str:
        if hub.fatal_error:
            return hub.fatal_error
        if not hub.subscriptions:
            return "nothing subscribed — add a pipeline"
        if hub.client is None:
            return "no feed attached — log in to Upstox"
        if not connected:
            return f"connecting… ({hub.reconnects} reconnect(s))"
        if not hub.ticks:
            return "connected, waiting for the first tick — the market may be closed"
        return f"{hub.ticks:,} ticks received"


def _age(now, then) -> str:
    seconds = (now - clock.ist(then)).total_seconds()
    if seconds < 2:
        return "just now"
    if seconds < 90:
        return f"{seconds:.0f}s ago"
    return f"{seconds / 60:.0f}m ago"
