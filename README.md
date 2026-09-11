# Upstox Agents

An offline, multi-agent trading platform for Upstox: pick an instrument → timeframe → indicator → strategy → order agent from a Python web console, trade in paper or live mode, and export everything with one click. Agents are deterministic Python and use no AI tokens at runtime. Claude Code is used only to build and change agents.

## Getting started with Claude Code

1. Put this folder in a new Git repository: `git init && git add . && git commit -m "chore: project spec and Claude Code setup"`, then push it to GitHub.
2. Copy `.env.example` to `.env` and fill in your Upstox API key, secret and redirect URI. Create the runtime folder: `mkdir -p ~/upstox_runtime`.
3. Open a terminal in the repo and start Claude Code with `claude`. It loads `CLAUDE.md` automatically. You do not need to run `/init`.
4. Type `/build-phase 1`. Claude will propose a plan; approve it and let it build. Repeat for later phases.
5. Before Phase 3, fill in `config/charges.yaml` with current rates. Before Phase 5, confirm SEBI/Upstox retail algo requirements (for example static IP registration).

## How the files are organised for low token use

| File | Loaded when | Purpose |
|------|-------------|---------|
| `CLAUDE.md` | Every session, automatically | Short map: architecture in 8 lines, golden rules, commands |
| `<folder>/CLAUDE.md` | When Claude works in that folder | Contract and rules for that agent type |
| `docs/*.md` | Only when Claude opens them | Full requirements, architecture, contracts, decisions, roadmap |
| `.claude/skills/*/SKILL.md` | Only when you type the command | Reusable workflows |

The root `CLAUDE.md` deliberately does not `@import` the docs, because imported files are loaded into every session.

## Everyday commands

| You type | What happens |
|----------|--------------|
| `/build-phase 2` | Builds the unticked tasks of Phase 2, plan first |
| `/new-strategy macd_s4 macd "buy ATM CE when histogram turns positive, exit when it turns negative"` | Scaffolds a strategy, asks about ambiguities, writes the rule and tests |
| `/new-indicator supertrend "period 10, multiplier 3"` | Scaffolds and implements an indicator with tests |
| `/new-order-agent bracket "…"` | New order agent |
| `/fix-agent agents/strategies/macd_s1_cross_option "entered twice on the same candle"` | Reproduces with a test, then fixes |
| `/add-requirement "…"` | Updates docs and roadmap only, no code |

## Tips to keep token usage low

Start one Claude Code session per task and use `/clear` between unrelated tasks; use `/compact` if a session runs long. When working on one agent type, you can start Claude Code from inside that folder (for example `agents/strategies`) so its `CLAUDE.md` is in scope. When debugging, paste only the relevant log lines rather than asking Claude to read log files. Keep runtime data in `~/upstox_runtime/`, outside the repo; the deny rules in `.claude/settings.json` are an extra layer, not a guarantee.

## Git workflow

One branch per phase (`phase/<n>-<slug>`) or per agent (`agent/<name>`), conventional commits, pull requests via `gh pr create`. Add a GitHub Actions workflow running `ruff` and `pytest` in Phase 1.

## Where to read more

`docs/ARCHITECTURE.md` (big picture and UI), `docs/REQUIREMENTS.md` (every agent), `docs/CONTRACTS.md` (message types and skeletons), `docs/DECISIONS.md` (pitfalls, open questions, suggestions), `docs/ROADMAP.md` (build order and status).
