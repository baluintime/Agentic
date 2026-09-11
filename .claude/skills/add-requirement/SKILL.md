---
name: add-requirement
description: Add or change a requirement in the docs and roadmap without writing code.
argument-hint: <requirement in plain English>
disable-model-invocation: true
---
New or changed requirement: $ARGUMENTS

1. Find the single affected section by heading in `docs/REQUIREMENTS.md` or `docs/ARCHITECTURE.md`; read only that section.
2. Check it against `docs/DECISIONS.md` for conflicts and pitfalls; point out ambiguities and ask me before editing.
3. Update the section in the same style (prose + tables), add tasks to the right phase in `docs/ROADMAP.md`, and log the decision in `docs/DECISIONS.md`.
4. Do not write code. Commit `docs: …`.
