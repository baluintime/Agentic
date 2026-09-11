# MACD indicator

Moving Average Convergence Divergence on closed candles.

| Output | Formula |
|--------|---------|
| `macd` | EMA(fast) − EMA(slow) of the source price |
| `signal` | EMA(signal) of `macd` |
| `histogram` | `macd` − `signal` |

Parameters (`config/macd.yaml`): `fast` 12, `slow` 26, `signal` 9, `source` close.

Warm-up: `3 × slow + signal` bars (117 with the defaults) — EMAs are recursive,
so early values depend on where the series starts.

Every `IndicatorResult` carries `macd`, `signal`, `histogram` plus the candle
OHLC, and the same set as `prev_values`. Strategies define "cross above" as
`prev_macd <= prev_signal and macd > signal`, and "rising" as `macd > prev_macd`.
