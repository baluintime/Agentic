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

## Is it actually working?

The pipelines page opens with a **Live prices** panel: every instrument the feed
is subscribed to, its last traded price and how long ago it ticked. If prices are
moving there, the whole chain is alive — feed, candles, indicators, strategies.

If it says `feed down` or `no tick yet`, check in this order:

| Symptom | Cause |
|---------|-------|
| `no feed attached — log in to Upstox` | No valid token yet; the feed needs one. |
| `the market feed decoder is missing` | Dependencies are out of date: `pip install -e ".[dev]"`. |
| `connecting…` with rising reconnects | Token rejected, or no network to `api.upstox.com`. |
| Repeated `HTTP 403` on connect | Upstox caps concurrent feed connections. Stop the app, wait a minute for the broker to release them, start again. |
| `connected, waiting for the first tick` | The market is closed, or nothing has traded yet. |
| Prices move, `market-data → ticks` rises | Working. Indicator `evaluations` climbs once candles close. |

Upgrading? Run `pip install -e ".[dev]"` after every pull — the feed decoder
arrived as a new dependency and the console cannot receive prices without it.

The `market-data` widget carries the same facts in detail: `connected`,
`subscriptions`, `ticks`, `reconnects` and the last tick's timestamp.

## Risk limits

Every order passes the Risk Agent, and its limits live in `config/risk.yaml`.
They are deliberately small starting values — raise them once you know what you
want to run, and restart the console to pick up the change.

| Setting | Blocks | Rejection message says |
|---------|--------|------------------------|
| `max_lots_per_order` | An order larger than N lots | `N lots is above max_lots_per_order=…` |
| `max_open_positions` | A new entry while N positions are already open | `max open positions reached (…)` |
| `max_trades_per_day_per_pipeline` | Further entries from a busy pipeline | `max trades/day …` |
| `max_capital_per_stock_order` | A stock order above ₹N | `order value above …` |
| `max_daily_loss_total` / `…_per_pipeline` | New entries after the day's loss limit | `daily loss limit hit …` |
| `max_orders_per_second` | Bursts above the broker/SEBI rate | `order rate above …` |

Every rejection names the setting and the file, so a blocked order tells you
which line to edit. The `risk` widget shows the live count of open positions; if
it ever disagrees with what your strategies actually hold, the engine
reconciles it whenever a pipeline is added or removed.

## Strategy parameters

Target and stop-loss take decimals — `0.5`, `0.25`, `12.75` — and are in points
on the **traded** instrument, so for an option pipeline they are points of
premium, not index points. Switch `target_mode` to `percent` to express them as
a percentage of the fill price instead.

A target of `0` means *no target leg*, and the same for the stop-loss. That is
useful for a strategy that exits on the opposite signal alone, but it does mean
a mistyped zero leaves the position running unprotected.

Trigger prices sent to the broker are snapped to the instrument's tick size, so
a 0.33 target on a 0.05-tick option becomes a valid trigger rather than an order
the exchange rejects.

Lots and indicator periods are whole numbers; anything else is refused with a
message naming the field.

## Exports

`Download All` and each widget's `Export` button write into
`$UPSTOX_RUNTIME_DIR/exports/` and then hand the file to the browser. The
console always tells you the filename and folder, so if your browser blocks or
silently discards the download the file is still on disk where the message says.

Every archive contains a `summary` sheet listing every agent's status, so an
export taken before the first trade is still a useful file. Trades, orders,
indicator values and candles get a sheet each; ticks go in as Parquet because a
liquid instrument can exceed Excel's row limit in a single day.

A widget whose `Export` button reports "nothing to export yet" says why —
no closed trades, no evaluated candles — rather than failing silently.

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
