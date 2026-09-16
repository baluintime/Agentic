"""Instrument master, auth, the feed hub and the REST client — all offline."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime

import httpx
import pytest

from broker.auth import AuthManager
from broker.instruments import InstrumentMaster
from broker.paper import PaperBroker, SimulatedLeg
from broker.rest import ENDPOINTS, TokenBucket, UpstoxError, UpstoxRest
from broker.ws_feed import MarketDataHub, ticks_from
from core.bus import EventBus
from core.clock import SimClock, set_clock
from core.contracts import OptionType, Segment, Side
from tests.helpers import NIFTY_KEY, Collector, FakeRest, nifty_records

SPOT = 24_873.0


@pytest.fixture
def master() -> InstrumentMaster:
    previous = set_clock(SimClock(datetime(2026, 9, 11, 10, 0)))
    try:
        yield InstrumentMaster.from_records(nifty_records())
    finally:
        set_clock(previous)


# -- instrument master -------------------------------------------------------
def test_strike_step_comes_from_the_file(master) -> None:
    assert master.strike_step(NIFTY_KEY) == 50.0


def test_atm_strike_rounds_to_the_step(master) -> None:
    assert master.atm_strike(SPOT, 50.0) == 24_850
    assert master.atm_strike(24_876.0, 50.0) == 24_900
    assert master.atm_strike(24_800.0, 100.0) == 24_800


def test_resolves_atm_call_and_put(master) -> None:
    call = master.option(NIFTY_KEY, SPOT, OptionType.CE)
    put = master.option(NIFTY_KEY, SPOT, OptionType.PE)
    assert call.strike == 24_850 and call.option_type is OptionType.CE
    assert put.strike == 24_850 and put.option_type is OptionType.PE
    assert call.lot_size == 75  # never hardcoded: it comes from the record
    assert call.freeze_quantity == 1800
    assert call.tick_size == 0.05  # the file reports paise
    assert call.expiry.date() == date(2026, 9, 17)


def test_strike_offsets(master) -> None:
    assert master.option(NIFTY_KEY, SPOT, OptionType.CE, offset=2).strike == 24_950
    assert master.option(NIFTY_KEY, SPOT, OptionType.CE, offset=-2).strike == 24_750


def test_strike_window_is_atm_plus_minus_n(master) -> None:
    keys = master.strike_window(NIFTY_KEY, SPOT, window=2)
    assert len(keys) == 10  # 5 strikes x CE and PE
    assert "NSE_FO|24850CE" in keys and "NSE_FO|24950PE" in keys


def test_missing_strike_raises(master) -> None:
    with pytest.raises(LookupError):
        master.option(NIFTY_KEY, 99_999.0, OptionType.CE)


def test_expiry_rules(master) -> None:
    assert master.pick_expiry(NIFTY_KEY, "nearest_weekly") == date(2026, 9, 17)


def test_expiry_rolls_after_the_cutoff_on_expiry_day() -> None:
    records = nifty_records()
    before = set_clock(SimClock(datetime(2026, 9, 17, 12, 0)))
    try:
        master = InstrumentMaster.from_records(records)
        assert master.pick_expiry(NIFTY_KEY, "nearest_weekly") == date(2026, 9, 17)
        set_clock(SimClock(datetime(2026, 9, 17, 13, 30)))
        master = InstrumentMaster.from_records(records)
        assert master.pick_expiry(NIFTY_KEY, "nearest_weekly") != date(2026, 9, 17)
    finally:
        set_clock(before)


def test_future_and_stock_resolution(master) -> None:
    future = master.future(NIFTY_KEY)
    assert future.segment is Segment.FUTURE and future.lot_size == 75
    stock = master.stock("NSE_EQ|INE002A01018")
    assert stock.segment is Segment.STOCK and stock.lot_size == 1


def test_resolver_protocol_dispatch(master) -> None:
    assert (
        master.resolve(NIFTY_KEY, Segment.OPTION, spot=SPOT, bullish=True).option_type
        is OptionType.CE
    )
    assert (
        master.resolve(NIFTY_KEY, Segment.OPTION, spot=SPOT, bullish=False).option_type
        is OptionType.PE
    )
    assert master.resolve(NIFTY_KEY, Segment.FUTURE, spot=SPOT).segment is Segment.FUTURE


def test_search_only_offers_tradable_underlyings(master) -> None:
    assert any(r["trading_symbol"] == "RELIANCE" for r in master.search("relia"))
    assert master.search("NIFTY 24850 CE") == []  # options are picked by the ATM rule


def test_lot_size_and_freeze_lookups(master) -> None:
    assert master.lot_size("NSE_FO|24850CE") == 75
    assert master.freeze_quantity("NSE_FO|24850CE") == 1800
    assert master.freeze_quantity(NIFTY_KEY) is None


# -- auth --------------------------------------------------------------------
def test_token_file_is_private_and_daily(tmp_path) -> None:
    manager = AuthManager(
        tmp_path / "token.json", {"UPSTOX_API_KEY": "k", "UPSTOX_API_SECRET": "s"}
    )
    assert manager.load().access_token is None
    state = manager.save("secret-token", {"user_name": "Bala"})
    assert state.user_name == "Bala"
    assert oct(manager.token_path.stat().st_mode)[-3:] == "600"
    assert "secret-token" not in json.dumps(state.redacted())
    reloaded = AuthManager(tmp_path / "token.json", {}).load()
    assert reloaded.access_token == "secret-token"
    assert not reloaded.stale


def test_yesterdays_token_is_stale(tmp_path) -> None:
    path = tmp_path / "token.json"
    path.write_text(json.dumps({"access_token": "x", "issued_on": "2020-01-01"}))
    state = AuthManager(path, {}).load()
    assert state.stale and "earlier day" in state.message


def test_login_url_carries_the_api_key(tmp_path) -> None:
    manager = AuthManager(
        tmp_path / "t.json", {"UPSTOX_API_KEY": "abc", "UPSTOX_REDIRECT_URI": "http://x/cb"}
    )
    url = manager.login_url()
    assert "client_id=abc" in url and "response_type=code" in url


@pytest.mark.asyncio
async def test_validate_uses_the_profile_endpoint(tmp_path) -> None:
    manager = AuthManager(tmp_path / "t.json", {})
    manager.save("tok")
    state = await manager.validate(FakeRest())
    assert state.valid and state.user_name == "Test User"


@pytest.mark.asyncio
async def test_validate_reports_a_rejected_token(tmp_path) -> None:
    class Rejecting(FakeRest):
        async def profile(self):
            raise UpstoxError("401", 401)

    manager = AuthManager(tmp_path / "t.json", {})
    manager.save("tok")
    state = await manager.validate(Rejecting())
    assert not state.valid and "rejected" in state.message


def test_expiry_warning(tmp_path) -> None:
    manager = AuthManager(tmp_path / "t.json", {})
    assert manager.expiry_warning() == "not logged in"
    manager.save("tok")
    assert manager.expiry_warning() == ""


# -- REST --------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rest_sends_the_bearer_token_and_parses_data() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": {"user_name": "Bala"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rest = UpstoxRest("tok", client=client)
    assert (await rest.profile())["user_name"] == "Bala"
    assert seen["auth"] == "Bearer tok"
    assert seen["url"].endswith(ENDPOINTS.profile)
    await client.aclose()


@pytest.mark.asyncio
async def test_rest_raises_on_http_error() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"errors": ["bad"]}))
    )
    rest = UpstoxRest("tok", client=client)
    with pytest.raises(UpstoxError):
        await rest.get("/v2/user/profile", retries=0)
    await client.aclose()


@pytest.mark.asyncio
async def test_place_order_is_never_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"errors": ["busy"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rest = UpstoxRest("tok", client=client, rate_per_second=1000)
    with pytest.raises(UpstoxError):
        await rest.place_order({"quantity": 1})
    assert calls["n"] == 1  # a timed-out placement may already have reached the exchange
    await client.aclose()


@pytest.mark.asyncio
async def test_historical_url_is_built_from_the_endpoint_template() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": {"candles": [[1, 2, 3, 4, 5, 6, 7]]}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    rest = UpstoxRest("tok", client=client)
    rows = await rest.historical_candles(
        NIFTY_KEY, "minutes", 5, date(2026, 9, 11), date(2026, 9, 4)
    )
    assert rows == [[1, 2, 3, 4, 5, 6, 7]]
    assert "/v3/historical-candle/" in seen["url"]
    assert seen["url"].endswith("/minutes/5/2026-09-11/2026-09-04")
    await client.aclose()


@pytest.mark.asyncio
async def test_token_bucket_limits_the_rate() -> None:
    bucket = TokenBucket(rate_per_second=1000, burst=2)
    loop = asyncio.get_running_loop()
    start = loop.time()
    for _ in range(6):
        await bucket.take()
    assert loop.time() - start >= 0.003


# -- market data hub ---------------------------------------------------------
class FakeFeed:
    def __init__(self, frames: list[dict], fail_after: int | None = None) -> None:
        self.frames = frames
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.connects = 0
        self.fail_after = fail_after

    async def connect(self) -> None:
        self.connects += 1

    async def subscribe(self, keys: list[str]) -> None:
        self.subscribed.extend(keys)

    async def unsubscribe(self, keys: list[str]) -> None:
        self.unsubscribed.extend(keys)

    async def messages_iter(self):
        for message in self.frames:
            yield message
        await asyncio.sleep(3600)

    def messages(self):
        return self.messages_iter()

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_hub_reference_counts_subscriptions() -> None:
    bus = EventBus()
    feed = FakeFeed([])
    hub = MarketDataHub("hub", None, bus, client=feed)
    hub.connected = True
    await hub.subscribe("A")
    await hub.subscribe("A")
    await hub.subscribe("B")
    assert hub.subscriptions == ["A", "B"]
    assert feed.subscribed == ["A", "B"]  # subscribed once each
    await hub.unsubscribe("A")
    assert hub.subscriptions == ["A", "B"]  # still referenced
    await hub.unsubscribe("A")
    assert hub.subscriptions == ["B"]
    assert feed.unsubscribed == ["A"]


@pytest.mark.asyncio
async def test_hub_publishes_ticks() -> None:
    bus = EventBus()
    await bus.start()
    ticks = Collector(bus, "tick.*")
    feed = FakeFeed([{"feeds": {"NSE|1": {"ltp": 100.5, "volume": 10}}}])
    hub = MarketDataHub("hub", None, bus, client=feed)
    await hub.subscribe("NSE|1")
    await hub.start()
    await asyncio.sleep(0.05)
    await bus.drain()
    assert ticks.messages and ticks.messages[0].ltp == 100.5
    assert feed.subscribed == ["NSE|1"]  # resubscribed on connect
    await hub.stop()
    await bus.stop()


def test_tick_normalisation() -> None:
    ticks = ticks_from({"feeds": {"A": {"ltp": 1.0}, "B": {"ltp": 2.0, "oi": 5}}})
    assert {t.instrument_key for t in ticks} == {"A", "B"}
    assert ticks_from({"instrument_key": "A", "ltp": 3.0})[0].ltp == 3.0
    assert ticks_from({"feeds": {"A": {}}}) == []
    assert ticks_from({}) == []


# -- paper broker ------------------------------------------------------------
def test_paper_slippage_always_works_against_the_trader() -> None:
    broker = PaperBroker(slippage_points=1.0)
    assert broker.fill_price(Side.BUY, 100.0) == 101.0
    assert broker.fill_price(Side.SELL, 100.0) == 99.0
    percent = PaperBroker(slippage_percent=1.0)
    assert percent.fill_price(Side.BUY, 100.0) == 101.0


def test_paper_fill_never_goes_below_the_tick() -> None:
    assert PaperBroker(slippage_points=1000).fill_price(Side.SELL, 5.0) == 0.05


@pytest.mark.parametrize(
    "side,ltp,expected",
    [
        (Side.BUY, 121.0, "TARGET_HIT"),
        (Side.BUY, 89.0, "SL_HIT"),
        (Side.BUY, 110.0, None),
        (Side.SELL, 79.0, "TARGET_HIT"),
        (Side.SELL, 111.0, "SL_HIT"),
    ],
)
def test_simulated_leg_triggers(side, ltp, expected) -> None:
    target = 120.0 if side is Side.BUY else 80.0
    stop = 90.0 if side is Side.BUY else 110.0
    leg = SimulatedLeg("c", "K", side, 75, 100.0, target, stop)
    hit = leg.check(ltp)
    assert (hit[0] if hit else None) == expected


# -- the console's instrument dropdown ---------------------------------------
def test_tradable_lists_every_underlying_with_a_label(master) -> None:
    rows = master.tradable()
    keys = {row["instrument_key"] for row in rows}
    assert keys == {NIFTY_KEY, "NSE_EQ|INE002A01018"}  # index and equity, no options
    labels = {row["instrument_key"]: row["label"] for row in rows}
    assert labels["NSE_EQ|INE002A01018"] == "RELIANCE · Reliance Industries (NSE_EQ)"
    assert labels[NIFTY_KEY] == "Nifty 50 (NSE_INDEX)"  # name repeats the symbol


def test_tradable_puts_indices_first(master) -> None:
    rows = master.tradable()
    assert rows[0]["instrument_type"] == "INDEX"


def test_tradable_excludes_options_and_futures(master) -> None:
    types = {row["instrument_type"] for row in master.tradable()}
    assert types == {"INDEX", "EQ"}


def test_tradable_is_empty_before_the_file_is_loaded() -> None:
    assert InstrumentMaster().tradable() == []


def test_tradable_deduplicates_by_instrument_key() -> None:
    records = nifty_records() + nifty_records()  # the same file loaded twice
    rows = InstrumentMaster.from_records(records).tradable()
    keys = [row["instrument_key"] for row in rows]
    assert len(keys) == len(set(keys))


# -- the daily login ---------------------------------------------------------
@pytest.mark.asyncio
async def test_exchange_code_stores_the_token(tmp_path) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(
            200, json={"access_token": "fresh-token", "user_name": "Bala", "user_id": "B1"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = AuthManager(
        tmp_path / "t.json",
        {
            "UPSTOX_API_KEY": "key",
            "UPSTOX_API_SECRET": "secret",
            "UPSTOX_REDIRECT_URI": "http://localhost:8080/auth/callback",
        },
    )
    state = await manager.exchange_code("the-code", client=client)
    assert state.valid and state.user_name == "Bala"
    assert "authorization_code" in seen["body"] and "the-code" in seen["body"]
    assert seen["url"].endswith("/v2/login/authorization/token")
    # stored for the rest of the day, private to the user
    assert AuthManager(tmp_path / "t.json", {}).load().access_token == "fresh-token"
    assert oct((tmp_path / "t.json").stat().st_mode)[-3:] == "600"
    await client.aclose()


@pytest.mark.asyncio
async def test_exchange_code_reports_a_refusal(tmp_path) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(400, json={"error": "invalid_grant"})
        )
    )
    manager = AuthManager(tmp_path / "t.json", {"UPSTOX_API_KEY": "k", "UPSTOX_API_SECRET": "s"})
    state = await manager.exchange_code("stale-code", client=client)
    assert not state.valid and "invalid_grant" in state.message
    assert not (tmp_path / "t.json").exists()  # nothing stored on a failed login
    await client.aclose()


def test_auth_accepts_a_string_token_path(tmp_path) -> None:
    """The constructor is public; a str path must not blow up inside save()."""
    manager = AuthManager(str(tmp_path / "t.json"), {})
    state = manager.save("tok", {"user_name": "Bala"})
    assert state.valid and manager.token_path.exists()
