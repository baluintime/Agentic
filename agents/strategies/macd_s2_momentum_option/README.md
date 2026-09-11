# MACD S2 — momentum option

| | Rule |
|--|------|
| Segment | Option (ATM of the selected expiry) |
| Entry long | `macd > prev_macd + min_change` → buy ATM **CE** |
| Entry short | `macd < prev_macd − min_change` → buy ATM **PE** |
| Exit | A CE is exited when MACD falls, a PE when MACD rises (the opposite signal) |
| Opposite signal | `reverse` by default: the exit immediately opens the other side |
| Safety net | Optional `target_points` / `stoploss_points` on the premium |

`min_change` is hysteresis. This strategy trades often, so check the estimated
round-trip charges in the widget before running it.

Requires the `macd` indicator. Decisions are taken once per closed candle.
