"""Agent widgets: a card per agent, built from its `status()` dict.

The UI is a client of the bus and the registry. It holds no trading logic and
must never block the event loop, so every card just re-reads `status()`.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

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

    def toggle_pause(self) -> None:
        self.agent.resume() if self.agent.paused else self.agent.pause()
        self.refresh()

    async def stop(self) -> None:
        await self.agent.stop_trading("stopped from the console")
        self.refresh()

    def refresh(self) -> None:
        status = self.agent.status()
        self.mode.set_text(status.get("mode", "paper"))
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
