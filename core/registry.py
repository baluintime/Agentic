"""Agent discovery: scans agents/*/*/manifest.yaml and imports agent.py.

Dropping a folder in is enough to make an agent appear in the UI.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.base_agent import BaseAgent

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
KINDS = ("indicators", "strategies", "orders")


@dataclass
class AgentSpec:
    name: str
    kind: str  # indicator | strategy | order
    version: str
    description: str
    requires: list[str]
    folder: Path
    cls: type[BaseAgent]
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_indicator(self) -> str | None:
        for item in self.requires:
            if ":" not in item:
                return item
        return None

    @property
    def needs_segment(self) -> str | None:
        for item in self.requires:
            if item.startswith("segment:"):
                return item.split(":", 1)[1]
        return None

    def config_defaults(self) -> dict[str, Any]:
        from core.config import defaults_for

        allowed = set(self.cls.Config.model_fields)
        return {k: v for k, v in defaults_for(self.name, self.folder).items() if k in allowed}

    def build_config(self, overrides: dict[str, Any] | None = None):
        values = self.config_defaults()
        values.update({k: v for k, v in (overrides or {}).items() if v is not None})
        return self.cls.Config(**values)


class Registry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or AGENTS_DIR
        self._specs: dict[tuple[str, str], AgentSpec] = {}

    def discover(self) -> Registry:
        self._specs.clear()
        for kind_dir in KINDS:
            base = self.root / kind_dir
            if not base.exists():
                continue
            for manifest in sorted(base.glob("*/manifest.yaml")):
                if manifest.parent.name.startswith("_"):
                    continue
                spec = self._load(manifest)
                if spec:
                    self._specs[(spec.kind, spec.name)] = spec
        return self

    def _load(self, manifest_path: Path) -> AgentSpec | None:
        data = yaml.safe_load(manifest_path.read_text()) or {}
        folder = manifest_path.parent
        module_path = folder / "agent.py"
        if not module_path.exists():
            return None
        cls = _load_agent_class(module_path, folder.name)
        if cls is None:
            return None
        return AgentSpec(
            name=data.get("name", cls.name),
            kind=data.get("kind", cls.kind),
            version=str(data.get("version", "0.1.0")),
            description=data.get("description", cls.description),
            requires=list(data.get("requires", cls.requires)),
            folder=folder,
            cls=cls,
            manifest=data,
        )

    # -- lookups -------------------------------------------------------------
    def all(self) -> list[AgentSpec]:
        return sorted(self._specs.values(), key=lambda s: (s.kind, s.name))

    def of_kind(self, kind: str) -> list[AgentSpec]:
        return [s for s in self.all() if s.kind == kind]

    def get(self, kind: str, name: str) -> AgentSpec:
        try:
            return self._specs[(kind, name)]
        except KeyError as exc:
            raise KeyError(f"no {kind} agent named {name!r}") from exc

    def strategies_for(self, indicator: str, segment: str | None = None) -> list[AgentSpec]:
        """Only the strategies the pipeline builder may offer for this choice."""
        out = []
        for spec in self.of_kind("strategy"):
            if spec.needs_indicator and spec.needs_indicator != indicator:
                continue
            if segment and spec.needs_segment and spec.needs_segment != segment:
                continue
            out.append(spec)
        return out


def _load_agent_class(module_path: Path, unique: str) -> type[BaseAgent] | None:
    module_name = f"agents_dyn.{unique}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: dataclasses and pydantic resolve types via sys.modules.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, BaseAgent) and obj.__module__ == module.__name__:
            if getattr(obj, "name", "base") != "base":
                return obj
    return None


_registry: Registry | None = None


def get_registry() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry().discover()
    return _registry
