# Architecture

> Read this when you need the big picture. For agent behaviour see docs/REQUIREMENTS.md; for types and base classes see docs/CONTRACTS.md.

"Offline agent" means **zero LLM / AI-token usage at runtime**. Every agent is plain, deterministic Python. The only runtime network calls go to Upstox. Claude Code is used only at development time.

## Design principles

| # | Principle | What it means in practice |
|---|-----------|---------------------------|
| P1 | Deterministic, offline agents | No AI calls at runtime. Same input always gives the same output, which makes testing and replay possible. |
| P2 | Fetch once, share many | One WebSocket connection, one historical load per morning, one candle builder per instrument+timeframe. Any number of agents can subscribe to it. |
| P3 | Same code for replay, paper and live | Strategies never know which mode they are in. Only the order adapter changes (Paper vs Upstox). |
| P4 | Plug-in agents with a fixed contract | Every agent type has a base class and a message contract. New agents only implement one or two methods. |
| P5 | Broker is the source of truth | On every restart the system reconciles its positions and GTTs with Upstox before trading. |
| P6 | Safety before profit | A Risk agent approves every order. A global kill switch and a time-based square-off always win over strategy logic. |
| P7 | Small files, small context | Each agent lives in its own folder, typically under 200 lines, so Claude Code only needs the skeleton plus one agent to work. |

## System overview

```
                        ┌──────────────────────────── Main Console (NiceGUI web page) ───────────────────────────┐
                        │ Token status · User name · Pipeline builder · Agent widgets · Kill switch · Download all │
                        └───────────────────────────────────────────┬────────────────────────────────────────────┘
                                                                    │ (same event bus)
 ┌────────────┐   ┌──────────────────┐   ┌────────────────┐   ┌─────┴──────┐   ┌──────────────┐   ┌──────────────┐
 │ Auth Agent │──▶│ Instrument Master│──▶│ Market Data Hub│──▶│  Candle    │──▶│  Indicator   │──▶│  Strategy    │
 │ (.env,     │   │ (daily symbols,  │   │ (1 WebSocket,  │   │  Builders  │   │  Agents      │   │  Agents      │
 │  token)    │   │  lots, strikes)  │   │  ref-counted   │   │ tick→1m→5m │   │ (MACD,       │   │ (MACD S1–S3, │
 └────────────┘   └──────────────────┘   │  subscriptions)│   │ + history  │   │  Ichimoku…)  │   │  Ichimoku S1)│
                                         └───────┬────────┘   └────────────┘   └──────────────┘   └──────┬───────┘
                                                 │                                                        │ Signal
                                         ┌───────┴────────┐                                        ┌──────┴───────┐
                                         │ Recorder       │                                        │ Risk Agent   │
                                         │ (ticks/candles │                                        │ (approve /   │
                                         │  → Parquet)    │                                        │  reject)     │
                                         └────────────────┘                                        └──────┬───────┘
                                                                                                          │ OrderRequest
 ┌──────────────┐  ┌───────────────┐  ┌───────────────┐  ┌──────────────┐                     ┌──────────┴────────┐
 │ Square-off   │  │ Charges       │  │ Health /      │  │ Persistence  │                     │ Order Agents      │
 │ Scheduler    │  │ Calculator    │  │ Watchdog      │  │ (SQLite +    │                     │ Normal · GTT ·    │
 │              │  │               │  │               │  │  workspaces) │                     │ Paper (simulated) │
 └──────────────┘  └───────────────┘  └───────────────┘  └──────────────┘                     └───────────────────┘
```

**Runtime model.** One Python process running an `asyncio` event loop, with an in-process publish/subscribe event bus. This is the simplest robust option for a single-user retail system: low latency, no extra servers, and fewer moving parts to fail. The bus sits behind an interface, so it can later be swapped for Redis or ZeroMQ if you want the engine and UI in separate processes.

**Storage.** SQLite for orders, trades, pipeline configurations and agent state. Parquet files (one per instrument per day) for ticks and candles. All runtime data lives **outside the Git repository** (for example `~/upstox_runtime/`).

## Agent catalogue

Agents marked **NEW** were not in the original requirements and are recommended for robustness.

| Group | Agent | Responsibility |
|-------|-------|----------------|
| Infrastructure | Auth Agent | Daily login, token storage, token validity check, user profile (name) for the console. |
| Infrastructure | Instrument Master Agent **NEW** | Downloads the Upstox instrument file each morning; resolves symbols, lot sizes, strike steps, expiries and ATM strikes. |
| Market data | Market Data Hub (Tick Agent) | Owns the single WebSocket; reference-counted subscriptions; fans ticks out to all subscribers. |
| Market data | Candle Agents (1m, 5m, 1D) | Build candles from ticks, merge with the morning historical load, emit "candle updated" and "candle closed" events. |
| Market data | Recorder Agent **NEW** | Writes every received tick and closed candle to Parquet. This builds your own tick history. |
| Analysis | Indicator Agents | MACD, Ichimoku, and future indicators. Output current and previous values. |
| Decision | Strategy Agents | Turn indicator results into signals; track positions, trade log and P&L. |
| Safety | Risk Agent **NEW** | Pre-trade checks and daily loss limits; kill switch. |
| Safety | Square-off Scheduler **NEW** | Stops new intraday entries and squares off at configurable times. |
| Execution | Normal Order Agent | Market/limit order, returns the fill confirmation. |
| Execution | GTT Order Agent | Market entry plus broker-side target and stop-loss; reports which leg hit back to the originating agent. |
| Execution | Paper Order Agent **NEW** | Same interface as live agents; simulates fills and GTT legs from live ticks. |
| Support | Charges Calculator **NEW** | Brokerage, STT, exchange fees, SEBI fee, stamp duty, GST from a versioned config file. |
| Support | Health / Watchdog Agent **NEW** | WebSocket heartbeat, stale-data detection, reconnect/resubscribe, token-expiry detection. |
| Support | Persistence & Workspace Agent **NEW** | Saves and reloads the full agent setup; crash recovery and broker reconciliation. |
| Support | Export Agent | One-click export of all agents' data. |
| Optional | Replay / Backtest Agent **NEW** | Feeds recorded data through the same pipeline with a simulated clock. |
| Optional | Analytics Agent **NEW** | Win rate, profit factor, drawdown, expectancy per strategy. |
| Optional | Notification Agent **NEW** | Telegram/email alerts for fills, SL hits, errors and disconnects. |

## Main console (UI)

**Recommended framework: NiceGUI.** It is pure Python, built on FastAPI, runs in the same `asyncio` loop as the engine, and pushes live updates over WebSockets, which suits dashboards with many live widgets. Streamlit is not recommended here because it re-runs the script on every interaction, which is a poor fit for a long-running trading engine.

**Launch flow.** On start, the console checks the token. If valid, it shows the user's name, market status and the saved workspace. If invalid, it shows the login button.

**Pipeline builder (stepper).** One pipeline = one row of choices. The user can add, clone, pause and remove any number of pipelines in any combination.

| Step | Choice |
|------|--------|
| 1 | Instrument (stock or index, searchable from Instrument Master) |
| 2 | Segment: Option / Future / Stock |
| 3 | Product: Intraday / Delivery-Overnight |
| 4 | Timeframe agent: Tick / 1m / 5m / 1D |
| 5 | Indicator agent(s) |
| 6 | Strategy agent (only strategies compatible with the chosen indicator are shown) |
| 7 | Order agent: Normal / GTT (Paper agent is used automatically in Paper mode) |
| 8 | Parameters: target, stop-loss, lots or capital, opposite-signal behaviour |
| 9 | Execution mode: Paper / Live |

**Page layout.** A header with token status, user name, market clock, total gross/charges/net P&L, **Kill Switch** and **Download All**. A pipelines page with the builder and a grid of agent widgets. A positions and orders page. A logs page with filtering by agent. A settings page for square-off times, risk limits and charges.

**Compatibility checks.** Agents declare what they require (e.g., Ichimoku S1 requires the Ichimoku indicator; S3 requires segment Stock). The builder only offers valid combinations, preventing misconfigured pipelines.

## Repository layout

```
upstox-agents/
├── CLAUDE.md                   # short: architecture summary, golden rules, pointers
├── README.md                   # for humans: setup and Claude Code workflow
├── docs/                       # read on demand, never auto-imported
│   ├── ARCHITECTURE.md  REQUIREMENTS.md  CONTRACTS.md  DECISIONS.md  ROADMAP.md
├── .claude/
│   ├── settings.json           # permission rules
│   └── skills/                 # /build-phase /new-indicator /new-strategy /new-order-agent /fix-agent /add-requirement
├── core/                       # stable framework; rarely edited (+ CLAUDE.md)
│   ├── contracts.py  bus.py  base_agent.py  indicator_base.py
│   ├── strategy_base.py  order_base.py  registry.py  clock.py
├── broker/                     # Upstox adapters: auth, rest, websocket, instruments, paper (+ CLAUDE.md)
├── system_agents/              # risk, squareoff, charges, health, persistence, export, recorder (+ CLAUDE.md)
├── agents/
│   ├── indicators/ {CLAUDE.md, _template/, macd/, ichimoku/}
│   ├── strategies/ {CLAUDE.md, _template/, macd_s1_cross_option/, …}
│   └── orders/     {CLAUDE.md, _template/, normal/, gtt/}
├── ui/                         # NiceGUI pages and widget components (+ CLAUDE.md)
├── config/                     # session.yaml, data.yaml, macd.yaml, ichimoku.yaml, risk.yaml, charges.yaml
├── tests/fixtures/             # small sample candle/tick files for offline tests
├── tools/new_agent.py          # scaffolding: python -m tools.new_agent strategy my_name
├── .env.example
└── .gitignore                  # .env, token, runtime data, exports
```

Runtime data (ticks, candles, SQLite DB, logs, exports, token) lives in `~/upstox_runtime/`, never inside the repo.

