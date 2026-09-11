from __future__ import annotations

import shutil

import pytest

from tools.new_agent import ROOT, camel, create


@pytest.fixture
def sandbox(tmp_path):
    for kind in ("indicators", "strategies", "orders"):
        shutil.copytree(
            ROOT / "agents" / kind / "_template", tmp_path / "agents" / kind / "_template"
        )
    return tmp_path


def test_camel() -> None:
    assert camel("macd_s4_histogram") == "MacdS4Histogram"


@pytest.mark.parametrize(
    "kind,folder,cls",
    [
        ("indicator", "indicators", "SupertrendAgent"),
        ("strategy", "strategies", "MacdS4"),
        ("order", "orders", "Bracket"),
    ],
)
def test_creates_a_renamed_folder(sandbox, kind, folder, cls) -> None:
    name = {"indicator": "supertrend", "strategy": "macd_s4", "order": "bracket"}[kind]
    created = create(kind, name, root=sandbox)
    assert created == sandbox / "agents" / folder / name
    agent = (created / "agent.py").read_text()
    assert f"class {cls}" in agent
    assert f'name = "{name}"' in agent
    assert "_template" not in (created / "manifest.yaml").read_text()


def test_rejects_a_bad_name(sandbox) -> None:
    with pytest.raises(SystemExit):
        create("indicator", "Bad-Name", root=sandbox)


def test_rejects_an_existing_folder(sandbox) -> None:
    create("indicator", "supertrend", root=sandbox)
    with pytest.raises(SystemExit):
        create("indicator", "supertrend", root=sandbox)


def test_rejects_an_unknown_kind(sandbox) -> None:
    with pytest.raises(SystemExit):
        create("widget", "thing", root=sandbox)
