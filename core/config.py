"""Loads YAML files from config/ once, with a small cache."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@cache
def load(name: str) -> dict[str, Any]:
    """load("macd") -> dict from config/macd.yaml ({} when the file is absent)."""
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"config/{name}.yaml must contain a mapping")
    return data


def reload() -> None:
    load.cache_clear()


def defaults_for(agent_name: str, folder: Path | None = None) -> dict[str, Any]:
    """Global config/<name>.yaml, overridden by the agent folder's config.yaml."""
    values = dict(load(agent_name))
    if folder:
        local = folder / "config.yaml"
        if local.exists():
            values.update(yaml.safe_load(local.read_text()) or {})
    return values
