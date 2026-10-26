import httpx
import pytest
import respx

from salling_mcp import client as client_mod
from salling_mcp.client import BASE_URL, SallingAPIError, SallingClient

STORES = f"{BASE_URL}/v2/stores"


@pytest.fixture
def c():
    return SallingClient(api_key="k", retry_delay=0)


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
