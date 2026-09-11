# ui/ — NiceGUI console

- The UI is a client of the bus and the registry; it must never contain trading logic and must never block the event loop.
- Agent widgets are generated from each agent's `Config` model and `status()` dict — do not hand-build per-agent forms.
- Header: token status, user name, market clock, total gross/charges/net, Kill Switch (confirm dialog), Download All.
- Pipeline builder stepper: instrument → segment → product → timeframe → indicator → strategy → order agent → parameters → Paper/Live. Only offer combinations allowed by each agent's `requires`.
- Live arming needs an explicit confirmation dialog.
- Spec: `docs/ARCHITECTURE.md` → "Main console (UI)".
