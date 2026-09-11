"""Instrument Master: the single source of truth for symbols, lots and strikes.

Docs: https://upstox.com/developer/api-documentation/instruments/
The complete instrument file is downloaded once each morning and cached. Lot
sizes, strike steps, freeze quantities and expiries change over time, so nothing
here is ever hardcoded — every answer comes from the file.
"""

from __future__ import annotations

import gzip
import io
import json
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from core import clock
from core.contracts import Instrument, OptionType, Segment
from core.rest_urls import INSTRUMENTS_URL

TRADABLE_TYPES = ("EQ", "INDEX")

COLUMNS = [
    "instrument_key",
    "trading_symbol",
    "name",
    "exchange",
    "segment",
    "instrument_type",
    "lot_size",
    "tick_size",
    "freeze_quantity",
    "expiry",
    "strike_price",
    "underlying_key",
    "asset_symbol",
    "weekly",
]


class InstrumentMaster:
    """Implements `core.resolver.InstrumentResolver`."""

    def __init__(self, frame: pd.DataFrame | None = None, cache_dir: Path | None = None) -> None:
        self.frame = frame if frame is not None else pd.DataFrame(columns=COLUMNS)
        self.cache_dir = cache_dir
        self.loaded_on: date | None = None
        self.expiry_roll_after = time(13, 0)

    # -- loading -------------------------------------------------------------
    @classmethod
    def from_records(cls, records: list[dict[str, Any]], **kwargs: Any) -> InstrumentMaster:
        master = cls(**kwargs)
        master.frame = normalise(records)
        master.loaded_on = clock.now().date()
        return master

    async def load(self, client: httpx.AsyncClient | None = None, force: bool = False) -> int:
        """Download today's instrument file, or reuse today's cache."""
        today = clock.now().date()
        cached = self._cache_path(today)
        if not force and cached and cached.exists():
            self.frame = pd.read_parquet(cached)
            self.loaded_on = today
            return len(self.frame)
        owns = client is None
        client = client or httpx.AsyncClient(timeout=60.0)
        try:
            response = await client.get(INSTRUMENTS_URL)
            response.raise_for_status()
            raw = response.content
        finally:
            if owns:
                await client.aclose()
        records = json.load(gzip.GzipFile(fileobj=io.BytesIO(raw)))
        self.frame = normalise(records)
        self.loaded_on = today
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            self.frame.to_parquet(cached, index=False)
        return len(self.frame)

    def _cache_path(self, day: date) -> Path | None:
        return self.cache_dir / f"instruments_{day.isoformat()}.parquet" if self.cache_dir else None

    @property
    def empty(self) -> bool:
        return self.frame.empty

    # -- lookups -------------------------------------------------------------
    def row(self, instrument_key: str) -> pd.Series | None:
        hit = self.frame[self.frame["instrument_key"] == instrument_key]
        return None if hit.empty else hit.iloc[0]

    def lot_size(self, instrument_key: str) -> int:
        row = self.row(instrument_key)
        return int(row["lot_size"]) if row is not None and row["lot_size"] else 1

    def freeze_quantity(self, instrument_key: str) -> int | None:
        row = self.row(instrument_key)
        if row is None or pd.isna(row["freeze_quantity"]) or not row["freeze_quantity"]:
            return None
        return int(row["freeze_quantity"])

    def tradable(self) -> list[dict]:
        """Every underlying a pipeline may be built on, indices first then equities.

        This is what the console's instrument dropdown is built from, so it
        carries a ready-made `label` for each row.
        """
        if self.empty:
            return []
        frame = self.frame[self.frame["instrument_type"].isin(TRADABLE_TYPES)].copy()
        # One row per key: the dropdown is keyed by instrument_key, so a duplicate
        # would silently vanish from the list while still inflating the count.
        frame = frame.drop_duplicates(subset="instrument_key", keep="first")
        frame["_rank"] = (frame["instrument_type"] != "INDEX").astype(int)
        frame = frame.sort_values(["_rank", "trading_symbol"])
        rows = frame[
            ["instrument_key", "trading_symbol", "name", "segment", "instrument_type"]
        ].to_dict("records")
        for row in rows:
            name = row["name"]
            suffix = f" · {name}" if name and name != row["trading_symbol"] else ""
            row["label"] = f"{row['trading_symbol']}{suffix} ({row['segment']})"
        return rows

    def search(self, query: str, limit: int = 25, tradable_only: bool = True) -> list[dict]:
        """Powers the instrument picker in the console."""
        if self.empty or not query:
            return []
        frame = self.frame
        if tradable_only:
            frame = frame[frame["instrument_type"].isin(TRADABLE_TYPES)]
        needle = query.upper()
        mask = frame["trading_symbol"].str.upper().str.contains(needle, na=False, regex=False)
        mask |= frame["name"].str.upper().str.contains(needle, na=False, regex=False)
        hit = frame[mask].head(limit)
        return hit[
            ["instrument_key", "trading_symbol", "name", "segment", "instrument_type"]
        ].to_dict("records")

    def underlying_options(self, underlying_key: str) -> pd.DataFrame:
        return self.frame[
            (self.frame["underlying_key"] == underlying_key)
            & (self.frame["instrument_type"].isin(["CE", "PE"]))
        ]

    def underlying_futures(self, underlying_key: str) -> pd.DataFrame:
        return self.frame[
            (self.frame["underlying_key"] == underlying_key)
            & (self.frame["instrument_type"].isin(["FUT"]))
        ]

    # -- expiries ------------------------------------------------------------
    def expiries(self, underlying_key: str, kind: str = "option") -> list[date]:
        frame = (
            self.underlying_options(underlying_key)
            if kind == "option"
            else self.underlying_futures(underlying_key)
        )
        values = sorted({d for d in frame["expiry"].dropna().dt.date.tolist()})
        return values

    def pick_expiry(
        self,
        underlying_key: str,
        rule: str = "nearest_weekly",
        on_date: date | None = None,
        kind: str = "option",
    ) -> date | None:
        """Nearest weekly/monthly expiry, rolling on expiry day after the cut-off."""
        today = on_date or clock.now().date()
        candidates = [e for e in self.expiries(underlying_key, kind) if e >= today]
        if not candidates:
            return None
        if today == candidates[0] and clock.now().time() >= self.expiry_roll_after:
            candidates = candidates[1:] or candidates
        if rule == "nearest_monthly":
            monthly = [e for e in candidates if _is_last_expiry_of_month(e, candidates)]
            return monthly[0] if monthly else candidates[-1]
        return candidates[0]

    # -- strikes -------------------------------------------------------------
    def strike_step(self, underlying_key: str, expiry_rule: str = "nearest_weekly") -> float:
        expiry = self.pick_expiry(underlying_key, expiry_rule)
        frame = self.underlying_options(underlying_key)
        if expiry is not None:
            frame = frame[frame["expiry"].dt.date == expiry]
        strikes = sorted({float(s) for s in frame["strike_price"].dropna().tolist()})
        diffs = [round(b - a, 4) for a, b in zip(strikes, strikes[1:], strict=False) if b > a]
        if not diffs:
            raise LookupError(f"no strikes found for {underlying_key}")
        return float(Counter(diffs).most_common(1)[0][0])

    def atm_strike(self, spot: float, step: float) -> float:
        return round(spot / step) * step

    def option(
        self,
        underlying_key: str,
        spot: float,
        option_type: OptionType,
        expiry_rule: str = "nearest_weekly",
        offset: int = 0,
    ) -> Instrument:
        step = self.strike_step(underlying_key, expiry_rule)
        expiry = self.pick_expiry(underlying_key, expiry_rule)
        strike = self.atm_strike(spot, step) + offset * step
        frame = self.underlying_options(underlying_key)
        frame = frame[
            (frame["instrument_type"] == option_type.value)
            & (frame["strike_price"].round(4) == round(strike, 4))
        ]
        if expiry is not None:
            frame = frame[frame["expiry"].dt.date == expiry]
        if frame.empty:
            raise LookupError(
                f"no {option_type.value} at strike {strike:g} expiry {expiry} for {underlying_key}"
            )
        return _to_instrument(frame.iloc[0], Segment.OPTION, underlying_key)

    def strike_window(
        self, underlying_key: str, spot: float, window: int = 2, expiry_rule: str = "nearest_weekly"
    ) -> list[str]:
        """ATM +/- N strikes, CE and PE: exactly what the feed must subscribe to."""
        keys: list[str] = []
        for offset in range(-window, window + 1):
            for option_type in (OptionType.CE, OptionType.PE):
                try:
                    keys.append(
                        self.option(
                            underlying_key, spot, option_type, expiry_rule, offset
                        ).instrument_key
                    )
                except LookupError:
                    continue
        return keys

    # -- other segments ------------------------------------------------------
    def future(self, underlying_key: str, expiry_rule: str = "nearest_monthly") -> Instrument:
        expiry = self.pick_expiry(underlying_key, expiry_rule, kind="future")
        frame = self.underlying_futures(underlying_key)
        if expiry is not None:
            frame = frame[frame["expiry"].dt.date == expiry]
        if frame.empty:
            raise LookupError(f"no future found for {underlying_key}")
        return _to_instrument(frame.iloc[0], Segment.FUTURE, underlying_key)

    def stock(self, instrument_key: str) -> Instrument:
        row = self.row(instrument_key)
        if row is None:
            raise LookupError(f"unknown instrument {instrument_key}")
        return _to_instrument(row, Segment.STOCK, instrument_key)

    # -- resolver protocol ---------------------------------------------------
    def resolve(
        self,
        underlying_key: str,
        segment: Segment,
        *,
        spot: float,
        bullish: bool = True,
        expiry_rule: str = "nearest_weekly",
        on_date: date | None = None,
    ) -> Instrument:
        if segment is Segment.OPTION:
            option_type = OptionType.CE if bullish else OptionType.PE
            return self.option(underlying_key, spot, option_type, expiry_rule)
        if segment is Segment.FUTURE:
            return self.future(underlying_key)
        return self.stock(underlying_key)


# -- helpers -----------------------------------------------------------------
def normalise(records: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS)
    if "trading_symbol" not in frame and "tradingsymbol" in frame:
        frame["trading_symbol"] = frame["tradingsymbol"]
    for column in COLUMNS:
        if column not in frame:
            frame[column] = None
    frame["expiry"] = frame["expiry"].map(_to_datetime)
    frame["expiry"] = pd.to_datetime(frame["expiry"], errors="coerce")
    frame["strike_price"] = pd.to_numeric(frame["strike_price"], errors="coerce")
    frame["lot_size"] = pd.to_numeric(frame["lot_size"], errors="coerce").fillna(1).astype(int)
    frame["freeze_quantity"] = pd.to_numeric(frame["freeze_quantity"], errors="coerce")
    # The file reports tick size in paise (5.0 means 0.05).
    ticks = pd.to_numeric(frame["tick_size"], errors="coerce").fillna(5.0)
    frame["tick_size"] = (ticks / 100).where(ticks > 1, ticks)
    for column in ("trading_symbol", "name", "exchange", "segment", "instrument_type"):
        frame[column] = frame[column].astype("string").fillna("")
    frame["underlying_key"] = frame["underlying_key"].astype("string")
    return frame[COLUMNS].reset_index(drop=True)


def _to_datetime(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, int | float):  # epoch milliseconds
        return datetime.fromtimestamp(float(value) / 1000, tz=clock.IST).replace(tzinfo=None)
    return value


def _is_last_expiry_of_month(candidate: date, candidates: list[date]) -> bool:
    same_month = [e for e in candidates if (e.year, e.month) == (candidate.year, candidate.month)]
    return candidate == max(same_month)


def _to_instrument(row: pd.Series, segment: Segment, underlying_key: str) -> Instrument:
    option_type = None
    if row["instrument_type"] in ("CE", "PE"):
        option_type = OptionType(row["instrument_type"])
    expiry = row["expiry"]
    return Instrument(
        instrument_key=str(row["instrument_key"]),
        tradingsymbol=str(row["trading_symbol"] or row["name"]),
        segment=segment,
        lot_size=int(row["lot_size"] or 1),
        tick_size=float(row["tick_size"] or 0.05),
        freeze_quantity=None if pd.isna(row["freeze_quantity"]) else int(row["freeze_quantity"]),
        expiry=None if pd.isna(expiry) else pd.Timestamp(expiry).to_pydatetime(),
        strike=None if pd.isna(row["strike_price"]) else float(row["strike_price"]),
        option_type=option_type,
        underlying_key=underlying_key,
    )
