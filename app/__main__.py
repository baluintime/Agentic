"""Run the console: `python -m app` -> http://localhost:8080"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from nicegui import app as nicegui_app
from nicegui import ui

from app.engine import Engine
from ui import console

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")


def main() -> None:
    load_dotenv()
    engine = Engine()
    console.build(engine)

    @nicegui_app.on_startup
    async def _startup() -> None:
        await engine.start()
        report = await engine.connect()
        log.info("startup: %s", {k: v for k, v in report.items() if k != "token"})

    @nicegui_app.on_shutdown
    async def _shutdown() -> None:
        await engine.stop()

    ui.run(
        title="Upstox Agents",
        port=int(os.environ.get("CONSOLE_PORT", 8080)),
        reload=False,
        show=False,
        favicon="📈",
    )


main()
