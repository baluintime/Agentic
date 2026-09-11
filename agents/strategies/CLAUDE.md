# agents/strategies/ — strategy agents

Read only: this file, `core/contracts.py`, `core/strategy_base.py`, `_template/`, the required indicator's `outputs`, and the one strategy you are working on.

Contract
- Subclass `StrategyAgent`; set `name`, `Config`, `requires` (e.g. `["macd", "segment:option"]`).
- Implement only `decide(result: IndicatorResult, pos: Position) -> Action | None`. Pure logic: no orders, no sizing, no I/O.
- Base class maps `ENTER_LONG`/`ENTER_SHORT` to ATM CE/PE (option), current-month future, or stock; sizes by lots/capital; sends via Risk Agent; tracks position state, trade log, charges, P&L; honours square-off, pause and kill switch; handles `on_opposite_signal` (exit | reverse | ignore).
- Standard Config fields: `target_points`, `stoploss_points`, `target_mode` (points|percent), `lots` or `capital`, `on_opposite_signal`, `evaluate_on` (close|every_tick).

Definitions
- Cross above: `prev_a <= prev_b and a > b`. Cross below: `prev_a >= prev_b and a < b`.
- Rising: `a > prev_a`. Target/SL for options are on the option premium, not the underlying.
- Delivery-mode stock strategies are long-only (no overnight cash shorts).

Tests (required): a table-driven test for every entry/exit rule, one test for duplicate-signal suppression on the same candle, one for the opposite-signal setting.
