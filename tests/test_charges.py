from __future__ import annotations

import pytest

from core.charges import ChargesCalculator
from core.contracts import Product, Segment, Side

RATES = {
    "effective_from": "2026-04-01",
    "brokerage": {
        "options": {"flat_per_order": 20},
        "futures": {"percent": 0.0003, "max_per_order": 20},
        "equity_intraday": {"percent": 0.0003, "max_per_order": 20},
        "equity_delivery": 0,
    },
    "stt": {
        "options_sell_premium": 0.001,
        "futures_sell": 0.0002,
        "equity_intraday_sell": 0.00025,
        "equity_delivery": 0.001,
    },
    "exchange_txn": {
        "nse_options_premium": 0.00035,
        "nse_futures": 0.0000173,
        "nse_equity": 0.0000297,
    },
    "sebi_fee_per_crore": 10,
    "stamp_duty_buy": {
        "options": 0.00003,
        "futures": 0.00002,
        "equity_intraday": 0.00003,
        "equity_delivery": 0.00015,
    },
    "gst_rate": 0.18,
}


def test_unconfigured_rates_report_incomplete_and_zero() -> None:
    breakup = ChargesCalculator({}).leg(
        segment=Segment.OPTION, product=Product.INTRADAY, side=Side.BUY, price=100, quantity=75
    )
    assert breakup.total == 0.0
    assert breakup.complete is False
    assert "gst_rate" in breakup.missing


def test_configured_option_buy_leg() -> None:
    breakup = ChargesCalculator(RATES).leg(
        segment=Segment.OPTION, product=Product.INTRADAY, side=Side.BUY, price=100, quantity=75
    )
    turnover = 7500
    assert breakup.brokerage == 20
    assert breakup.stt == 0  # STT on options is charged on the sell leg only
    assert breakup.exchange_txn == pytest.approx(turnover * 0.00035, abs=0.01)
    assert breakup.stamp_duty == pytest.approx(turnover * 0.00003, abs=0.01)
    assert breakup.gst == pytest.approx(
        (breakup.brokerage + breakup.exchange_txn + breakup.sebi_fee) * 0.18, abs=0.01
    )
    assert breakup.complete


def test_option_sell_leg_pays_stt_on_the_premium() -> None:
    breakup = ChargesCalculator(RATES).leg(
        segment=Segment.OPTION, product=Product.INTRADAY, side=Side.SELL, price=120, quantity=75
    )
    assert breakup.stt == pytest.approx(120 * 75 * 0.001, abs=0.01)
    assert breakup.stamp_duty == 0  # stamp duty is charged on the buy leg only


def test_round_trip_is_the_sum_of_both_legs() -> None:
    calculator = ChargesCalculator(RATES)
    entry = calculator.leg(
        segment=Segment.OPTION, product=Product.INTRADAY, side=Side.BUY, price=100, quantity=75
    )
    exit_leg = calculator.leg(
        segment=Segment.OPTION, product=Product.INTRADAY, side=Side.SELL, price=120, quantity=75
    )
    trip = calculator.round_trip(
        segment=Segment.OPTION,
        product=Product.INTRADAY,
        entry_side=Side.BUY,
        entry_price=100,
        exit_price=120,
        quantity=75,
    )
    assert trip.total == pytest.approx(entry.total + exit_leg.total, abs=0.01)


def test_brokerage_percent_is_capped() -> None:
    calculator = ChargesCalculator(RATES)
    small = calculator.leg(
        segment=Segment.FUTURE, product=Product.INTRADAY, side=Side.BUY, price=100, quantity=75
    )
    large = calculator.leg(
        segment=Segment.FUTURE, product=Product.INTRADAY, side=Side.BUY, price=25_000, quantity=75
    )
    assert small.brokerage == pytest.approx(7500 * 0.0003, abs=0.01)
    assert large.brokerage == 20  # the cap


def test_delivery_equity_pays_stt_on_both_legs() -> None:
    calculator = ChargesCalculator(RATES)
    buy = calculator.leg(
        segment=Segment.STOCK, product=Product.DELIVERY, side=Side.BUY, price=1000, quantity=10
    )
    sell = calculator.leg(
        segment=Segment.STOCK, product=Product.DELIVERY, side=Side.SELL, price=1000, quantity=10
    )
    assert buy.stt > 0 and sell.stt > 0


def test_intraday_equity_pays_stt_on_the_sell_only() -> None:
    calculator = ChargesCalculator(RATES)
    buy = calculator.leg(
        segment=Segment.STOCK, product=Product.INTRADAY, side=Side.BUY, price=1000, quantity=10
    )
    sell = calculator.leg(
        segment=Segment.STOCK, product=Product.INTRADAY, side=Side.SELL, price=1000, quantity=10
    )
    assert buy.stt == 0 and sell.stt > 0


def test_configured_flag_needs_effective_from_and_gst() -> None:
    assert ChargesCalculator(RATES).configured
    assert not ChargesCalculator({"gst_rate": 0.18}).configured
    assert not ChargesCalculator({"effective_from": "2026-04-01"}).configured
