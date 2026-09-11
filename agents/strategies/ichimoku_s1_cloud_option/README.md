# Ichimoku S1 — cloud cross option

| | Rule |
|--|------|
| Segment | Option (ATM of the selected expiry) |
| Entry long | `prev_span_a <= prev_span_b and span_a > span_b` → buy ATM **CE** |
| Entry short | `prev_span_a >= prev_span_b and span_a < span_b` → buy ATM **PE** |
| Exit | Target / stop-loss on the **option premium** |
| Opposite signal | `on_opposite_signal`: `exit` (default), `reverse` or `ignore` |

`span_reference` picks which cloud the rule reads:
`projected` (Kumo twist, computed from today's bars) or `current`
(the cloud under today's price, computed 26 bars ago).

Requires the `ichimoku` indicator. Decisions are taken once per closed candle.
