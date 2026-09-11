---
name: new-strategy
description: Scaffold and implement a new strategy agent from a plain-English rule.
argument-hint: <name> <indicator> <entry and exit rules>
disable-model-invocation: true
---
Create a strategy agent. Request: $ARGUMENTS

Read only: `agents/strategies/CLAUDE.md`, `core/contracts.py`, `core/strategy_base.py`, `agents/strategies/_template/`, and the `outputs` + `README.md` of the required indicator. Do not read other strategies unless I name one as an example.

1. Restate the rule as an unambiguous entry/exit table using the definitions in `agents/strategies/CLAUDE.md`. If anything is ambiguous (opposite signal, evaluate on close vs tick, segment, target/SL basis), ask me before coding.
2. Run `python -m tools.new_agent strategy <name>`.
3. Implement `Config`, `requires`, and `decide()` only.
4. Table-driven tests for every rule, duplicate-signal suppression, and the opposite-signal setting.
5. `README.md` (≤ 20 lines) with the rule table; `manifest.yaml`.
6. Tests + ruff pass; commit on branch `agent/<name>`; add a line to `docs/DECISIONS.md` decision log if you made a choice.
