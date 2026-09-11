# Upstox Agents — offline multi-agent trading platform

Python 3.11+, single `asyncio` process, in-process event bus, NiceGUI console.
All agents are deterministic Python: **no LLM/AI calls at runtime**. The only runtime network traffic is to Upstox.

## Read on demand — never read all docs at once
| Need | Open |
|------|------|
| What to build next / current status | `docs/ROADMAP.md` |
| Big picture, agent catalogue, UI, repo layout | `docs/ARCHITECTURE.md` |
| Behaviour of one agent | `docs/REQUIREMENTS.md` — jump to that agent's `###` heading only |
| Message types, base classes, skeletons | `core/contracts.py` + `core/*_base.py` (fallback before Phase 1: `docs/CONTRACTS.md`) |
| Known pitfalls, open questions | `docs/DECISIONS.md` |
| Rules for a folder | that folder's `CLAUDE.md` |

## Architecture in 8 lines
1. Auth → Instrument Master → Market Data Hub (one WebSocket, ref-counted subscriptions).
2. Candle agents build tick → 1m → 5m locally; history loaded once each morning and cached as Parquet.
3. Indicator agents: pure `compute(df)`; base class adds prev values and publishes `IndicatorResult`.
4. Strategy agents: only `decide(result, position) -> Action | None`; base class does sizing, ATM, logging, P&L.
5. Every `OrderRequest` passes the Risk Agent, then an Order Agent (Normal / GTT / Paper).
6. `OrderEvent`s are routed back to `origin_agent_id` via `correlation_id` (also written to the broker order tag).
7. System agents: square-off, charges, health, persistence/workspaces, export, recorder.
8. Same code for replay, paper and live — only the order adapter changes.

## Golden rules
- Agents talk only through `core/contracts.py` types on the bus. Never import one agent from another.
- Do not edit `core/` unless the task says so. If a contract change seems needed, stop and propose it first.
- New agent: `python -m tools.new_agent <indicator|strategy|order> <name>`, then edit only that folder.
- Never hardcode lot size, strike step, expiry, freeze quantity or charge rates — use Instrument Master / `config/`.
- Live order agents must refuse unless the pipeline is armed LIVE and Risk Agent is enabled.
- All datetimes are timezone-aware IST. Use `core.clock` (never `datetime.now()` directly) so replay works.
- Agent files stay under ~200 lines. Tests are offline: fixtures from `tests/fixtures/`, mocked broker, no network.
- Never open `.env`, token files, `~/upstox_runtime/`, exports, or large fixture files. Ask if you need data samples.

## Commands (available after Phase 1)
- Install: `pip install -e ".[dev]"`
- Test one agent: `pytest agents/<kind>/<name> -q`  · All: `pytest -q`
- Lint: `ruff check . && ruff format --check .`
- Run console: `python -m app` → http://localhost:8080

## Workflow
- Plan first for anything touching more than one folder; wait for approval before large edits.
- Branch: `phase/<n>-<slug>` or `agent/<name>`. Conventional commits. Small diffs.
- When a task is done: tests pass, lint clean, tick the box in `docs/ROADMAP.md`, log any decision in `docs/DECISIONS.md`.
- Skills: `/build-phase`, `/new-indicator`, `/new-strategy`, `/new-order-agent`, `/fix-agent`, `/add-requirement`.
