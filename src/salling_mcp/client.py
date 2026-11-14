import asyncio
import copy
import json
import math
import os
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from salling_mcp.cache import Store

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
TTL_PRODUCTS = 86400  # Bilka ToGo prices: reuse for a day, the quota is only 100 calls/day
CACHE_MAX_ENTRIES = 500

# Food Waste and Product Suggestions have a 100 calls/day quota and per-IP spike
# protection (see skills/salling-group-api/REFERENCE.md). Their calls go one at a time,
# spaced out, and count against a daily budget kept below the quota.
QUOTA_PREFIXES = {"/v1/food-waste": "food-waste", "/v1/product-suggestions": "product-suggestions"}
MIN_GAP = 1.0           # seconds between calls to a spike-protected API
MAX_PACE_WAIT = 10.0    # longest wait we impose for a Retry-After on a normal response
DAILY_BUDGET = 90       # calls per rolling 24 h per quota API (the quota is 100)


def _bucket(path: str) -> str | None:
    for prefix, name in QUOTA_PREFIXES.items():
        if path.startswith(prefix):
            return name
    return None


def default_cache_path() -> Path | None:
    """SALLING_CACHE_PATH, or ~/.cache/salling-mcp/cache.sqlite. 'off' keeps it in memory."""
    configured = os.getenv("SALLING_CACHE_PATH")
    if configured and configured.lower() == "off":
        return None
    if configured:
        return Path(configured)
    base = Path(os.getenv("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "salling-mcp" / "cache.sqlite"


class SallingClient:
    def __init__(self, api_key: str | None = None, timeout: float = 15.0,
                 max_concurrency: int = 4, retries: int = 2, retry_delay: float = 0.5,
                 min_gap: float = MIN_GAP, daily_budget: int = DAILY_BUDGET,
                 cache_path: str | Path | None = None):
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
        self._store = Store(cache_path)
        self._spike_lock = asyncio.Lock()
        self._min_gap = min_gap
        self._next_call = 0.0
        self._daily_budget = daily_budget
        remaining = (self._store.get_meta("blocked_until") or 0.0) - time.time()
        if remaining > 0:  # a block from before a restart is still running
            self._blocked_until = time.monotonic() + remaining

    async def aclose(self) -> None:
        await self._http.aclose()
        self._store.close()

    def _rate_limited(self) -> SallingAPIError:
        minutes = max(1, math.ceil((self._blocked_until - time.monotonic()) / 60))
        return SallingAPIError(f"Rate limited by Salling Group API: try again in about {minutes} min. "
                               "Do not retry before then.")

    async def _get_once(self, path: str, params: dict[str, Any]) -> httpx.Response:
        if self._blocked_until > time.monotonic():
            raise self._rate_limited()
        bucket = _bucket(path)
        if bucket is None:
            async with self._limit:
                res = await self._http.get(path, params=params)
        else:
            async with self._spike_lock:  # one at a time, spaced out
                self._check_budget(bucket)
                await self._pace()
                self._store.record_call(bucket)
                res = await self._http.get(path, params=params)
                self._note_retry_after(res)
        retry_after = res.headers.get("Retry-After", "")
        if res.status_code == 429 and retry_after.isdigit() and int(retry_after) > MAX_RETRY_WAIT:
            # A long Retry-After means the quota is used up or this IP is quarantined.
            # Every request sent during that time can extend it, so stop calling.
            self._blocked_until = time.monotonic() + int(retry_after)
            self._store.set_meta("blocked_until", time.time() + int(retry_after))
            raise self._rate_limited()
        return res

    def _check_budget(self, bucket: str) -> None:
        used = self._store.calls_in_window(bucket)
        if used >= self._daily_budget:
            raise SallingAPIError(
                f"Daily call budget for the {bucket} API is used up ({used}/{self._daily_budget} "
                "in the last 24 h; the API quota is 100/day). Answer from what you already have "
                "or try again later; do not retry now.")

    async def _pace(self) -> None:
        wait = self._next_call - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

    def _note_retry_after(self, res: httpx.Response) -> None:
        """Earliest time the next call to a spike-protected API is safe."""
        retry_after = res.headers.get("Retry-After", "")
        asked = min(float(retry_after), MAX_PACE_WAIT) if retry_after.isdigit() else 0.0
        self._next_call = time.monotonic() + max(self._min_gap, asked)

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
        disk_key = json.dumps([path, sorted(clean.items())], default=str)
        saved = self._store.get(disk_key)  # survives restarts
        if saved:
            self._remember(key, saved[0], saved[1])
            return copy.deepcopy(saved[0])
        data = await self._fetch(path, clean)
        self._remember(key, data, ttl)
        self._store.put(disk_key, data, ttl)
        return copy.deepcopy(data)

    def _remember(self, key: tuple, data: Any, ttl: float) -> None:
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (time.monotonic() + ttl, data)

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
        data = await self._get("/v1/product-suggestions/relevant-products", {"query": query},
                               ttl=TTL_PRODUCTS)
        return data.get("suggestions", []) if isinstance(data, dict) else data

    async def similar_products(self, product_id: str) -> list[dict]:
        return await self._get("/v1/product-suggestions/similar-products", {"productId": product_id},
                               ttl=TTL_PRODUCTS)

    async def frequently_bought_together(self, product_id: str) -> list[dict]:
        return await self._get("/v1/product-suggestions/frequently-bought-together",
                               {"productId": product_id}, ttl=TTL_PRODUCTS)

    # Stores
    async def stores(self, **params: Any) -> list[dict]:
        return await self._get("/v2/stores", params)

    async def store(self, store_id: str) -> dict:
        return await self._get(f"/v2/stores/{store_id}")
