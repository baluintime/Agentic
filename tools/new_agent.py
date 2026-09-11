"""Scaffold a new agent folder from the matching template.

    python -m tools.new_agent <indicator|strategy|order> <snake_case_name>

Copies `agents/<kind>s/_template/` to `agents/<kind>s/<name>/`, renames the
class and fills in the manifest. Then edit only that folder.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KINDS = {"indicator": "indicators", "strategy": "strategies", "order": "orders"}
TEMPLATE_CLASS = {
    "indicator": "TemplateIndicator",
    "strategy": "TemplateStrategy",
    "order": "TemplateOrderAgent",
}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def create(kind: str, name: str, root: Path = ROOT) -> Path:
    if kind not in KINDS:
        raise SystemExit(f"kind must be one of {', '.join(KINDS)}")
    if not NAME_RE.match(name):
        raise SystemExit(f"{name!r} must be snake_case: lower case, digits and underscores")
    folder = root / "agents" / KINDS[kind] / name
    if folder.exists():
        raise SystemExit(f"{folder.relative_to(root)} already exists")
    template = root / "agents" / KINDS[kind] / "_template"
    shutil.copytree(template, folder)
    class_name = camel(name) + ("Agent" if kind == "indicator" else "")
    replacements = {
        TEMPLATE_CLASS[kind]: class_name,
        f"agents.{KINDS[kind]}._template.agent": f"agents.{KINDS[kind]}.{name}.agent",
        '"_template"': f'"{name}"',
        "name: _template": f"name: {name}",
        "# _template": f"# {name}",
    }
    for path in sorted(folder.rglob("*")):
        if path.suffix not in (".py", ".yaml", ".md") or not path.is_file():
            continue
        text = path.read_text()
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text)
    return folder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.new_agent")
    parser.add_argument("kind", choices=sorted(KINDS))
    parser.add_argument("name", help="snake_case agent name, e.g. supertrend")
    args = parser.parse_args(argv)
    folder = create(args.kind, args.name)
    rel = folder.relative_to(ROOT)
    print(f"created {rel}")
    print(f"  edit   {rel}/agent.py   ({args.kind} logic only)")
    print(f"  tests  pytest {rel} -q")
    print(f"  lint   ruff check {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
