import os
from typing import Any

import httpx

BASE_URL = "https://api.sallinggroup.com"


class SallingAPIError(Exception):
    pass


class SallingClient:
    def __init__(self, api_key: str | None = None, timeout: float = 15.0):
        key = api_key or os.getenv("SALLING_API_KEY")
        if not key:
            raise SallingAPIError("SALLING_API_KEY is not set")
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            res = await self._http.get(path, params=clean)
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
        return await self._get("/v1/food-waste/", {"zip": zip_code, "geo": geo, "radius": radius})

    async def store_food_waste(self, store_id: str) -> dict:
        return await self._get(f"/v1/food-waste/{store_id}")

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
