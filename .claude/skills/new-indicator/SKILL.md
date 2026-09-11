---
name: new-indicator
description: Scaffold and implement a new indicator agent.
argument-hint: <name> <formula / parameters description>
disable-model-invocation: true
---
Create an indicator agent. Request: $ARGUMENTS

Read only: `agents/indicators/CLAUDE.md`, `core/contracts.py`, `core/indicator_base.py`, `agents/indicators/_template/`. Do not read other agents or docs unless blocked.

1. Run `python -m tools.new_agent indicator <name>` (snake_case).
2. Implement `Config`, `outputs`, `warmup_bars()`, pure `compute(df)`. Add defaults to `config/<name>.yaml`.
3. Write `test_agent.py`: known values on fixture candles, warm-up NaNs, no look-ahead.
4. Fill `README.md` (≤ 20 lines: formula, outputs, parameters, warm-up) and `manifest.yaml`.
5. `pytest agents/indicators/<name> -q` and `ruff check agents/indicators/<name>` must pass.
6. Commit on branch `agent/<name>`. Summarise outputs so strategies can use them.
