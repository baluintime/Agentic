# Agent requirements

> Do not read this whole file. Search for the heading of the agent you are working on (e.g. `### Indicator agents`, `### Order agents`) and read only that section.

## Detailed requirements

### Auth Agent

The Auth Agent reads `UPSTOX_API_KEY`, `UPSTOX_API_SECRET` and `UPSTOX_REDIRECT_URI` from `.env`. On startup it loads the stored token and validates it by calling the user-profile endpoint. If the token is valid, it publishes the user's name to the console; if not, the console shows a "Login to Upstox" button that starts the OAuth flow and stores the new token.

Upstox access tokens are valid for one trading day and must be renewed daily. Prefer Upstox's officially supported login/approval flow. Avoid headless-browser automation of the login page with stored passwords/TOTP: it breaks whenever the page changes and may conflict with the broker's terms. The token file is stored outside the repo with restricted file permissions and is never written to logs.

### Instrument Master Agent (NEW)

Each morning this agent downloads the Upstox instrument file and caches it locally. It answers questions such as "what is the instrument key for NIFTY?", "what is the current lot size?", "what is the strike step for this expiry?" and "what is the ATM CE/PE for spot 24,873?". Lot sizes and strike steps change from time to time, so they must never be hardcoded.

ATM strike is `round(spot / strike_step) * strike_step`, using the strike step derived from the chosen expiry. Expiry selection is configurable per pipeline (nearest weekly, nearest monthly), with a rule to roll to the next expiry on expiry day after a configurable time.

### Market data

**Fetch only what is needed.** The hub subscribes only to the instruments used by active pipelines: the selected underlying, plus a small window of option strikes around ATM (default ATM ± 2 strikes, CE and PE, for the selected expiry). When spot moves by more than one strike step, the window is re-centered and far strikes are unsubscribed. Subscriptions are reference-counted, so an instrument is unsubscribed only when the last agent using it is removed.

**Candles are built locally.** Rather than polling the API for 1-minute and 5-minute candles, the candle agents aggregate them from the tick stream (tick → 1m → 5m). This gives one consistent data source, no polling load, and instant candle-close events. Candles align to the NSE session start (09:15 IST, so 5-minute candles start 09:15, 09:20, …). Volume per candle is derived from the change in cumulative day volume in the feed.

**Historical load once per morning.** On first start of the day, each candle agent loads history from the Upstox historical candle API, stores it in Parquet, and reuses it for the rest of the day. If the app restarts intraday, it reloads from the local cache and only fetches the missing part of today from the intraday candle API.

**Reconciliation.** After any WebSocket reconnect, the candle agent back-fills the gap from the intraday candle API so indicators are not computed on candles with missing ticks.

| Agent | Live | Default history | Note |
|-------|------|-----------------|------|
| Tick | WebSocket | Not available from the API | Tick history must be **self-recorded** by the Recorder Agent. The historical API supplies candles, not ticks. |
| 1-minute | Built from ticks | 5 trading days (≈1,875 bars) | Sufficient for MACD and Ichimoku. |
| 5-minute | Built from 1-minute | 5 trading days (≈375 bars) | Sufficient. |
| 1-day | Today's forming candle from ticks | **200 days recommended** (original: 75) | 75 days is not enough; see docs/DECISIONS.md. |

**History length is driven by indicators.** Each indicator declares `warmup_bars`. The candle agent loads `max(configured_days, warmup requirement)`, so adding a new indicator can never silently run on too little data.

### Indicator agents

All indicators follow one contract. The author writes one pure function `compute(df) -> df` that adds columns to a candle DataFrame. The base class handles subscription to the attached candle agent, computing on each closed candle, attaching previous values, publishing results, and exporting to Excel. Recomputing over the last few hundred bars on every candle close is cheap and avoids the bugs of hand-written incremental updates.

Indicators run on **closed candles** by default, which prevents signals that appear and disappear within a candle. A per-pipeline option `evaluate_on: close | every_tick` exists for strategies that deliberately want intra-candle evaluation.

Every `IndicatorResult` contains the current and previous values of each output, plus the current and previous candle OHLC.

**MACD Agent.** Parameters come from `config/macd.yaml` (default fast 12, slow 26, signal 9, source close). Outputs are `macd`, `signal`, `histogram`, `open`, `high`, `low`, `close`, and the `prev_` version of each. Warm-up: at least `3 × slow + signal` bars for stable EMA values.

**Ichimoku Agent.** Parameters come from `config/ichimoku.yaml` (default 9, 26, 52, displacement 26). The original text said "calculate MACD" for Ichimoku; this is corrected here. Outputs and their `prev_` versions:

| Output | Formula |
|--------|---------|
| Conversion line (Tenkan-sen) | (highest high + lowest low) / 2 over 9 bars |
| Base line (Kijun-sen) | (highest high + lowest low) / 2 over 26 bars |
| Leading Span A (Senkou A) | (Tenkan + Kijun) / 2, plotted 26 bars ahead |
| Leading Span B (Senkou B) | (highest high + lowest low) / 2 over 52 bars, plotted 26 bars ahead |
| Lagging Span (Chikou) | Close plotted 26 bars behind |

Because the spans are displaced, the agent publishes both versions and the strategy chooses: `span_a_projected` / `span_b_projected` (calculated now, plotted in the future; a cross here is the classic "Kumo twist") and `span_a_current` / `span_b_current` (the cloud under today's price, calculated 26 bars ago). Warm-up: 52 + 26 = 78 bars.

### Strategy agents

**Common behaviour (implemented once in the base class).** Every strategy agent monitors price for its open position, keeps a position state machine (FLAT → ENTERING → OPEN → EXITING → FLAT), sends orders only through the Risk Agent, logs every trade, supports export, and exposes a UI widget. It ignores duplicate signals on the same candle and refuses new entries after the intraday cut-off time.

**Pipeline inputs chosen by the user:** segment (Option / Future / Stock), product (Intraday / Delivery-Overnight), target and stop-loss (points or %, based on the traded instrument's price), quantity (lots for F&O, capital for stock), execution mode (Paper / Live), and behaviour on an opposite signal (`exit`, `reverse` or `ignore`).

**Trade log fields (one row per round trip):**

| Field | Field | Field |
|-------|-------|-------|
| Pipeline ID / strategy name | Instrument traded (symbol, strike, CE/PE, expiry) | Timeframe (tick, 1m, 5m, 1D) |
| Intraday / overnight | Entry time / exit time | Underlying price at entry / exit |
| Lot size / number of lots / quantity | Entry price / exit price | Exit reason (target, SL, signal, square-off, kill switch) |
| Gross P&L | Charges (itemised) | Net P&L |
| Correlation ID / broker order IDs | Paper or live | Indicator snapshot at entry |

**UI widget per strategy:** editable default parameters, trades closed today, current open trade with live MTM, gross P&L, charges, net P&L, and Pause / Stop / Export buttons.

**Strategy definitions (rewritten as unambiguous rules).** "Cross above" means `prev_macd <= prev_signal and macd > signal`. "Rising" means `macd > prev_macd`.

| Strategy | Segment | Entry rule | Exit rule |
|----------|---------|------------|-----------|
| MACD S1 — Crossover option | Option | MACD crosses above signal → buy ATM CE. MACD crosses below signal → buy ATM PE. | Target / SL on the **option premium** (e.g., entry premium + T / − S), placed via GTT or monitored by Paper agent. Opposite cross follows the `on_opposite_signal` setting. |
| MACD S2 — Momentum option | Option | MACD rising → buy ATM CE. MACD falling → buy ATM PE. | CE exits when MACD falls; PE exits when MACD rises. With `reverse`, the exit immediately opens the opposite side. Optional target/SL as a safety net. |
| MACD S3 — Momentum stock | Stock | MACD rising → buy stock with ₹1,00,000 capital (quantity = floor(capital ÷ price)). MACD falling → short (intraday only). | Long exits when MACD falls; short exits when MACD rises. In delivery mode the strategy is long-only, because cash-segment shorts cannot be carried overnight. |
| Ichimoku S1 — Cloud cross option | Option | Span A crosses above Span B → buy ATM CE. Span A crosses below Span B → buy ATM PE. | Target / SL on the option premium. `span_reference: projected | current` is configurable. |

**Futures.** If the user selects Future, the strategy trades the current-month future of the underlying instead of an ATM option. Target/SL are then in future-price points.

### Order agents

All order agents accept the same `OrderRequest` and emit `OrderEvent`s. Every request carries `origin_agent_id`, `pipeline_id` and a unique `correlation_id`, and the order agent routes every event back to exactly that origin. The correlation ID is also written into the broker order tag so positions can be re-attached to pipelines after a restart.

**Normal Order Agent.** Places market (or limit) orders and returns the fill confirmation: status, average fill price, filled quantity, broker order ID and timestamp. Handles partial fills and rejections. For index options, orders above the exchange freeze quantity are split into multiple slices.

**GTT Order Agent.** Receives target and stop-loss as fixed numbers (points) from the strategy. It places the entry at market, reads the actual fill price, computes absolute target = fill + T and stop-loss = fill − S (inverted for shorts), and places a broker-side GTT with target and stop-loss legs. It then monitors the order-update stream and reports `TARGET_HIT`, `SL_HIT` or `CANCELLED` to the origin agent, cancelling the remaining leg if the broker does not do so automatically. Verify against the current Upstox GTT documentation which leg combinations (entry + target + stop-loss, trailing stop) are supported; if a combination is not supported, fall back to market entry followed by a two-leg GTT.

**Paper Order Agent (NEW).** Implements the same interface with simulated fills at LTP plus a configurable slippage, and simulates target/SL legs from live ticks. This is how "order agent works only if live" is satisfied cleanly: in Paper mode the pipeline uses this agent automatically, and the live agents refuse to run unless the pipeline is armed for Live.

**Live mode arming.** Switching a pipeline to Live requires a confirmation dialog, a valid token, the Risk Agent enabled, and a passed broker reconciliation.

### Safety and support agents

**Risk Agent.** Approves or rejects every `OrderRequest`. Checks include maximum daily loss (global and per pipeline), maximum trades per day, maximum open positions, maximum lots/capital per order, order-rate limiting, duplicate-order guard, and trading-hours check. A breach of the daily loss limit triggers square-off of everything and blocks new entries. The console's **Kill Switch** cancels all pending orders and GTTs, squares off all positions, and stops all strategies.

**Square-off Scheduler.** Two configurable times: `intraday_no_new_entries` (e.g., 15:00) and `intraday_squareoff` (e.g., 15:15, set before the broker's own auto square-off). At square-off it cancels open GTTs first, exits all intraday positions, then verifies with the positions API that everything is flat. Overnight pipelines are untouched.

**Charges Calculator.** Rates live in `config/charges.yaml` with an `effective_from` date, because statutory rates change. Charges are computed per leg and itemised in the trade log.

**Health / Watchdog Agent.** Monitors WebSocket heartbeat and reconnects with exponential back-off, resubscribing all active instruments. If no tick arrives for a configurable time during market hours, it pauses new entries and alerts. It also detects token expiry and checks that the system clock is in sync (all timestamps in IST).

**Persistence & Workspace Agent.** The full setup (pipelines, parameters, execution modes) is saved as `workspaces/<name>.yaml` and auto-loaded next day. On every start, it reconciles open positions and GTTs with Upstox and re-attaches them to their pipelines using the order tag.

**Export Agent.** Every agent implements `export_frames()`, which returns named DataFrames. The console's **Download All** button produces one ZIP containing an Excel workbook (sheets for trades, orders, indicator values and candles per agent) plus ticks as Parquet/CSV. Ticks are kept out of Excel because a worksheet holds a maximum of 1,048,576 rows, which a liquid instrument can exceed in a single day. Each widget also has its own Export button.

