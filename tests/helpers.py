"""Shared offline test helpers: no network, no broker, no files outside the repo."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pandas as pd

from core import clock
from core.contracts import (
    Candle,
    IndicatorResult,
    Instrument,
    OrderRequest,
    Segment,
    Side,
    Timeframe,
)

SESSION_START = clock.combine(datetime(2026, 9, 11).date(), clock.MARKET_OPEN)


def frame_from_closes(
    closes: list[float], start: datetime | None = None, minutes: int = 5
) -> pd.DataFrame:
    start = start or SESSION_START
    index = [start + timedelta(minutes=minutes * i) for i in range(len(closes))]
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [100] * len(closes),
        },
        index=pd.DatetimeIndex(index, name="start"),
    )
    return frame


def ramp_frame(n: int, start_price: float = 100.0, step: float = 1.0) -> pd.DataFrame:
    return frame_from_closes([start_price + step * i for i in range(n)])


def wave_frame(n: int, base: float = 100.0, amplitude: float = 10.0, period: int = 40):
    closes = [base + amplitude * math.sin(2 * math.pi * i / period) for i in range(n)]
    return frame_from_closes(closes)


def make_candle(
    close: float = 100.0, start: datetime | None = None, timeframe: Timeframe = Timeframe.M5
) -> Candle:
    start = start or SESSION_START
    return Candle(
        "NSE_INDEX|Nifty 50", timeframe, start, close, close + 1, close - 1, close, 100, True
    )


def make_result(
    values: dict,
    prev_values: dict,
    pipeline_id: str = "p1",
    indicator: str = "macd",
    start: datetime | None = None,
    close: float = 24_873.0,
) -> IndicatorResult:
    start = start or SESSION_START
    return IndicatorResult(
        pipeline_id=pipeline_id,
        indicator=indicator,
        timeframe=Timeframe.M5,
        candle=make_candle(close, start),
        prev_candle=make_candle(close, start - timedelta(minutes=5)),
        values={**values, "close": close, "open": close, "high": close, "low": close},
        prev_values={**prev_values, "close": close, "open": close, "high": close, "low": close},
    )


def make_instrument(
    key: str = "NSE_FO|CE24850",
    lot_size: int = 75,
    segment: Segment = Segment.OPTION,
    freeze: int | None = 1800,
) -> Instrument:
    return Instrument(
        instrument_key=key,
        tradingsymbol="NIFTY 24850 CE",
        segment=segment,
        lot_size=lot_size,
        freeze_quantity=freeze,
        strike=24850.0,
    )


def make_request(**overrides) -> OrderRequest:
    values = dict(
        correlation_id="corr0001",
        origin_agent_id="strategy-1",
        pipeline_id="p1",
        instrument_key="NSE_FO|CE24850",
        side=Side.BUY,
        quantity=75,
        product="I",
        target_points=20.0,
        stoploss_points=10.0,
        meta={
            "order_agent": "paper",
            "exec_mode": "paper",
            "segment": "option",
            "purpose": "entry",
            "lot_size": 75,
            "freeze_quantity": 1800,
        },
    )
    values.update(overrides)
    return OrderRequest(**values)


class FakeRest:
    """Mocked broker: scripted order lifecycles, no network."""

    def __init__(self, fills: list[dict] | None = None, order_ids: list[str] | None = None) -> None:
        self.fills = fills or []
        self.order_ids = order_ids or ["OID-1", "OID-2", "OID-3", "OID-4"]
        self.placed: list[dict] = []
        self.gtts: list[dict] = []
        self.cancelled_gtts: list[str] = []
        self.cancelled_orders: list[str] = []
        self.place_error: Exception | None = None
        self.gtt_error: Exception | None = None
        self._detail_calls = 0

    async def place_order(self, payload: dict) -> dict:
        if self.place_error:
            raise self.place_error
        self.placed.append(payload)
        return {"order_id": self.order_ids[min(len(self.placed) - 1, len(self.order_ids) - 1)]}

    async def order_details(self, order_id: str) -> dict:
        index = min(self._detail_calls, len(self.fills) - 1) if self.fills else 0
        self._detail_calls += 1
        return self.fills[index] if self.fills else {"status": "complete"}

    async def cancel_order(self, order_id: str) -> dict:
        self.cancelled_orders.append(order_id)
        return {"order_id": order_id}

    async def place_gtt(self, payload: dict) -> dict:
        if self.gtt_error:
            raise self.gtt_error
        self.gtts.append(payload)
        return {"gtt_order_id": f"GTT-{len(self.gtts)}"}

    async def cancel_gtt(self, gtt_order_id: str) -> dict:
        self.cancelled_gtts.append(gtt_order_id)
        return {"gtt_order_id": gtt_order_id}

    async def gtt_orders(self) -> list[dict]:
        return list(self.gtts)

    async def positions(self) -> list[dict]:
        return []

    async def profile(self) -> dict:
        return {"user_name": "Test User", "user_id": "T1", "email": "t@example.com"}

    async def historical_candles(self, key, unit, interval, to_date, from_date) -> list:
        return []

    async def intraday_candles(self, key, unit="minutes", interval=1) -> list:
        return []

    async def feed_authorize(self) -> str:
        return ""


def fill(status: str = "complete", quantity: int = 75, price: float = 100.0, **extra) -> dict:
    return {
        "status": status,
        "filled_quantity": quantity,
        "average_price": price,
        **extra,
    }


class Collector:
    """Records everything published on a topic pattern."""

    def __init__(self, bus, pattern: str) -> None:
        self.messages: list = []
        bus.subscribe(pattern, self.messages.append, "collector")

    def of_status(self, status: str) -> list:
        return [m for m in self.messages if getattr(m, "status", None) == status]

    @property
    def last(self):
        return self.messages[-1] if self.messages else None


NIFTY_KEY = "NSE_INDEX|Nifty 50"


def nifty_records(strikes: range = range(24_500, 25_301, 50)) -> list[dict]:
    """A small synthetic instrument file: the index, its weekly options and a future."""
    weeklies = [
        int(datetime(2026, 9, day, 15, 30, tzinfo=clock.IST).timestamp() * 1000)
        for day in (17, 24, 29)
    ]
    monthly = weeklies[-1]
    records = [
        {
            "instrument_key": NIFTY_KEY,
            "trading_symbol": "Nifty 50",
            "name": "Nifty 50",
            "exchange": "NSE",
            "segment": "NSE_INDEX",
            "instrument_type": "INDEX",
            "lot_size": 1,
            "tick_size": 5.0,
        },
        {
            "instrument_key": "NSE_EQ|INE002A01018",
            "trading_symbol": "RELIANCE",
            "name": "Reliance Industries",
            "exchange": "NSE",
            "segment": "NSE_EQ",
            "instrument_type": "EQ",
            "lot_size": 1,
            "tick_size": 5.0,
        },
        {
            "instrument_key": "NSE_FO|NIFTYFUT",
            "trading_symbol": "NIFTY FUT 29 SEP 26",
            "name": "NIFTY",
            "exchange": "NSE",
            "segment": "NSE_FO",
            "instrument_type": "FUT",
            "lot_size": 75,
            "tick_size": 5.0,
            "freeze_quantity": 1800.0,
            "expiry": monthly,
            "underlying_key": NIFTY_KEY,
        },
    ]
    for expiry in weeklies:
        stamp = datetime.fromtimestamp(expiry / 1000, tz=clock.IST).strftime("%d %b %y").upper()
        suffix = "" if expiry == weeklies[0] else f"_{stamp[:2]}"
        for strike in strikes:
            for option_type in ("CE", "PE"):
                records.append(
                    {
                        "instrument_key": f"NSE_FO|{strike}{option_type}{suffix}",
                        "trading_symbol": f"NIFTY {strike} {option_type} {stamp}",
                        "name": "NIFTY",
                        "exchange": "NSE",
                        "segment": "NSE_FO",
                        "instrument_type": option_type,
                        "lot_size": 75,
                        "tick_size": 5.0,
                        "freeze_quantity": 1800.0,
                        "expiry": expiry,
                        "strike_price": float(strike),
                        "underlying_key": NIFTY_KEY,
                    }
                )
    return records


class FakeResolver:
    """Minimal `core.resolver.InstrumentResolver` for strategy tests."""

    def __init__(self, instrument: Instrument | None = None, step: float = 50.0) -> None:
        self.instrument = instrument or make_instrument()
        self.step = step
        self.calls: list[dict] = []

    def resolve(
        self,
        underlying_key,
        segment,
        *,
        spot,
        bullish=True,
        expiry_rule="nearest_weekly",
        on_date=None,
    ) -> Instrument:
        self.calls.append({"segment": segment, "spot": spot, "bullish": bullish})
        if segment is Segment.STOCK:
            return Instrument(
                instrument_key=underlying_key,
                tradingsymbol="RELIANCE",
                segment=Segment.STOCK,
                lot_size=1,
            )
        return self.instrument

    def strike_step(self, underlying_key, expiry_rule="nearest_weekly") -> float:
        return self.step

    def lot_size(self, instrument_key: str) -> int:
        return self.instrument.lot_size

    def freeze_quantity(self, instrument_key: str) -> int | None:
        return self.instrument.freeze_quantity
