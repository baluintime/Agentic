# Decisions, pitfalls and suggestions

> Check the "Issues found" table before implementing data, indicator or strategy logic: it lists traps already identified. Record new decisions at the bottom under "Decision log".

## Review findings: gaps and corrections in the original requirements

### Issues found

| # | Original requirement | Problem | Resolution |
|---|----------------------|---------|------------|
| 1 | 1-day agent loads 75 days | Ichimoku needs 78 bars for the current cloud; MACD's EMAs need roughly 100 bars to stabilise. Daily indicators would be wrong or empty. | Load 200 daily bars; history length driven by `warmup_bars`. |
| 2 | Tick agent loads 5 days of history | The Upstox historical API provides candles, not tick-by-tick history. | Recorder Agent stores ticks daily; after 5 days you have your own tick history. |
| 3 | Ichimoku agent "calculate the MACD" | Copy-paste error. | Corrected in 4.4. |
| 4 | Ichimoku S1 Span A / Span B cross | Spans are displaced 26 bars; "cross" is ambiguous. | Both projected and current spans published; strategy parameter selects one. |
| 5 | MACD S2 / S3 "MACD greater than previous MACD" | Evaluated on every tick, this flips constantly, causing many trades and heavy charges. | Evaluate on candle close by default; add optional minimum-change threshold (hysteresis). |
| 6 | MACD S3 "sell the stock" | Cash-segment shorts cannot be held overnight. | Shorting only in intraday mode; delivery mode is long-only. |
| 7 | "Order agent work only if live" | Paper trades still need fills and target/SL tracking. | Paper Order Agent with the same interface. |
| 8 | Excel export of all data | Tick data can exceed Excel's row limit in a day. | Ticks exported as Parquet/CSV; everything else in Excel. |
| 9 | Lot size and ATM strike | Lot sizes and strike steps change; hardcoding breaks silently. | Instrument Master Agent resolves them daily. |
| 10 | Multiple pipelines on the same instrument | Pipelines could hold opposing positions, and broker positions are netted. | Each pipeline tracks its own trades via correlation ID; Risk Agent sees the net broker position. |
| 11 | No crash recovery | A restart would forget open positions and GTTs. | Broker reconciliation on startup; order tags link positions to pipelines. |
| 12 | "Save and reload next day" | Reloading into an expired option or rolled future. | On reload, option/future instruments are re-resolved (new ATM, current expiry); only the pipeline definition is saved. |

### Decisions you still need to make

| Question | Suggested default |
|----------|-------------------|
| What happens on an opposite signal while a position is open (S1, Ichimoku S1)? | `exit` (configurable to `reverse` or `ignore`). |
| Which expiry for option strategies? | Nearest weekly for indices, nearest monthly for stocks; roll on expiry day after 13:00. |
| May option pipelines hold overnight? | Allowed only when product is Delivery-Overnight; blocked on expiry day. |
| Are target/SL in points or percent? | Both supported; points by default. |
| Evaluate on candle close or every tick? | Candle close. |
| One open position per pipeline, or pyramiding? | One per pipeline. |

## New suggestions from analysing the requirements

**Regulatory check before going Live.** SEBI has introduced a framework for retail algorithmic trading through broker APIs, covering items such as static IP whitelisting, order-rate thresholds and algo identification. Confirm the current requirements with Upstox (including whether your IP must be registered for order APIs) before enabling Live mode, and build the order-rate limiter in the Risk Agent to stay under the applicable threshold.

**Replay and backtest from day one.** Because agents are deterministic and all data is recorded, the Replay Agent can push recorded ticks or candles through the exact same pipelines with a simulated clock and the Paper Order Agent. You can test a new Claude-built strategy on last week's data in minutes before ever running it live. This is the single highest-value addition.

**Paper-first promotion rule.** Require every new strategy to run in Paper mode for a set number of sessions, with its Analytics results visible, before the Live option is unlocked for it.

**Option-specific filters.** Add optional filters to option strategies: minimum premium, maximum bid-ask spread, and minimum open interest, so orders are not placed in illiquid strikes where market orders suffer large slippage.

**Volatility-aware stops.** Offer an ATR-based target/SL mode alongside fixed points, since a fixed 10-point stop means very different risk on a calm day versus a volatile one.

**Time-of-day filters.** Allow each strategy to skip the first N minutes after open and to avoid entries in the last N minutes before cut-off; indicator signals in the opening minutes are often noisy.

**Charges awareness in signals.** Show in the widget the estimated round-trip charges per trade; for high-frequency strategies such as S2 this often decides whether the strategy is profitable at all.

**Alerts.** A Notification Agent (Telegram is simple to set up) for fills, SL hits, disconnects and risk breaches, so you do not have to watch the console all day.

**Structured logging.** JSON logs with `agent_id`, `pipeline_id` and `correlation_id` on every line make it possible to trace one trade from tick to fill, and to give Claude only the relevant log lines when debugging.

**Separate engine and UI later.** Start with one process for simplicity. If the UI ever becomes heavy, move the engine to its own process behind a local API so a UI problem can never interrupt trading.

## Decision log

Append one line per decision: `YYYY-MM-DD — decision — reason`.

