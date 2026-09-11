"""Rate-limited Upstox REST client. Only this package talks to Upstox.

Endpoint reference (verify against the current docs before changing anything):
  Open API index      https://upstox.com/developer/api-documentation/open-api/
  Place order (v3)    https://upstox.com/developer/api-documentation/v3/place-order/
  Historical candles  https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/
  Intraday candles    https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/
  GTT order           https://upstox.com/developer/api-documentation/place-gtt-order/
  Instruments file    https://upstox.com/developer/api-documentation/instruments/

Paths live in `Endpoints` so a doc change is a one-line edit. Order placement is
never retried blindly: only idempotent GETs are retried.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from core.rest_urls import INSTRUMENTS_URL  # noqa: F401  (re-exported for convenience)

API_BASE = "https://api.upstox.com"
HFT_BASE = "https://api-hft.upstox.com"


@dataclass(frozen=True)
class Endpoints:
    profile: str = "/v2/user/profile"
    funds: str = "/v2/user/get-funds-and-margin"
    positions: str = "/v2/portfolio/short-term-positions"
    holdings: str = "/v2/portfolio/long-term-holdings"
    order_book: str = "/v2/order/retrieve-all"
    order_details: str = "/v2/order/details"
    place_order: str = "/v3/order/place"  # on HFT_BASE
    cancel_order: str = "/v3/order/cancel"  # on HFT_BASE
    gtt_place: str = "/v3/order/gtt/place"
    gtt_modify: str = "/v3/order/gtt/modify"
    gtt_cancel: str = "/v3/order/gtt/cancel"
    gtt_list: str = "/v3/order/gtt"
    historical: str = "/v3/historical-candle/{key}/{unit}/{interval}/{to_date}/{from_date}"
    intraday: str = "/v3/historical-candle/intraday/{key}/{unit}/{interval}"
    market_feed_authorize: str = "/v3/feed/market-data-feed/authorize"
    token: str = "/v2/login/authorization/token"
    authorize: str = "/v2/login/authorization/dialog"


ENDPOINTS = Endpoints()


class TokenBucket:
    """Simple token bucket; keeps us under the broker/SEBI order-rate limits."""

    def __init__(self, rate_per_second: float, burst: int | None = None) -> None:
        self.rate = float(rate_per_second)
        self.capacity = float(burst if burst is not None else max(1, int(rate_per_second)))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                await asyncio.sleep((tokens - self._tokens) / self.rate)


class UpstoxError(RuntimeError):
    def __init__(self, message: str, status: int = 0, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


class UpstoxRest:
    """Async Upstox REST adapter. Inject `client` (httpx.AsyncClient) in tests."""

    def __init__(
        self,
        access_token: str | None = None,
        client: httpx.AsyncClient | None = None,
        rate_per_second: float = 5,
        endpoints: Endpoints = ENDPOINTS,
    ) -> None:
        self.access_token = access_token
        self.endpoints = endpoints
        self._client = client
        self._owns_client = client is None
        self._bucket = TokenBucket(rate_per_second)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    def headers(self) -> dict[str, str]:
        head = {"Accept": "application/json"}
        if self.access_token:
            head["Authorization"] = f"Bearer {self.access_token}"
        return head

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # -- plumbing ------------------------------------------------------------
    async def _request(
        self,
        method: str,
        url: str,
        *,
        retries: int = 0,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            await self._bucket.take()
            try:
                response = await self.client.request(
                    method, url, headers=self.headers(), json=json, params=params
                )
                if response.status_code >= 400:
                    raise UpstoxError(
                        f"{method} {url} -> {response.status_code}",
                        response.status_code,
                        _safe_json(response),
                    )
                return response.json()
            except (httpx.TransportError, UpstoxError) as exc:
                status = getattr(exc, "status", 0)
                retryable = status in (0, 429, 500, 502, 503, 504)
                if attempt >= retries or not retryable:
                    raise
                await asyncio.sleep(2**attempt)
                attempt += 1

    async def get(self, path: str, *, params: dict | None = None, retries: int = 2) -> dict:
        return await self._request("GET", API_BASE + path, params=params, retries=retries)

    async def post(
        self, path: str, payload: dict, *, base: str = API_BASE, retries: int = 0
    ) -> dict:
        return await self._request("POST", base + path, json=payload, retries=retries)

    # -- account -------------------------------------------------------------
    async def profile(self) -> dict:
        return (await self.get(self.endpoints.profile)).get("data", {})

    async def positions(self) -> list[dict]:
        return (await self.get(self.endpoints.positions)).get("data", []) or []

    async def order_book(self) -> list[dict]:
        return (await self.get(self.endpoints.order_book)).get("data", []) or []

    async def order_details(self, order_id: str) -> dict:
        data = await self.get(self.endpoints.order_details, params={"order_id": order_id})
        return data.get("data", {}) or {}

    # -- market data ---------------------------------------------------------
    async def historical_candles(
        self, instrument_key: str, unit: str, interval: int, to_date: date, from_date: date
    ) -> list[list[Any]]:
        path = self.endpoints.historical.format(
            key=instrument_key,
            unit=unit,
            interval=interval,
            to_date=to_date.isoformat(),
            from_date=from_date.isoformat(),
        )
        data = await self.get(path)
        return (data.get("data") or {}).get("candles", [])

    async def intraday_candles(
        self, instrument_key: str, unit: str = "minutes", interval: int = 1
    ) -> list[list[Any]]:
        path = self.endpoints.intraday.format(key=instrument_key, unit=unit, interval=interval)
        data = await self.get(path)
        return (data.get("data") or {}).get("candles", [])

    async def feed_authorize(self) -> str:
        data = await self.get(self.endpoints.market_feed_authorize)
        return (data.get("data") or {}).get("authorized_redirect_uri", "")

    # -- orders --------------------------------------------------------------
    async def place_order(self, payload: dict) -> dict:
        """Never retried: a timeout may still have reached the exchange."""
        data = await self.post(self.endpoints.place_order, payload, base=HFT_BASE, retries=0)
        return data.get("data", data)

    async def cancel_order(self, order_id: str) -> dict:
        data = await self._request(
            "DELETE", f"{HFT_BASE}{self.endpoints.cancel_order}", params={"order_id": order_id}
        )
        return data.get("data", data)

    async def place_gtt(self, payload: dict) -> dict:
        data = await self.post(self.endpoints.gtt_place, payload, retries=0)
        return data.get("data", data)

    async def cancel_gtt(self, gtt_order_id: str) -> dict:
        data = await self._request(
            "DELETE", API_BASE + self.endpoints.gtt_cancel, json={"gtt_order_id": gtt_order_id}
        )
        return data.get("data", data)

    async def gtt_orders(self) -> list[dict]:
        return (await self.get(self.endpoints.gtt_list)).get("data", []) or []


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text[:500]
