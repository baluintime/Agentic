# broker/ — Upstox adapters

- Only this folder talks to Upstox. Agents never call Upstox directly.
- Before writing or changing any Upstox call, check the current official Upstox API docs for the endpoint, payload and limits; do not rely on memory. Note the doc URL in a code comment.
- `auth.py`: reads `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_REDIRECT_URI` from env; token file in `$UPSTOX_RUNTIME_DIR` with 0600 permissions; never log the token. No headless scraping of the login page.
- `instruments.py`: daily download + cache of the instrument file; functions for ATM strike, strike step, lot size, freeze quantity, expiries, current-month future.
- `ws_feed.py`: exactly one market-data WebSocket; ref-counted subscriptions; exponential back-off reconnect with resubscribe; publishes `Tick` on `tick.<instrument>`.
- `rest.py`: rate-limited client (token bucket), retries only on idempotent calls, never retries order placement blindly.
- `paper.py`: same interface as the live order path; fills at LTP ± configurable slippage.
- Tests use recorded JSON/protobuf samples in `tests/fixtures/broker/`; no network in tests.
