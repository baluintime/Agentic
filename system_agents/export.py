"""Export agent: one ZIP with everything, or one workbook per agent.

Every agent implements `export_frames()`. Those frames go into an Excel
workbook (one sheet each); ticks stay out of Excel because a worksheet holds at
most 1,048,576 rows, which a liquid instrument can exceed in a single day — they
are copied in as Parquet instead.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
from pydantic import BaseModel

from core import clock
from core.base_agent import BaseAgent

EXCEL_SHEET_LIMIT = 31
EXCEL_ROW_LIMIT = 1_048_576


class ExportConfig(BaseModel):
    include_ticks: bool = True
    tick_format: str = "parquet"  # parquet | csv


class ExportAgent(BaseAgent):
    kind: ClassVar[str] = "system"
    name: ClassVar[str] = "export"
    description: ClassVar[str] = "Per-agent export and Download All"
    Config: ClassVar[type[BaseModel]] = ExportConfig

    def __init__(
        self,
        *args: Any,
        agents: Callable[[], Iterable[BaseAgent]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.agents = agents or (lambda: [])
        self.last_export: Path | None = None
        self.sheet_count = 0
        self.tick_file_count = 0

    # -- destinations --------------------------------------------------------
    @property
    def out_dir(self) -> Path:
        base = self.store.exports if self.store else Path("exports")
        base.mkdir(parents=True, exist_ok=True)
        return base

    def stamp(self) -> str:
        return clock.now().strftime("%Y%m%d_%H%M%S")

    # -- single agent --------------------------------------------------------
    def why_empty(self, agent: BaseAgent) -> str:
        """Explain an empty export instead of just refusing."""
        status = {}
        try:
            status = agent.status()
        except Exception:
            pass
        if agent.kind == "strategy" and not status.get("trades"):
            return f"{agent.agent_id} has no closed trades yet"
        if agent.kind == "indicator" and not status.get("evaluations"):
            return f"{agent.agent_id} has not evaluated a closed candle yet"
        if agent.kind == "data" and not status.get("bars"):
            return f"{agent.agent_id} has no candles yet"
        return f"{agent.agent_id} has nothing to export yet"

    def export_agent(self, agent: BaseAgent) -> Path | None:
        frames = _safe_frames(agent)
        if not frames:
            return None
        path = self.out_dir / f"{_slug(agent.agent_id)}_{self.stamp()}.xlsx"
        _write_workbook(path, frames)
        self.last_export = path
        return path

    # -- everything ----------------------------------------------------------
    def summary(self) -> pd.DataFrame:
        """Every agent's status, so an export is never an empty file."""
        rows = []
        for agent in self.agents():
            try:
                status = agent.status()
            except Exception:
                status = {"error": "status() failed"}
            for key, value in status.items():
                rows.append({"agent": agent.agent_id, "field": key, "value": str(value)})
        rows.append({"agent": "export", "field": "exported_at", "value": clock.now().isoformat()})
        return pd.DataFrame(rows)

    def export_all(self) -> Path:
        stamp = self.stamp()
        workbook = self.out_dir / f"upstox_agents_{stamp}.xlsx"
        sheets: dict[str, pd.DataFrame] = {"summary": self.summary()}
        for agent in self.agents():
            for title, frame in _safe_frames(agent).items():
                sheets[_sheet_name(f"{agent.agent_id}_{title}", sheets)] = frame
        self.sheet_count = len(sheets) - 1  # the summary does not count as data
        _write_workbook(workbook, sheets)
        archive = self.out_dir / f"upstox_agents_{stamp}.zip"
        tick_files = self._tick_files()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(workbook, workbook.name)
            for tick_file in tick_files:
                zf.write(tick_file, f"ticks/{tick_file.name}")
        self.tick_file_count = len(tick_files)
        self.last_export = archive
        return archive

    def _tick_files(self) -> list[Path]:
        if self.store is None or not getattr(self.config, "include_ticks", True):
            return []
        return sorted((self.store.base / "ticks").glob("*.parquet"))

    def status(self) -> dict[str, Any]:
        base = super().status()
        base.update(
            {
                "exports_dir": str(self.out_dir),
                "last_export": str(self.last_export) if self.last_export else None,
                "agents": len(list(self.agents())),
                "data_sheets": self.sheet_count,
                "tick_files": self.tick_file_count,
            }
        )
        return base


# -- helpers -----------------------------------------------------------------
def _safe_frames(agent: BaseAgent) -> dict[str, pd.DataFrame]:
    try:
        frames = agent.export_frames() or {}
    except Exception:  # one broken agent must not break Download All
        agent.log.exception("export_frames failed")
        return {}
    return {name: frame for name, frame in frames.items() if frame is not None and not frame.empty}


def _write_workbook(path: Path, frames: dict[str, pd.DataFrame]) -> None:
    used: dict[str, pd.DataFrame] = {}
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for title, frame in frames.items():
            name = _sheet_name(title, used)
            used[name] = frame
            _excel_safe(frame.head(EXCEL_ROW_LIMIT - 1)).to_excel(
                writer, sheet_name=name, index=False
            )


def _excel_safe(frame: pd.DataFrame) -> pd.DataFrame:
    """Excel cannot store timezone-aware datetimes."""
    out = frame.copy()
    for column in out.columns:
        if pd.api.types.is_datetime64tz_dtype(out[column]):
            out[column] = out[column].dt.tz_localize(None)
        elif out[column].dtype == object:
            out[column] = out[column].map(
                lambda v: v.replace(tzinfo=None) if hasattr(v, "tzinfo") and v.tzinfo else v
            )
    return out


def _sheet_name(title: str, used: dict[str, Any]) -> str:
    name = _slug(title)[:EXCEL_SHEET_LIMIT]
    if name not in used:
        return name
    for suffix in range(2, 100):
        candidate = f"{name[: EXCEL_SHEET_LIMIT - 3]}_{suffix}"
        if candidate not in used:
            return candidate
    return name


def _slug(value: str) -> str:
    keep = [c if c.isalnum() or c in "-_" else "_" for c in value]
    return "".join(keep).strip("_") or "sheet"
