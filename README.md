# Upstox Agents

An offline, multi-agent trading platform for Upstox: pick an instrument → timeframe → indicator → strategy → order agent from a Python web console, trade in paper or live mode, and export everything with one click. Agents are deterministic Python and use no AI tokens at runtime. Claude Code is used only to build and change agents.

## Getting started

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # then fill in your Upstox API key, secret and redirect URI
mkdir -p ~/upstox_runtime     # ticks, candles, SQLite, exports and the token live here
pytest -q                     # 271 offline tests: no network, no broker
python -m app                 # the console on http://localhost:8080
```

## Connecting Upstox

Upstox access tokens last one trading day, so this is a morning ritual.

1. Create an app at [Upstox's developer console](https://account.upstox.com/developer/apps)
   and copy the API key and secret into `.env`.
2. **Register `http://localhost:8080/auth/callback` as that app's redirect URI**, and put
   the same value in `UPSTOX_REDIRECT_URI`. This is the step people miss: Upstox
   only ever redirects to the URI registered on the app, and the console serves
   exactly the path in your `.env`, so the two have to agree.
3. Start the console and click **Login to Upstox** in the header. Approve the app
   in the tab that opens; you land back on the console, connected.
4. If you registered a different redirect URI (a public host, a different port),
   approve anyway, copy the `code=` value out of the address bar you land on, and
   paste it into the same dialog.

The token is written to `$UPSTOX_RUNTIME_DIR/upstox_token.json` with `0600`
permissions and is never logged. The header badge shows `token ok` once the
profile endpoint confirms it; tomorrow it will say the token is from an earlier
day and the Login button comes back.

Nothing above is needed to browse instruments or build a pipeline — the
instrument file is public and loads without a login. You need the token for
market data, paper fills priced off the live feed, and any live order.

## Using the console

The console opens on the pipelines page. Add a
pipeline with the stepper (instrument → segment → product → timeframe →
indicator → strategy → order agent → parameters → Paper/Live), and watch the
agent widgets. Paper mode needs no arming and routes every order to the Paper
Order Agent.

**Before you trade real money**

1. Fill in `config/charges.yaml` with the current rates from Upstox's charges page.
   It ships empty on purpose: until it is filled the trade log reports charges of
   zero and the widget says `configured: false`.
2. Confirm the current SEBI/Upstox retail algo requirements for your account
   (static IP registration, order-rate thresholds, algo tagging) and set
   `max_orders_per_second` in `config/risk.yaml` accordingly.
3. Verify the GTT leg combinations in `agents/orders/gtt/agent.py` against the
   current Upstox GTT documentation.
4. Generate the market-feed protobuf decoder from Upstox's published proto and
   pass it to `UpstoxFeedClient(rest, decode=...)`; without it the feed yields
   only JSON frames.
5. Run several paper sessions. Live arming is refused until the Analytics agent
   has counted enough of them (`min_paper_sessions`).

## Working on it with Claude Code

Start Claude Code with `claude` in the repo; it loads `CLAUDE.md` automatically.
`/build-phase <n>` works through `docs/ROADMAP.md`, and `/new-strategy`,
`/new-indicator`, `/new-order-agent` and `/fix-agent` cover the day-to-day work.

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
