# system_agents/ — infrastructure agents

Agents here: candles, recorder, risk, squareoff, charges, health, persistence, export, replay, analytics, notifications. Specs: `docs/REQUIREMENTS.md` → "Market data" and "Safety and support agents".

- Candle builder: candles align to 09:15 IST; volume = delta of cumulative day volume; emit `is_closed=False` updates and one `is_closed=True` event per candle; back-fill gaps after reconnect from the intraday candle API.
- History length = max(config/data.yaml days, largest `warmup_bars` of attached indicators).
- Risk agent is the only path from `Signal`/`OrderRequest` to order agents. Rejections are `OrderEvent(status="REJECTED")` with a reason.
- Square-off order: cancel open GTTs → exit intraday positions → verify flat via positions API → publish `system.squareoff.done`.
- Charges come only from `config/charges.yaml` (versioned by `effective_from`).
- Export: every agent's `export_frames()`; ticks go to Parquet/CSV, never Excel.
- Persistence: SQLite in `$UPSTOX_RUNTIME_DIR`; workspaces as YAML; on reload re-resolve options/futures to the current ATM/expiry.
- Replay publishes recorded ticks on the ordinary `tick.*` topics under a `SimClock`: no special code path anywhere else.
- Timer loops sleep on real time and read `core.clock` for decisions, so a `SimClock` never spins them.
- Notifications must never affect trading: a failing transport is logged and swallowed.
