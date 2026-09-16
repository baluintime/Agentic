"""The console's live-price ordering, which has to read well at a glance."""

from __future__ import annotations

from types import SimpleNamespace

from ui.widgets import PricesCard


def card_with(underlyings: list[str]) -> PricesCard:
    """Build the ordering logic without a browser client."""
    pipelines = {
        key: SimpleNamespace(spec=SimpleNamespace(underlying_key=key)) for key in underlyings
    }
    card = PricesCard.__new__(PricesCard)
    card.engine = SimpleNamespace(pipelines=pipelines)
    return card


def test_underlyings_lead_the_list() -> None:
    keys = ["NSE_FO|24800CE", "NSE_FO|24800PE", "NSE_INDEX|Nifty 50", "NSE_FO|24850CE"]
    assert card_with(["NSE_INDEX|Nifty 50"])._ordered(keys)[0] == "NSE_INDEX|Nifty 50"


def test_every_subscription_is_still_listed_once() -> None:
    keys = ["NSE_FO|A", "NSE_INDEX|Nifty 50", "NSE_EQ|R"]
    ordered = card_with(["NSE_INDEX|Nifty 50", "NSE_EQ|R"])._ordered(keys)
    assert sorted(ordered) == sorted(keys)
    assert len(ordered) == len(set(ordered))


def test_an_underlying_with_no_subscription_is_not_invented() -> None:
    ordered = card_with(["NSE_INDEX|Gone"])._ordered(["NSE_FO|A"])
    assert ordered == ["NSE_FO|A"]


def test_no_pipelines_keeps_the_feed_order() -> None:
    keys = ["NSE_FO|A", "NSE_FO|B"]
    assert card_with([])._ordered(keys) == keys
