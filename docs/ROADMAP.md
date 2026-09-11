# Roadmap

> Work one phase at a time (`/build-phase <n>`). Tick boxes as tasks are completed and merged. Each phase ends with its exit criterion verified.

## Phase 1 — Core and market data
Exit: live 1m/5m candles for a selected index shown in logs and recorded to Parquet.
- [ ] `pyproject.toml` (python 3.11, deps: nicegui, pandas, pyarrow, pydantic, pyyaml, python-dotenv, httpx, websockets, openpyxl, upstox SDK if suitable; dev: pytest, pytest-asyncio, ruff)
- [ ] `core/contracts.py`, `core/bus.py` (async pub/sub, topic wildcards), `core/clock.py` (real + simulated IST clock)
- [ ] `core/base_agent.py`, `core/registry.py` (discover `agents/*/*/manifest.yaml`)
- [ ] `tools/new_agent.py` scaffolder + `_template/` folders for indicator, strategy, order
- [ ] `broker/auth.py` — token load/validate/refresh flow, profile name; token stored in runtime dir
- [ ] `broker/instruments.py` — daily instrument file download + cache; ATM, lot size, strike step, expiry lookup
- [ ] `broker/ws_feed.py` — Market Data Hub, ref-counted subscribe/unsubscribe, reconnect + resubscribe
- [ ] `system_agents/candles.py` — tick→1m→5m, daily; history load + Parquet cache; gap back-fill after reconnect
- [ ] `system_agents/recorder.py` — ticks and closed candles to Parquet
- [ ] `tests/fixtures/` — small candle and tick samples; tests for bus, candle builder, ATM resolution

## Phase 2 — Indicators and console
Exit: indicator values visible live in the console; per-agent export works.
- [ ] `core/indicator_base.py` (warm-up, closed-candle evaluation, prev values, export)
- [ ] `agents/indicators/macd`
- [ ] `agents/indicators/ichimoku` (projected and current spans)
- [ ] `ui/` — header (token, name, market clock), pipeline builder stepper, widget grid generated from `Config` models
- [ ] `system_agents/export.py` — per-agent export and Download All (Excel + Parquet/CSV ticks, zipped)

## Phase 3 — Strategies and paper trading
Exit: one full paper-trading day with correct trade log and net P&L.
- [ ] `core/strategy_base.py` (position state machine, ATM/future/stock resolution, sizing, trade log, stats)
- [ ] `core/order_base.py` + `broker/paper.py` Paper Order Agent (slippage, simulated target/SL)
- [ ] `system_agents/charges.py` + `config/charges.yaml` filled with current rates
- [ ] Strategies: `macd_s1_cross_option`, `macd_s2_momentum_option`, `macd_s3_momentum_stock`, `ichimoku_s1_cloud_option`
- [ ] Strategy widgets: closed trades, open trade MTM, gross, charges, net, Pause/Stop/Export

## Phase 4 — Safety and persistence
Exit: survives restart and WebSocket disconnect; squares off on time; kill switch works.
- [ ] `system_agents/risk.py` + `config/risk.yaml`, kill switch in header
- [ ] `system_agents/squareoff.py` (no-new-entries time, square-off time, flat verification)
- [ ] `system_agents/health.py` (heartbeat, stale data, token expiry, clock check)
- [ ] `system_agents/persistence.py` (SQLite, workspaces save/auto-load, re-resolve expired instruments)
- [ ] Startup reconciliation with broker positions/GTTs via order tag

## Phase 5 — Live orders
Exit: small-size live trades with correct confirmations routed to the right agent.
- [ ] Confirm current SEBI/Upstox retail algo requirements (static IP, rate limits, algo tagging); record in DECISIONS.md
- [ ] `agents/orders/normal` (partial fills, rejections, freeze-quantity slicing)
- [ ] `agents/orders/gtt` (market entry, target/SL legs per current Upstox GTT docs, leg-hit reporting)
- [ ] Live arming flow (confirmation, token, risk enabled, reconciliation passed)

## Phase 6 — Research tools
Exit: a new strategy can be replayed on recorded data before paper/live.
- [ ] Replay/backtest agent using simulated clock + Paper Order Agent
- [ ] Analytics agent (win rate, profit factor, drawdown, expectancy)
- [ ] Notification agent (Telegram)
- [ ] Paper-first promotion rule before Live unlock
