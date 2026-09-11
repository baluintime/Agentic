# core/ — framework (stable; edit only when the task explicitly says so)

- `contracts.py` is the public API between agents. Changing it affects every agent: propose first, then update `docs/CONTRACTS.md` in the same commit, bump `CONTRACTS_VERSION`, and run the full test suite.
- `bus.py`: async pub/sub. Topics: `tick.<instrument>`, `candle.<tf>.<instrument>`, `indicator.<pipeline>`, `signal.<pipeline>`, `order.request`, `order.event.<origin_agent>`, `system.*`. A slow or failing subscriber must never block or crash others (per-subscriber queue + exception isolation + logging).
- `clock.py`: `now()` returns aware IST datetimes; `SimClock` for replay. No other module calls `datetime.now()`.
- Base classes (`base_agent.py`, `indicator_base.py`, `strategy_base.py`, `order_base.py`) hold all shared behaviour so agent authors write only `compute()`, `decide()` or `place()`.
- `registry.py` discovers agents from `agents/*/*/manifest.yaml`; manifest fields: `name, kind, version, requires, description`.
- Keep modules small and fully typed; 100% of public functions have tests.
