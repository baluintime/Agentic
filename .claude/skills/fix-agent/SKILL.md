---
name: fix-agent
description: Diagnose and fix a bug in one agent with minimal context.
argument-hint: <agent path, e.g. agents/strategies/macd_s1_cross_option> <problem description>
disable-model-invocation: true
---
Fix: $ARGUMENTS

1. Read only that agent's folder, its kind's `CLAUDE.md`, and `core/contracts.py`. Read a base class only if the bug points there.
2. If I supplied log lines, use those; do not open log or data files yourself.
3. Write a failing test that reproduces the problem, then make the smallest fix.
4. If the root cause is in `core/`, stop and explain before changing it.
5. Tests + ruff pass; commit `fix(<agent>): …`; add a decision-log line if behaviour changed.
