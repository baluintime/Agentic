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

2026-09-11 — Added `order.approved` to the bus topics — the Risk Agent must sit between the strategy and the order agents, so it consumes `order.request` and republishes approved requests; order agents subscribe to `order.approved` only.
2026-09-11 — `OrderEvent` gained a `meta` dict — GTT ids, order-id lists and slice sizes have to reach the origin agent without widening the fixed fields.
2026-09-11 — Broker-side target/SL legs report on the **entry** correlation id — a GTT leg is not a separate strategy order, and routing it to the entry keeps one round trip to one id.
2026-09-11 — Target and stop-loss are computed from the actual fill price, in the order agent — points are meaningless until the fill is known, and percent mode would otherwise be applied to the wrong base. The request carries `target_mode`/`target_value` in `meta` for this.
2026-09-11 — `on_opposite_signal: reverse` waits for the exit fill before entering the other side — entering immediately overwrote a position that was still closing, losing the round trip.
2026-09-11 — An `IndicatorResult` is published only when the current **and** previous values are all real numbers — a NaN previous value makes every "crossed above" comparison silently False.
2026-09-11 — Ichimoku publishes `chikou` (today's close) and `chikou_reference` (the close 26 bars ago) instead of a forward-shifted lagging span — a forward shift would be look-ahead.
2026-09-11 — 5-minute candles are aggregated from **closed** 1-minute candles, not from ticks directly — one back-fill of the 1-minute series then repairs both timeframes.
2026-09-11 — Timer loops (candle close, square-off, health) sleep on real time and take their decisions from `core.clock` — sleeping on a `SimClock` would spin the loop during replay.
2026-09-11 — `config/charges.yaml` is shipped empty; the calculator reports `complete: false` and zero — inventing statutory rates would put plausible but wrong numbers in the trade log.
2026-09-11 — The v3 market-data feed's protobuf decoder is not included: `UpstoxFeedClient` takes a `decode` callable — the schema must be generated from Upstox's published proto, and guessing it would produce silently wrong ticks. Everything above the transport is tested against normalised fixtures.
2026-09-11 — Upstox endpoint paths live in one `Endpoints` dataclass in `broker/rest.py` — the docs site is not reachable from the build environment, so a doc change is a one-line edit rather than a hunt.
2026-09-11 — Tests block real sockets (`tests/conftest.py`) and redirect `UPSTOX_RUNTIME_DIR` — "tests are offline" is enforced rather than assumed.
2026-09-11 — Live arming also requires the paper-first promotion rule to pass — the Analytics agent counts distinct paper sessions per strategy.
2026-09-11 — The Square-off agent skips the positions check when the token is invalid, and retries the verification on a later tick once the broker is reachable — starting the console after 15:15 while logged out produced a 401 and an ERROR line for a check that could never have succeeded. `flat_verified: null` now means "not checked", which is not the same as "flat".
2026-09-11 — `Engine.add_pipeline` catches a new strategy up on the session state it was not alive to hear (square-off cut-off, risk block, kill switch) — a pipeline added after the broadcast would otherwise start free to take entries.
2026-09-11 — The instrument file is downloaded at startup whether or not the token is valid — it is a public asset needing no Authorization header, and gating it behind the daily login left the pipeline builder with an empty instrument list before login.
2026-09-11 — The console picks the instrument from one searchable dropdown (`ui.select(with_input=True)`) over `InstrumentMaster.tradable()`, replacing type-then-Enter-then-click-a-result — typing alone set nothing, so Create kept asking for an instrument. `tradable()` dedupes by instrument key.
2026-09-16 — The console now serves the OAuth callback and has a Login button: `AuthManager` had `login_url()` and `exchange_code()` from the start, but nothing called them, so there was no way to connect from the UI. The callback route is derived from `UPSTOX_REDIRECT_URI` rather than hardcoded, because Upstox only redirects to the URI registered on the app.
2026-09-16 — A paste-the-code fallback sits in the same dialog — anyone whose registered redirect URI is not this console (a public host, a different port) can still finish the login.
2026-09-16 — Header elements are built per client with a closure refresh instead of being stored on the `Console` instance — shared attributes meant the newest browser tab's widgets replaced the previous tab's, and older tabs stopped updating. The same pattern still applies to the pipelines page, which is only refreshed for the most recently built tab.
2026-09-16 — The engine attaches a real `UpstoxFeedClient` to the hub as soon as the token validates: the hub was built with `client=None` and its connect loop idled forever, so `connected` stayed False and no tick ever reached a candle agent. Nothing downstream could work.
2026-09-16 — `upstox-python-sdk` is a dependency purely for its generated `MarketDataFeedV3_pb2`: the v3 feed is protobuf, and decoding it against a hand-written schema would produce silently wrong prices. `decode_feed()` maps it to the hub's normalised shape and is tested against frames built with that same schema.
2026-09-16 — The feed subscribes in `full` mode rather than `ltpc`: `ltpc` carries no volume, and the candle agents derive per-candle volume from the delta of the day's cumulative volume (`vtt`).
2026-09-16 — v3 connects straight to the feed URL with a Bearer header; the v2-style `feed_authorize()` redirect call is not used, and asking for it first would have failed the connection.
2026-09-16 — The console shows a Live prices panel (subscribed instruments, last price, tick age) with pipeline underlyings first — without it there was no way to tell a working pipeline from a dead feed, which is exactly what went unnoticed.
2026-09-16 — The hub closes the feed socket in a `finally` on every reconnect: it previously opened a new connection per retry without closing the old one, so a handful of retries exhausted the broker's per-user connection cap and every further attempt came back 403. The visible symptom (403) was two steps removed from the cause (a leak).
2026-09-16 — A `FeedUnavailable` error stops the reconnect loop instead of retrying: a missing decoder package or an absent token cannot be fixed by trying again, and retrying burned a connection each time. The reason is recorded on the hub and shown in the Live prices panel.
2026-09-16 — The decoder dependency is checked before the socket is opened, not on the first frame — failing after a successful connect is what leaked connections in the first place.
2026-09-18 — The Risk Agent counts open positions from `OrderEvent`s rather than from approved requests: a target or stop-loss leg closes a position on the *entry's* correlation id and never sends an exit request, so request-counting leaked one slot per target/SL exit until `max_open_positions` blocked every new entry permanently. A rejected entry leaked a slot the same way.
2026-09-18 — One open entry per pipeline is now the representation (`open_entries: pipeline -> entry correlation`), which matches the documented "one position per pipeline" rule and makes the count self-correcting.
2026-09-18 — `Engine.sync_risk_positions()` reconciles that count against what the strategies actually hold, and runs whenever a pipeline is added or removed, so any future drift self-heals instead of silently blocking trading.
2026-09-18 — Risk rejections name the setting and the file (`max_lots_per_order=5 in config/risk.yaml`) — the old messages stated a limit without saying where to change it.
2026-09-23 — Parameter forms decide int-ness from the field's annotation, never from the default value's Python type: `target_points: float = Field(20, ...)` has an int literal as its default, so the form truncated every decimal the user typed. A 0.5 target became 0.0, and since 0 means "no target", the position then ran with no target leg at all — a silent failure, not a rejected input.
2026-09-23 — `form_values()` no longer rounds anything silently. A value the model rejects raises, and the builder names the offending parameter, so a bad number is refused rather than quietly changed. (Integer inputs still round in the browser, visibly, as any number input does; the Python guard covers workspace files and other non-UI callers.)
2026-09-23 — GTT trigger prices are snapped to the instrument's tick size, which the strategy now passes in the order meta: a fractional target (0.33 on a 0.05-tick option) lands between ticks and the exchange rejects it. Enabling decimal targets made this reachable.
2026-09-24 — A frame the decoder cannot read is skipped rather than allowed to tear down the feed: an exception inside the message loop propagated out of `messages()`, dropped the socket and forced a reconnect, so one unrecognised frame type could look like a flaky connection.
2026-09-24 — The feed sets explicit `ping_interval`/`ping_timeout`/`close_timeout`, and disconnects are logged with the WebSocket close code, the reason and how long the connection had been held. `str(exc)` on a closed connection is usually empty or "no close frame received or sent", which is undiagnosable.
2026-09-24 — Every export writes a `summary` sheet of all agent statuses, so an export taken before any trade is a readable file rather than an empty archive, and the console reports the saved path whether or not the browser download succeeds — a blocked download looked exactly like a broken export.
