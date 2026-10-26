import asyncio
import copy
import math
import os
import time
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

BASE_URL = "https://api.sallinggroup.com"


class SallingAPIError(ToolError):
    """An API failure with a message that is safe to show the agent (never the key).
    It is a ToolError because MCPServer hides the text of any other exception."""


RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_WAIT = 5  # seconds; a longer Retry-After is treated as a block

# Seconds a successful response is reused. Food waste stock changes during the day;
# products and stores barely change within a chat.
TTL_FOOD_WASTE = 300
TTL_STABLE = 3600
CACHE_MAX_ENTRIES = 500


class SallingClient:
    def __init__(self, api_key: str | None = None, timeout: float = 15.0,
                 max_concurrency: int = 4, retries: int = 2, retry_delay: float = 0.5):
        key = api_key or os.getenv("SALLING_API_KEY")
        if not key:
            raise SallingAPIError("SALLING_API_KEY is not set")
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        self._limit = asyncio.Semaphore(max_concurrency)
        self._retries = retries
        self._retry_delay = retry_delay
        self._cache: dict[tuple[str, tuple[tuple[str, Any], ...]], tuple[float, Any]] = {}
        self._blocked_until = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    def _rate_limited(self) -> SallingAPIError:
        minutes = max(1, math.ceil((self._blocked_until - time.monotonic()) / 60))
        return SallingAPIError(f"Rate limited by Salling Group API: try again in about {minutes} min. "
                               "Do not retry before then.")

    async def _get_once(self, path: str, params: dict[str, Any]) -> httpx.Response:
        if self._blocked_until > time.monotonic():
            raise self._rate_limited()
        async with self._limit:
            res = await self._http.get(path, params=params)
        retry_after = res.headers.get("Retry-After", "")
        if res.status_code == 429 and retry_after.isdigit() and int(retry_after) > MAX_RETRY_WAIT:
            # A long Retry-After means the API has blocked this IP. Every request sent
            # during the block can extend it, so stop calling until it has passed.
            self._blocked_until = time.monotonic() + int(retry_after)
            raise self._rate_limited()
        return res

    async def _send(self, path: str, params: dict[str, Any]) -> httpx.Response:
        """GET with retries on short rate limits, 5xx and network errors (all calls are reads)."""
        for attempt in range(self._retries):
            try:
                res = await self._get_once(path, params)
            except httpx.TransportError:
                await asyncio.sleep(self._retry_delay * 2 ** attempt)
                continue
            if res.status_code not in RETRY_STATUSES:
                return res
            retry_after = res.headers.get("Retry-After", "")
            await asyncio.sleep(float(retry_after) if retry_after.isdigit() else self._retry_delay * 2 ** attempt)
        return await self._get_once(path, params)

    async def _get(self, path: str, params: dict[str, Any] | None = None,
                   ttl: float = TTL_STABLE) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        key = (path, tuple(sorted(clean.items())))
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return copy.deepcopy(hit[1])
        data = await self._fetch(path, clean)
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (time.monotonic() + ttl, data)
        return copy.deepcopy(data)

    async def _fetch(self, path: str, clean: dict[str, Any]) -> Any:
        try:
            res = await self._send(path, clean)
            res.raise_for_status()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if status in (401, 403):
                raise SallingAPIError(f"Not authorized ({status}): check the API key and its scopes") from None
            if status == 404:
                raise SallingAPIError(f"Not found: {path}") from None
            if status == 429:
                raise SallingAPIError("Rate limited by Salling Group API, try again shortly") from None
            raise SallingAPIError(f"Salling Group API error {status} on {path}") from None
        except httpx.RequestError as e:
            raise SallingAPIError(f"Could not reach Salling Group API: {type(e).__name__}") from None
        return res.json()

    # Food Waste
    async def food_waste(self, zip_code: str | None = None, geo: str | None = None,
                         radius: float | None = None) -> list[dict]:
        return await self._get("/v1/food-waste/", {"zip": zip_code, "geo": geo, "radius": radius},
                               ttl=TTL_FOOD_WASTE)

    async def store_food_waste(self, store_id: str) -> dict:
        return await self._get(f"/v1/food-waste/{store_id}", ttl=TTL_FOOD_WASTE)

    # Product Suggestions
    async def relevant_products(self, query: str) -> list[dict]:
        data = await self._get("/v1/product-suggestions/relevant-products", {"query": query})
        return data.get("suggestions", []) if isinstance(data, dict) else data

    async def similar_products(self, product_id: str) -> list[dict]:
        return await self._get("/v1/product-suggestions/similar-products", {"productId": product_id})

    async def frequently_bought_together(self, product_id: str) -> list[dict]:
        return await self._get("/v1/product-suggestions/frequently-bought-together",
                               {"productId": product_id})

    # Stores
    async def stores(self, **params: Any) -> list[dict]:
        return await self._get("/v2/stores", params)

    async def store(self, store_id: str) -> dict:
        return await self._get(f"/v2/stores/{store_id}")
