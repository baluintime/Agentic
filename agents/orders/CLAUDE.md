# agents/orders/ — order agents

Read only: this file, `core/contracts.py`, `core/order_base.py`, `broker/rest.py` interface, `_template/`, and the one order agent you are working on.

Contract
- Subclass `OrderAgent`; implement `async place(req: OrderRequest) -> OrderEvent` (+ `cancel`, `on_broker_update` if needed).
- Every event carries the request's `correlation_id` and `origin_agent_id`; the base class routes it to `order.event.<origin_agent_id>`.
- Write `correlation_id` into the broker order tag (respect the tag length limit).
- Live agents refuse unless the pipeline is armed LIVE. Never retry a placement without first checking whether the original order exists.
- Split orders above freeze quantity (from Instrument Master). Handle partial fills and rejections explicitly.

GTT agent: market entry → read actual fill price → target = fill + T, SL = fill − S (inverted for shorts) → place GTT legs as supported by current Upstox docs → report `TARGET_HIT` / `SL_HIT` / `CANCELLED` → cancel the remaining leg if the broker does not.

Tests: mocked broker covering fill, partial fill, rejection, target hit, SL hit, restart re-attach by tag.
