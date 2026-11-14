import asyncio

import httpx
import pytest
import respx

from salling_mcp import client as client_mod
from salling_mcp.client import BASE_URL, SallingAPIError, SallingClient

STORES = f"{BASE_URL}/v2/stores"


@pytest.fixture
def c():
    return SallingClient(api_key="k", retry_delay=0, min_gap=0)


@respx.mock
async def test_repeated_calls_are_cached(c):
    route = respx.get(STORES).mock(return_value=httpx.Response(200, json=[{"id": "a"}]))
    first = await c.stores(zip="8000")
    first[0]["id"] = "changed by caller"
    assert await c.stores(zip="8000") == [{"id": "a"}]
    assert route.call_count == 1
    await c.stores(zip="8200")
    assert route.call_count == 2


@respx.mock
async def test_cache_expires(c, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(client_mod.time, "time", lambda: now[0])
    route = respx.get(f"{BASE_URL}/v1/food-waste/").mock(return_value=httpx.Response(200, json=[]))
    await c.food_waste(zip_code="8000")
    now[0] += client_mod.TTL_FOOD_WASTE - 1
    await c.food_waste(zip_code="8000")
    assert route.call_count == 1
    now[0] += 2
    await c.food_waste(zip_code="8000")
    assert route.call_count == 2


@respx.mock
async def test_errors_are_not_cached(c):
    route = respx.get(STORES).mock(side_effect=[httpx.Response(403), httpx.Response(200, json=[])])
    with pytest.raises(SallingAPIError):
        await c.stores(zip="8000")
    assert await c.stores(zip="8000") == []
    assert route.call_count == 2


@respx.mock
async def test_cache_is_bounded(c, monkeypatch):
    monkeypatch.setattr(client_mod, "CACHE_MAX_ENTRIES", 2)
    respx.get(STORES).mock(return_value=httpx.Response(200, json=[]))
    for z in ("1000", "2000", "3000"):
        await c.stores(zip=z)
    assert len(c._cache) == 2


@respx.mock
async def test_not_found_message(c):
    respx.get(f"{STORES}/nope").mock(return_value=httpx.Response(404))
    with pytest.raises(SallingAPIError, match="Not found: /v2/stores/nope"):
        await c.store("nope")


def test_missing_key_is_reported(monkeypatch):
    monkeypatch.delenv("SALLING_API_KEY", raising=False)
    with pytest.raises(SallingAPIError, match="SALLING_API_KEY is not set"):
        SallingClient()


@respx.mock
async def test_ip_block_stops_all_calls_until_it_ends(c, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: now[0])
    route = respx.get(STORES).mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "1929"}), httpx.Response(200, json=[])])
    with pytest.raises(SallingAPIError, match="about 33 min"):
        await c.stores(zip="8000")
    with pytest.raises(SallingAPIError, match="Do not retry"):
        await c.stores(zip="8200")
    assert route.call_count == 1  # no retries into the block, no new requests during it
    now[0] += 1930
    assert await c.stores(zip="8200") == []
    assert route.call_count == 2


@respx.mock
async def test_short_retry_after_is_still_retried(c):
    route = respx.get(STORES).mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json=[])])
    assert await c.stores(zip="8000") == []
    assert route.call_count == 2


PRODUCTS = f"{BASE_URL}/v1/product-suggestions/relevant-products"


@respx.mock
async def test_cache_survives_restart(tmp_path):
    db = tmp_path / "cache.sqlite"
    route = respx.get(PRODUCTS).mock(return_value=httpx.Response(200, json={"suggestions": [{"id": 1}]}))
    first = SallingClient(api_key="k", min_gap=0, cache_path=db)
    await first.relevant_products("mælk")
    await first.aclose()
    second = SallingClient(api_key="k", min_gap=0, cache_path=db)
    assert await second.relevant_products("mælk") == [{"id": 1}]
    assert route.call_count == 1
    await second.aclose()


@respx.mock
async def test_block_survives_restart(tmp_path):
    db = tmp_path / "cache.sqlite"
    route = respx.get(PRODUCTS).mock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    first = SallingClient(api_key="k", min_gap=0, retry_delay=0, cache_path=db)
    with pytest.raises(SallingAPIError, match="Rate limited"):
        await first.relevant_products("mælk")
    await first.aclose()
    second = SallingClient(api_key="k", min_gap=0, retry_delay=0, cache_path=db)
    with pytest.raises(SallingAPIError, match="Rate limited"):
        await second.relevant_products("smør")
    assert route.call_count == 1  # the restarted client did not call again
    await second.aclose()


@respx.mock
async def test_quota_apis_are_called_one_at_a_time():
    active = peak = 0

    async def slow(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"suggestions": []})
    respx.get(PRODUCTS).mock(side_effect=slow)
    c = SallingClient(api_key="k", min_gap=0)
    await asyncio.gather(*(c.relevant_products(f"v{i}") for i in range(6)))
    assert peak == 1
    await c.aclose()


@respx.mock
async def test_calls_are_spaced_by_min_gap(monkeypatch):
    sleeps = []
    real_sleep = asyncio.sleep

    async def fake_sleep(s):
        sleeps.append(s)
        await real_sleep(0)
    monkeypatch.setattr(client_mod.asyncio, "sleep", fake_sleep)
    respx.get(PRODUCTS).mock(return_value=httpx.Response(200, json={"suggestions": []}))
    c = SallingClient(api_key="k", min_gap=1.0)
    await c.relevant_products("a")
    await c.relevant_products("b")
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 1.0
    await c.aclose()


@respx.mock
async def test_daily_budget_stops_calls_before_the_quota():
    route = respx.get(PRODUCTS).mock(return_value=httpx.Response(200, json={"suggestions": []}))
    c = SallingClient(api_key="k", min_gap=0, daily_budget=2)
    await c.relevant_products("a")
    await c.relevant_products("b")
    with pytest.raises(SallingAPIError, match="Daily call budget"):
        await c.relevant_products("c")
    assert route.call_count == 2
    await c.relevant_products("a")  # cached answers are still free
    await c.aclose()
