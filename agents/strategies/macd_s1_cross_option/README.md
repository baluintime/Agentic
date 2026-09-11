# MACD S1 — crossover option

| | Rule |
|--|------|
| Segment | Option (ATM of the selected expiry) |
| Entry long | `prev_macd <= prev_signal and macd > signal` → buy ATM **CE** |
| Entry short | `prev_macd >= prev_signal and macd < signal` → buy ATM **PE** |
| Exit | Target / stop-loss on the **option premium**, via GTT legs (live) or the Paper agent |
| Opposite signal | `on_opposite_signal`: `exit` (default), `reverse` or `ignore` |

Parameters: `target_points`, `stoploss_points`, `target_mode` (points/percent),
`lots`, `on_opposite_signal`, `evaluate_on` (default `close`).

Requires the `macd` indicator. Decisions are taken once per closed candle.
