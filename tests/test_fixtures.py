"""The fixtures themselves must stay small, offline and well formed."""

from __future__ import annotations

import pandas as pd

from agents.indicators.ichimoku.agent import Config as IchimokuConfig
from agents.indicators.ichimoku.agent import IchimokuAgent
from agents.indicators.macd.agent import Config as MacdConfig
from agents.indicators.macd.agent import MacdAgent
from broker.instruments import InstrumentMaster
from broker.ws_feed import ticks_from
from core.contracts import OptionType, Segment
from tests import fixtures


def test_candle_fixtures_are_a_full_session() -> None:
    one_minute = fixtures.candles("1m")
    assert len(one_minute) == 375
    assert one_minute.index[0].strftime("%H:%M") == "09:15"
    assert one_minute.index[-1].strftime("%H:%M") == "15:29"
    assert (one_minute["high"] >= one_minute["low"]).all()
    assert one_minute["volume"].is_monotonic_increasing  # cumulative day volume


def test_five_minute_fixture_matches_the_one_minute_one() -> None:
    one_minute, five_minute = fixtures.candles("1m"), fixtures.candles("5m")
    assert len(five_minute) == 75
    assert five_minute["open"].iloc[0] == one_minute["open"].iloc[0]
    assert five_minute["high"].iloc[0] == one_minute["high"].iloc[:5].max()
    assert five_minute["low"].iloc[0] == one_minute["low"].iloc[:5].min()
    assert five_minute["close"].iloc[0] == one_minute["close"].iloc[4]


def test_indicators_run_on_the_fixture_session() -> None:
    frame = fixtures.candles("1m")
    macd = MacdAgent("m", MacdConfig()).compute(frame.copy())
    assert not pd.isna(macd["macd"].iloc[-1])
    assert macd["macd"].iloc[:20].notna().all()  # EMAs are defined from the start
    ichimoku = IchimokuAgent("i", IchimokuConfig()).compute(frame.copy())
    assert not pd.isna(ichimoku["span_b_current"].iloc[-1])
    assert pd.isna(ichimoku["span_b_current"].iloc[70])  # still inside the 78-bar warm-up


def test_tick_fixture_shape() -> None:
    frame = fixtures.ticks()
    assert len(frame) == 600
    assert (frame["ltp"] > 0).all()
    assert frame["cum_volume"].is_monotonic_increasing


def test_instrument_sample_covers_every_shape() -> None:
    master = InstrumentMaster.from_records(fixtures.broker_sample("instruments_sample"))
    assert master.lot_size("NSE_FO|44671CE") == 75
    assert master.freeze_quantity("NSE_FO|44671CE") == 1800
    assert master.strike_step("NSE_INDEX|Nifty 50") == 50.0
    option = master.option("NSE_INDEX|Nifty 50", 24_860.0, OptionType.CE)
    assert option.strike == 24_850 and option.tick_size == 0.05
    assert master.future("NSE_INDEX|Nifty 50").segment is Segment.FUTURE
    assert master.stock("NSE_EQ|INE002A01018").tradingsymbol == "RELIANCE"


def test_market_feed_sample_normalises_to_ticks() -> None:
    ticks = ticks_from(fixtures.broker_sample("market_feed_sample"))
    assert len(ticks) == 2
    by_key = {t.instrument_key: t for t in ticks}
    assert by_key["NSE_FO|44675"].cum_volume == 1_875_000
    assert by_key["NSE_INDEX|Nifty 50"].ltp == 24_873.45


def test_order_details_sample_has_the_fields_the_order_agents_read() -> None:
    data = fixtures.broker_sample("order_details_sample")["data"]
    assert {"status", "filled_quantity", "average_price", "tag"} <= set(data)
