# Roadmap

> Work one phase at a time (`/build-phase <n>`). Tick boxes as tasks are completed and merged. Each phase ends with its exit criterion verified.

## Status

Phases 1–6 are built and covered by an offline test suite (`pytest -q`). Three
items are deliberately **not** done and are called out below: the charge rates,
the SEBI/Upstox live-trading confirmation and the market-feed protobuf decoder.
Every exit criterion that needs a live Upstox session is verified offline
instead — against fixtures and through the Replay agent — because this
repository has no broker credentials. Re-verify each one on a real session
before trading money.

## Phase 1 — Core and market data
Exit: live 1m/5m candles for a selected index shown in logs and recorded to Parquet.
*Verified offline: `tests/test_candles.py` and `tests/test_replay.py` build 1m and 5m
candles from recorded ticks and write them to Parquet. Re-check against a live feed.*
- [x] `pyproject.toml` (python 3.11, deps: nicegui, pandas, pyarrow, pydantic, pyyaml, python-dotenv, httpx, websockets, openpyxl, upstox SDK if suitable; dev: pytest, pytest-asyncio, ruff)
- [x] `core/contracts.py`, `core/bus.py` (async pub/sub, topic wildcards), `core/clock.py` (real + simulated IST clock)
- [x] `core/base_agent.py`, `core/registry.py` (discover `agents/*/*/manifest.yaml`)
- [x] `tools/new_agent.py` scaffolder + `_template/` folders for indicator, strategy, order
- [x] `broker/auth.py` — token load/validate/refresh flow, profile name; token stored in runtime dir
- [x] `broker/instruments.py` — daily instrument file download + cache; ATM, lot size, strike step, expiry lookup
- [x] `broker/ws_feed.py` — Market Data Hub, ref-counted subscribe/unsubscribe, reconnect + resubscribe
      *(the v3 feed's protobuf decoder is not included — see DECISIONS.md)*
- [x] `system_agents/candles.py` — tick→1m→5m, daily; history load + Parquet cache; gap back-fill after reconnect
- [x] `system_agents/recorder.py` — ticks and closed candles to Parquet
- [x] `tests/fixtures/` — small candle and tick samples; tests for bus, candle builder, ATM resolution

## Phase 2 — Indicators and console
Exit: indicator values visible live in the console; per-agent export works.
- [x] `core/indicator_base.py` (warm-up, closed-candle evaluation, prev values, export)
- [x] `agents/indicators/macd`
- [x] `agents/indicators/ichimoku` (projected and current spans)
- [x] `ui/` — header (token, name, market clock), pipeline builder stepper, widget grid generated from `Config` models
- [x] `system_agents/export.py` — per-agent export and Download All (Excel + Parquet/CSV ticks, zipped)

## Phase 3 — Strategies and paper trading
Exit: one full paper-trading day with correct trade log and net P&L.
*Verified offline: `tests/test_integration.py` runs a complete round trip from tick to
trade row. A full session still needs a real paper day before Phase 5.*
- [x] `core/strategy_base.py` (position state machine, ATM/future/stock resolution, sizing, trade log, stats)
- [x] `core/order_base.py` + `broker/paper.py` Paper Order Agent (slippage, simulated target/SL)
- [x] `system_agents/charges.py`
- [ ] `config/charges.yaml` filled with current rates — **left empty on purpose.** Rates are
      statutory and change; they must be copied from Upstox's current charges page, not guessed.
      Until then the calculator reports `configured: false` and charges of zero.
- [x] Strategies: `macd_s1_cross_option`, `macd_s2_momentum_option`, `macd_s3_momentum_stock`, `ichimoku_s1_cloud_option`
- [x] Strategy widgets: closed trades, open trade MTM, gross, charges, net, Pause/Stop/Export

## Phase 4 — Safety and persistence
Exit: survives restart and WebSocket disconnect; squares off on time; kill switch works.
- [x] `system_agents/risk.py` + `config/risk.yaml`, kill switch in header
- [x] `system_agents/squareoff.py` (no-new-entries time, square-off time, flat verification)
- [x] `system_agents/health.py` (heartbeat, stale data, token expiry, clock check)
- [x] `system_agents/persistence.py` (SQLite, workspaces save/auto-load, re-resolve expired instruments)
- [x] Startup reconciliation with broker positions/GTTs via order tag

## Phase 5 — Live orders
Exit: small-size live trades with correct confirmations routed to the right agent.
- [ ] Confirm current SEBI/Upstox retail algo requirements (static IP, rate limits, algo tagging);
      record in DECISIONS.md — **not done: this needs your Upstox account and cannot be
      answered from the code.** The order-rate limiter is already in the Risk Agent; set
      `max_orders_per_second` to whatever limit applies to you.
- [x] `agents/orders/normal` (partial fills, rejections, freeze-quantity slicing)
- [x] `agents/orders/gtt` (market entry, target/SL legs per current Upstox GTT docs, leg-hit reporting)
      *(verify the supported leg combinations against the live docs before trading)*
- [x] Live arming flow (confirmation, token, risk enabled, reconciliation passed, paper-first rule)

## Phase 6 — Research tools
Exit: a new strategy can be replayed on recorded data before paper/live.
- [x] Replay/backtest agent using simulated clock + Paper Order Agent
- [x] Analytics agent (win rate, profit factor, drawdown, expectancy)
- [x] Notification agent (Telegram)
- [x] Paper-first promotion rule before Live unlock
