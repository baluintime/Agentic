# agents/indicators/ — indicator agents

Read only: this file, `core/contracts.py`, `core/indicator_base.py`, `_template/`, and the one indicator you are working on.

Contract
- Subclass `IndicatorAgent`; set `name`, `Config` (pydantic, drives the UI form), `outputs` (list of column names).
- Implement `warmup_bars() -> int` and `compute(df) -> df`. `compute` is pure: no I/O, no state, no bus access; it adds output columns to an OHLCV DataFrame indexed by candle start time.
- The base class handles subscription, closed-candle evaluation, `prev_` values, publishing `IndicatorResult`, and export.
- Defaults live in `config/<name>.yaml`; the agent folder's `config.yaml` holds overrides only.

Tests (required)
- Compare against hand-checked values on a fixture of 200–500 candles.
- Check warm-up: rows before `warmup_bars` must be NaN or excluded.
- Check no look-ahead: value at bar t must not change when bars after t are appended.

Pitfalls: Ichimoku spans are displaced 26 bars — publish both `*_projected` and `*_current`. MACD needs ≥ 3×slow+signal bars.
