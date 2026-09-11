---
name: new-order-agent
description: Scaffold and implement a new order agent.
argument-hint: <name> <behaviour description>
disable-model-invocation: true
---
Create an order agent. Request: $ARGUMENTS

Read only: `agents/orders/CLAUDE.md`, `core/contracts.py`, `core/order_base.py`, the public interface of `broker/rest.py`, `agents/orders/_template/`.

1. Check the current official Upstox docs for every endpoint used; cite the URL in comments.
2. Run `python -m tools.new_agent order <name>`; implement `place()` and any `cancel`/`on_broker_update`.
3. Enforce: LIVE-armed only, correlation ID in order tag, freeze-quantity slicing, no blind retries.
4. Tests with a mocked broker: fill, partial, reject, and every terminal status the agent can emit.
5. Tests + ruff pass; commit on branch `agent/<name>`.
