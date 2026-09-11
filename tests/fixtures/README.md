# Test fixtures

Small, offline samples. No test may touch the network or the runtime folder.

| File | What it is |
|------|------------|
| `nifty_1m_2026-09-10.parquet` | 375 one-minute candles — one full session |
| `nifty_5m_2026-09-10.parquet` | the same session aggregated to 5 minutes |
| `nifty_atm_ce_ticks_2026-09-10.parquet` | 600 option ticks with cumulative day volume |
| `broker/instruments_sample.json` | one record of each instrument shape (index, equity, option, future) |
| `broker/order_details_sample.json` | an order-details response |
| `broker/market_feed_sample.json` | a normalised market-feed message |

Load them with `tests.fixtures.load(name)`.
