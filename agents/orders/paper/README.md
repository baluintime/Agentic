# Paper Order Agent

Same `OrderRequest` / `OrderEvent` contract as the live agents, with no network.

* Entry and exit fill at the instrument's last traded price plus slippage,
  always applied against the trader (`slippage_points` + `slippage_percent`).
* Target and stop-loss legs are armed from the actual fill price and evaluated on
  every tick; a hit emits `TARGET_HIT` or `SL_HIT` on the entry's correlation id,
  exactly like a broker-side GTT leg.
* `system.squareoff.start` and `system.kill` close every open leg as `SQUARED_OFF`.
* Refuses any request whose pipeline is armed LIVE — live orders belong to the
  Normal or GTT agent.

Paper mode selects this agent automatically, whichever order agent the pipeline names.
