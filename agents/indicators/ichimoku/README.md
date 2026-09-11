# Ichimoku indicator

| Output | Formula |
|--------|---------|
| `tenkan` | (highest high + lowest low) / 2 over 9 bars |
| `kijun` | (highest high + lowest low) / 2 over 26 bars |
| `span_a_projected` | (tenkan + kijun) / 2 — plotted 26 bars ahead |
| `span_b_projected` | (highest high + lowest low) / 2 over 52 bars — plotted 26 ahead |
| `span_a_current` | `span_a_projected` shifted back 26 bars: the cloud under today's price |
| `span_b_current` | `span_b_projected` shifted back 26 bars |
| `chikou` | today's close (the lagging span's value) |
| `chikou_reference` | close 26 bars ago — the price where the lagging span is plotted |

Parameters (`config/ichimoku.yaml`): `tenkan` 9, `kijun` 26, `senkou_b` 52,
`displacement` 26. Warm-up: `senkou_b + displacement` = 78 bars.

Strategies choose which cloud to read through `span_reference: projected | current`.
