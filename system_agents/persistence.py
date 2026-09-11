"""Persistence & Workspaces: survive a restart, and reconcile with the broker.

The saved workspace holds only the *pipeline definition*. Options and futures
are re-resolved on load, so yesterday's expired strike never comes back.
On every start, open positions and GTTs are read from Upstox and re-attached to
their pipeline through the order tag (the correlation id).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import BaseModel

from core import clock
from core.base_agent import BaseAgent
from core.pipeline import PipelineSpec

VOLATILE_FIELDS = ("instrument_key", "strike", "expiry")


class PersistenceConfig(BaseModel):
    workspace: str = "default"
    autosave: bool = True


class PersistenceAgent(BaseAgent):
    kind: ClassVar[str] = "system"
    name: ClassVar[str] = "persistence"
    Config: ClassVar[type[BaseModel]] = PersistenceConfig
    description: ClassVar[str] = "Workspace save/load and broker reconciliation"

    def __init__(self, *args: Any, rest=None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.rest = rest
        self.last_saved: datetime | None = None
        self.reconciliation: dict[str, Any] = {}

    # -- workspaces ----------------------------------------------------------
    def workspace_path(self, name: str | None = None) -> Path:
        name = name or getattr(self.config, "workspace", "default")
        base = self.store.workspaces if self.store else Path("workspaces")
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{name}.yaml"

    def save(self, pipelines: list[PipelineSpec], name: str | None = None) -> Path:
        path = self.workspace_path(name)
        payload = {
            "saved_at": clock.now().isoformat(),
            "pipelines": [p.to_dict() for p in pipelines],
        }
        path.write_text(yaml.safe_dump(payload, sort_keys=False))
        self.last_saved = clock.now()
        return path

    def load(self, name: str | None = None) -> list[PipelineSpec]:
        path = self.workspace_path(name)
        if not path.exists():
            return []
        payload = yaml.safe_load(path.read_text()) or {}
        specs = []
        for raw in payload.get("pipelines", []):
            spec = PipelineSpec.from_dict(raw)
            spec.paused = True  # resumed deliberately, never silently mid-session
            specs.append(spec)
        return specs

    def list_workspaces(self) -> list[str]:
        base = self.store.workspaces if self.store else Path("workspaces")
        if not base.exists():
            return []
        return sorted(p.stem for p in base.glob("*.yaml"))

    # -- reconciliation ------------------------------------------------------
    async def reconcile(self) -> dict[str, Any]:
        """Broker is the source of truth: re-attach positions and GTTs by tag."""
        if self.rest is None:
            self.reconciliation = {"checked": False, "reason": "no broker connection"}
            return self.reconciliation
        positions, gtts, errors = [], [], []
        try:
            positions = await self.rest.positions()
        except Exception as exc:
            errors.append(f"positions: {exc}")
        try:
            gtts = await self.rest.gtt_orders()
        except Exception as exc:
            errors.append(f"gtt: {exc}")
        by_pipeline: dict[str, list[dict]] = {}
        for item in list(positions) + list(gtts):
            tag = str(item.get("tag") or item.get("order_tag") or "")
            pipeline = self.pipeline_for_tag(tag)
            by_pipeline.setdefault(pipeline or "unattached", []).append(item)
        self.reconciliation = {
            "checked": True,
            "at": clock.now().isoformat(),
            "open_positions": sum(1 for p in positions if int(p.get("quantity", 0) or 0) != 0),
            "gtts": len(gtts),
            "by_pipeline": {k: len(v) for k, v in by_pipeline.items()},
            "unattached": len(by_pipeline.get("unattached", [])),
            "errors": errors,
        }
        if self.store:
            self.store.put("last_reconciliation", self.reconciliation)
        return self.reconciliation

    def pipeline_for_tag(self, tag: str) -> str | None:
        """The order tag carries the correlation id; SQLite maps it to a pipeline."""
        if not tag or self.store is None:
            return None
        row = self.store.db.execute(
            "SELECT pipeline_id FROM orders WHERE correlation_id LIKE ? AND pipeline_id IS NOT NULL"
            " ORDER BY rowid DESC LIMIT 1",
            (f"{tag}%",),
        ).fetchone()
        return row["pipeline_id"] if row else None

    @property
    def passed(self) -> bool:
        """Live arming requires a reconciliation that actually ran without errors."""
        return bool(self.reconciliation.get("checked")) and not self.reconciliation.get("errors")

    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {
                "workspace": getattr(self.config, "workspace", "default"),
                "last_saved": self.last_saved.isoformat() if self.last_saved else None,
                "workspaces": self.list_workspaces(),
                "reconciliation": self.reconciliation,
            }
        )
        return base
