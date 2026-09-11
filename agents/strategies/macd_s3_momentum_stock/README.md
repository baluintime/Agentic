# MACD S3 — momentum stock

| | Rule |
|--|------|
| Segment | Stock (the selected instrument itself) |
| Entry long | `macd > prev_macd + min_change` → buy `floor(capital / price)` shares |
| Entry short | `macd < prev_macd − min_change` → short the same quantity, **intraday only** |
| Exit | A long exits when MACD falls, a short when MACD rises |
| Delivery mode | Long-only: cash-segment shorts cannot be held overnight, so short signals are skipped |

Parameters: `capital` (₹1,00,000 default), `min_change`, `on_opposite_signal`,
optional `target_points` / `stoploss_points` in rupees per share.

Requires the `macd` indicator. Decisions are taken once per closed candle.
