"""One row per round trip, as specified in docs/REQUIREMENTS.md."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd


@dataclass
class TradeRow:
    pipeline_id: str
    strategy: str
    instrument: str
    instrument_key: str
    segment: str
    timeframe: str
    product: str  # Intraday | Delivery-Overnight
    direction: str  # long | short
    entry_time: datetime | None = None
    exit_time: datetime | None = None
    underlying_entry: float | None = None
    underlying_exit: float | None = None
    lot_size: int = 1
    lots: int = 0
    quantity: int = 0
    entry_price: float | None = None
    exit_price: float | None = None
    exit_reason: str = ""  # target | stoploss | signal | squareoff | kill_switch | reverse
    gross_pnl: float = 0.0
    charges: float = 0.0
    charges_detail: dict[str, Any] = field(default_factory=dict)
    net_pnl: float = 0.0
    correlation_ids: list[str] = field(default_factory=list)
    broker_order_ids: list[str] = field(default_factory=list)
    mode: str = "paper"  # paper | live
    indicator_snapshot: dict[str, float] = field(default_factory=dict)
    session_date: date | None = None
    trade_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def finalise(self, charges: float, charges_detail: dict[str, Any]) -> TradeRow:
        self.charges = round(charges, 2)
        self.charges_detail = charges_detail
        self.net_pnl = round(self.gross_pnl - self.charges, 2)
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradeLog:
    """Closed round trips for one strategy agent, plus running totals."""

    def __init__(self) -> None:
        self.rows: list[TradeRow] = []

    def add(self, row: TradeRow) -> TradeRow:
        self.rows.append(row)
        return row

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def gross(self) -> float:
        return round(sum(r.gross_pnl for r in self.rows), 2)

    @property
    def charges(self) -> float:
        return round(sum(r.charges for r in self.rows), 2)

    @property
    def net(self) -> float:
        return round(sum(r.net_pnl for r in self.rows), 2)

    @property
    def wins(self) -> int:
        return sum(1 for r in self.rows if r.net_pnl > 0)

    def totals(self) -> dict[str, float]:
        return {
            "trades": len(self.rows),
            "wins": self.wins,
            "losses": len(self.rows) - self.wins,
            "gross": self.gross,
            "charges": self.charges,
            "net": self.net,
            "win_rate": round(100 * self.wins / len(self.rows), 1) if self.rows else 0.0,
        }

    def to_frame(self) -> pd.DataFrame:
        if not self.rows:
            return pd.DataFrame()
        frame = pd.DataFrame([r.to_dict() for r in self.rows])
        frame["charges_detail"] = frame["charges_detail"].map(
            lambda d: ", ".join(f"{k}={v}" for k, v in d.items() if k not in ("missing",))
        )
        frame["indicator_snapshot"] = frame["indicator_snapshot"].map(
            lambda d: ", ".join(f"{k}={round(v, 4)}" for k, v in d.items())
        )
        for col in ("correlation_ids", "broker_order_ids"):
            frame[col] = frame[col].map(lambda v: ", ".join(map(str, v)))
        return frame
