"""Runtime storage: SQLite for records, Parquet for market data.

Everything lives under $UPSTOX_RUNTIME_DIR (default ~/upstox_runtime), never
inside the repository.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orders (
    correlation_id TEXT, origin_agent_id TEXT, pipeline_id TEXT, instrument_key TEXT,
    side TEXT, quantity INTEGER, status TEXT, fill_price REAL, filled_qty INTEGER,
    broker_order_id TEXT, ts TEXT, message TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS orders_corr ON orders(correlation_id);
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY, pipeline_id TEXT, strategy TEXT, session_date TEXT,
    payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS trades_session ON trades(session_date);
CREATE TABLE IF NOT EXISTS agent_state (
    agent_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def runtime_dir() -> Path:
    raw = os.environ.get("UPSTOX_RUNTIME_DIR", "~/upstox_runtime")
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _encode(value: Any) -> str:
    def default(obj: Any) -> Any:
        if isinstance(obj, datetime | date):
            return obj.isoformat()
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)
        return str(obj)

    return json.dumps(value, default=default)


class Store:
    """Thin persistence facade handed to every agent."""

    def __init__(self, base: Path | None = None) -> None:
        self.base = Path(base) if base else runtime_dir()
        self.base.mkdir(parents=True, exist_ok=True)
        for sub in ("ticks", "candles", "exports", "workspaces", "cache", "logs"):
            (self.base / sub).mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.base / "upstox_agents.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # -- paths ---------------------------------------------------------------
    def tick_path(self, instrument_key: str, day: date) -> Path:
        return self.base / "ticks" / f"{_safe(instrument_key)}_{day.isoformat()}.parquet"

    def candle_path(self, instrument_key: str, timeframe: str, day: date) -> Path:
        name = f"{_safe(instrument_key)}_{timeframe}_{day.isoformat()}.parquet"
        return self.base / "candles" / name

    def history_path(self, instrument_key: str, timeframe: str, day: date) -> Path:
        name = f"{_safe(instrument_key)}_{timeframe}_hist_{day.isoformat()}.parquet"
        return self.base / "cache" / name

    @property
    def workspaces(self) -> Path:
        return self.base / "workspaces"

    @property
    def exports(self) -> Path:
        return self.base / "exports"

    # -- key/value -----------------------------------------------------------
    def put(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO kv(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, _encode(value), datetime.now().isoformat()),
        )
        self.db.commit()

    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    # -- records -------------------------------------------------------------
    def record_order(self, event: Any, request: Any = None) -> None:
        req = request
        self.db.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                getattr(event, "correlation_id", None),
                getattr(event, "origin_agent_id", None),
                getattr(req, "pipeline_id", None),
                getattr(req, "instrument_key", None),
                getattr(getattr(req, "side", None), "value", None),
                getattr(req, "quantity", None),
                getattr(event, "status", None),
                getattr(event, "fill_price", None),
                getattr(event, "filled_qty", None),
                getattr(event, "broker_order_id", None),
                getattr(event, "ts", datetime.now()).isoformat(),
                getattr(event, "message", ""),
                _encode(getattr(event, "meta", {})),
            ),
        )
        self.db.commit()

    def record_trade(self, trade: Any) -> None:
        payload = asdict(trade) if is_dataclass(trade) and not isinstance(trade, type) else trade
        self.db.execute(
            "INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?)",
            (
                str(payload.get("trade_id")),
                payload.get("pipeline_id"),
                payload.get("strategy"),
                str(payload.get("session_date") or ""),
                _encode(payload),
            ),
        )
        self.db.commit()

    def trades(self, session_date: date | None = None) -> list[dict]:
        if session_date:
            rows = self.db.execute(
                "SELECT payload FROM trades WHERE session_date=?", (session_date.isoformat(),)
            ).fetchall()
        else:
            rows = self.db.execute("SELECT payload FROM trades").fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def save_agent_state(self, agent_id: str, payload: dict) -> None:
        self.db.execute(
            "INSERT INTO agent_state VALUES (?,?,?) ON CONFLICT(agent_id) DO UPDATE SET "
            "payload=excluded.payload, updated_at=excluded.updated_at",
            (agent_id, _encode(payload), datetime.now().isoformat()),
        )
        self.db.commit()

    def load_agent_state(self, agent_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT payload FROM agent_state WHERE agent_id=?", (agent_id,)
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def close(self) -> None:
        self.db.close()


def _safe(key: str) -> str:
    return key.replace("|", "_").replace("/", "_").replace(":", "_")
