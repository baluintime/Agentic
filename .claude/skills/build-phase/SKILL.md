---
name: build-phase
description: Build one roadmap phase of the Upstox agents platform.
argument-hint: <phase-number> [optional focus or task]
disable-model-invocation: true
---
Build Phase $ARGUMENTS from `docs/ROADMAP.md`.

1. Read `docs/ROADMAP.md` and only the unticked tasks of this phase.
2. For each task, read only the docs sections it needs (use headings in `docs/REQUIREMENTS.md`, `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md`) and the `CLAUDE.md` of the folder you will edit. Check `docs/DECISIONS.md` → "Issues found".
3. Present a short plan (files to create/modify, tests, open questions) and wait for my approval.
4. Create branch `phase/<n>-<slug>`. Implement task by task; after each task run its tests and `ruff check .`, then commit with a conventional message and tick the box in `docs/ROADMAP.md`.
5. Anything touching Upstox endpoints: verify against current official Upstox docs first.
6. Finish by verifying the phase exit criterion, listing anything deferred, and recording decisions in `docs/DECISIONS.md`.

Do not start the next phase.
