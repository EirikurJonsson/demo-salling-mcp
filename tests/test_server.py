import asyncio
import os
from datetime import datetime

import httpx
import pytest
import respx
from mcp.server.elicitation import AcceptedElicitation, DeclinedElicitation
from mcp.server.mcpserver.exceptions import ToolError

from salling_mcp import pricing, server
from salling_mcp.client import BASE_URL, SallingAPIError, SallingClient


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    c = SallingClient(api_key="test-key", retry_delay=0)
    monkeypatch.setattr(server, "_client", c)
    monkeypatch.setattr(server, "_remember_zip", True)  # as when run over stdio
    monkeypatch.setattr(server, "_saved_zip", None)
    monkeypatch.setattr(server, "_zip_centers", {})
    return c


@respx.mock
async def test_price_ingredients_applies_tolerance_and_totals():
    def handler(request):
        q = request.url.params["query"]
        data = {"mælk": [{"prod_id": "1", "description": "Minimælk", "price": 10.0},
                         {"prod_id": "2", "description": "Sødmælk", "price": 11.5},
                         {"prod_id": "3", "description": "Øko", "price": 20.0}],
                "løg": []}[q]
        return httpx.Response(200, json={"suggestions": data})

    route = respx.get(f"{BASE_URL}/v1/product-suggestions/relevant-products").mock(side_effect=handler)
    out = await server.price_ingredients(["mælk", "løg"], 0.2)

    assert route.calls[0].request.headers["Authorization"] == "Bearer test-key"
    assert [o["price"] for o in out["items"]["mælk"]["options"]] == [10.0, 11.5]
    assert out["total_cheapest"] == 10.0
    assert out["not_found"] == ["løg"]


@respx.mock
async def test_find_food_waste_filters_by_ingredient():
    respx.get(f"{BASE_URL}/v2/stores").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{BASE_URL}/v1/food-waste/").mock(return_value=httpx.Response(200, json=[{
        "store": {"id": "s1", "coordinates": [10, 56]},
        "clearances": [
            {"offer": {"newPrice": 20}, "product": {"description": "HAKKET OKSEKØD"}},
            {"offer": {"newPrice": 5}, "product": {"description": "AGURK"}},
        ]}]))
    out = await server.find_food_waste(zip_code="8000", ingredients=["oksekød"])
    assert [c["product"] for c in out["stores"][0]["clearances"]] == ["HAKKET OKSEKØD"]


@respx.mock
async def test_search_stores_uses_zip_center_and_sorts_by_distance():
    def handler(request):
        p = request.url.params
        if "zip" in p:  # resolving the area center
            return httpx.Response(200, json=[{"coordinates": [10.20, 56.15]},
                                             {"coordinates": [10.22, 56.17]}])
        assert p["geo"] == "56.16000,10.21000" and "brand" not in p
        return httpx.Response(200, json=[
            {"id": "far", "coordinates": [10.40, 56.30]},
            {"id": "near", "coordinates": [10.21, 56.16]},
        ])
    respx.get(f"{BASE_URL}/v2/stores").mock(side_effect=handler)
    out = await server.search_stores(zip_code="8000")
    assert [s["store_id"] for s in out["stores"]] == ["near", "far"]
    assert out["stores"][0]["distance_km"] == 0.0
    assert out["stores"][0]["map_url"].startswith("https://www.openstreetmap.org/")
    assert server._saved_zip == "8000"


@respx.mock
async def test_search_stores_falls_back_to_zip_when_no_center():
    route = respx.get(f"{BASE_URL}/v2/stores").mock(return_value=httpx.Response(200, json=[]))
    out = await server.search_stores(zip_code="9999")
    assert out["stores"] == [] and route.calls[-1].request.url.params["zip"] == "9999"


async def test_invalid_postal_code_rejected():
    with pytest.raises(ToolError, match="postal code"):
        await server.search_stores(zip_code="Nørrebrogade 1")


class FakeCtx:
    def __init__(self, result):
        self.result = result

    async def elicit(self, message, schema):
        return self.result


@respx.mock
async def test_asks_user_for_postal_code_when_missing():
    respx.get(f"{BASE_URL}/v2/stores").mock(return_value=httpx.Response(200, json=[]))
    ctx = FakeCtx(AcceptedElicitation(data=server.PostalCode(zip_code="2200")))
    out = await server.search_stores(ctx=ctx)
    assert out["postal_code"] == "2200"


async def test_declined_elicitation_tells_agent_to_ask():
    with pytest.raises(ToolError, match="postnummer"):
        await server.search_stores(ctx=FakeCtx(DeclinedElicitation()))


@respx.mock
async def test_auth_error_is_readable_and_hides_key():
    respx.get(f"{BASE_URL}/v2/stores/x").mock(return_value=httpx.Response(401))
    with pytest.raises(SallingAPIError) as e:
        await server.get_store_details("x")
    assert "Not authorized" in str(e.value) and "test-key" not in str(e.value)


async def test_location_required():
    with pytest.raises(ToolError, match="postnummer"):
        await server.find_food_waste()


MILK = {"suggestions": [{"prod_id": "1", "description": "Sødmælk 1 L", "price": 12.0}]}


@respx.mock
async def test_price_ingredients_without_postal_code_skips_stores():
    respx.get(f"{BASE_URL}/v1/product-suggestions/relevant-products").mock(
        return_value=httpx.Response(200, json=MILK))
    stores = respx.get(f"{BASE_URL}/v2/stores")
    out = await server.price_ingredients(["mælk"])
    assert "suggested_store" not in out and not stores.called


@respx.mock
async def test_price_ingredients_suggests_nearest_bilka_and_food_waste():
    respx.get(f"{BASE_URL}/v1/product-suggestions/relevant-products").mock(
        return_value=httpx.Response(200, json=MILK))

    def stores(request):
        p = request.url.params
        if "zip" in p:
            return httpx.Response(200, json=[{"coordinates": [10.21, 56.16]}])
        assert p["brand"] == "bilka"
        return httpx.Response(200, json=[{"id": "far", "brand": "bilka", "coordinates": [10.5, 56.4]},
                                         {"id": "near", "brand": "bilka", "coordinates": [10.22, 56.16]}])
    respx.get(f"{BASE_URL}/v2/stores").mock(side_effect=stores)
    respx.get(f"{BASE_URL}/v1/food-waste/").mock(return_value=httpx.Response(200, json=[{
        "store": {"id": "n1", "coordinates": [10.21, 56.16]},
        "clearances": [{"offer": {"newPrice": 6}, "product": {"description": "SØDMÆLK"}},
                       {"offer": {"newPrice": 5}, "product": {"description": "AGURK"}}]}]))

    out = await server.price_ingredients(["mælk"], zip_code="8000")
    assert out["suggested_store"]["store_id"] == "near"
    assert "Bilka" in out["suggested_store"]["why"]
    assert [c["product"] for c in out["food_waste_matches"][0]["clearances"]] == ["SØDMÆLK"]
    assert out["items"]["mælk"]["cheapest"] == 12.0


@respx.mock
async def test_price_ingredients_falls_back_to_nearest_store_without_bilka():
    respx.get(f"{BASE_URL}/v1/product-suggestions/relevant-products").mock(
        return_value=httpx.Response(200, json=MILK))

    def stores(request):
        p = request.url.params
        if "zip" in p:
            return httpx.Response(200, json=[{"coordinates": [14.9, 55.1]}])
        if p.get("brand") == "bilka":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[{"id": "netto", "brand": "netto", "coordinates": [14.9, 55.1]}])
    respx.get(f"{BASE_URL}/v2/stores").mock(side_effect=stores)
    respx.get(f"{BASE_URL}/v1/food-waste/").mock(return_value=httpx.Response(200, json=[]))

    out = await server.price_ingredients(["mælk"], zip_code="3700")
    assert out["suggested_store"]["store_id"] == "netto"
    assert out["food_waste_matches"] == []


@respx.mock
async def test_store_lookup_failure_does_not_break_pricing():
    respx.get(f"{BASE_URL}/v1/product-suggestions/relevant-products").mock(
        return_value=httpx.Response(200, json=MILK))
    respx.get(f"{BASE_URL}/v2/stores").mock(side_effect=[
        httpx.Response(200, json=[{"coordinates": [10.21, 56.16]}]),
        httpx.Response(403), httpx.Response(403)])
    respx.get(f"{BASE_URL}/v1/food-waste/").mock(return_value=httpx.Response(200, json=[]))

    out = await server.price_ingredients(["mælk"], zip_code="8000")
    assert out["total_cheapest"] == 12.0 and "Not authorized" in out["store_error"]


@respx.mock
async def test_retries_rate_limit_then_succeeds():
    route = respx.get(f"{BASE_URL}/v2/stores/x").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"id": "x"})])
    out = await server.get_store_details("x")
    assert out["store_id"] == "x" and route.call_count == 2


@respx.mock
async def test_gives_up_after_retries():
    route = respx.get(f"{BASE_URL}/v2/stores/x").mock(return_value=httpx.Response(503))
    with pytest.raises(SallingAPIError, match="503"):
        await server.get_store_details("x")
    assert route.call_count == 3


@respx.mock
async def test_retries_network_errors():
    route = respx.get(f"{BASE_URL}/v2/stores/x").mock(side_effect=[
        httpx.ConnectError("boom"), httpx.Response(200, json={"id": "x"})])
    assert (await server.get_store_details("x"))["store_id"] == "x"
    assert route.call_count == 2


@respx.mock
async def test_concurrent_requests_are_capped(fake_client):
    active = peak = 0

    async def slow(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json=MILK)
    respx.get(f"{BASE_URL}/v1/product-suggestions/relevant-products").mock(side_effect=slow)
    await server.price_ingredients([f"vare{i}" for i in range(12)])
    assert peak == 4


@respx.mock
async def test_price_ingredients_with_amount_counts_packs():
    respx.get(f"{BASE_URL}/v1/product-suggestions/relevant-products").mock(
        return_value=httpx.Response(200, json={"suggestions": [
            {"prod_id": "1", "description": "Oksekød 500 g", "price": 45.0}]}))
    out = await server.price_ingredients([server.Ingredient(term="oksekød", amount="1,2 kg"), "mælk"])
    assert out["items"]["oksekød"]["suggested"]["packs"] == 3
    assert out["total_suggested"] == 135.0
    assert out["not_found"] == ["mælk"]  # beef is not priced as milk


LIVE_LOEG = [("Løg", 12), ("Løg øko", 14), ("Løg", 22), ("Rødløg", 8), ("Rødløg øko", 12),
             ("Hvidløg", 14.5), ("Bananskalotteløg", 10), ("Forårsløg i bundt", 9)]


def suggestions(rows):
    return httpx.Response(200, json={"suggestions": [
        {"prod_id": str(i), "title": t, "description": t, "price": p} for i, (t, p) in enumerate(rows)]})


@respx.mock
async def test_cheap_other_product_does_not_push_out_the_real_match():
    respx.get(f"{BASE_URL}/v1/product-suggestions/relevant-products").mock(return_value=suggestions(LIVE_LOEG))
    item = (await server.price_ingredients(["løg"]))["items"]["løg"]
    assert [o["title"] for o in item["options"]] == ["Løg", "Løg øko"]
    assert item["other_matches"][0] == {"product_id": "2", "title": "Løg", "price": 22}
    assert "Rødløg" in [o["title"] for o in item["other_matches"]]


@respx.mock
async def test_phrase_falls_back_to_single_word():
    def handler(request):
        q = request.url.params["query"]
        if q == "gule løg":
            return suggestions([("Pastasauce m. løg og gulerod", 22.25)])
        return suggestions(LIVE_LOEG if q == "løg" else [])
    route = respx.get(f"{BASE_URL}/v1/product-suggestions/relevant-products").mock(side_effect=handler)
    item = (await server.price_ingredients(["gule løg"]))["items"]["gule løg"]
    assert item["searched_as"] == "løg" and item["options"][0]["title"] == "Løg"
    assert [c.request.url.params["query"] for c in route.calls] == ["gule løg", "løg"]


@respx.mock
async def test_no_match_shows_closest_results():
    respx.get(f"{BASE_URL}/v1/product-suggestions/relevant-products").mock(
        return_value=suggestions([("Ravioli m. tomat og ricotta", 25)]))
    item = (await server.price_ingredients(["burrata"]))["items"]["burrata"]
    assert item["found"] is False
    assert item["closest_results"] == [{"product_id": "0", "title": "Ravioli m. tomat og ricotta", "price": 25}]


@respx.mock
async def test_postal_code_not_remembered_outside_stdio(monkeypatch):
    monkeypatch.setattr(server, "_remember_zip", False)
    respx.get(f"{BASE_URL}/v2/stores").mock(return_value=httpx.Response(200, json=[]))
    out = await server.set_postal_code("8000")
    assert out["remembered"] is False and server._saved_zip is None
    with pytest.raises(ToolError, match="postnummer"):
        await server.search_stores()


@pytest.mark.parametrize("call", [
    lambda: server.search_stores(zip_code="8000", radius_km=0),
    lambda: server.search_stores(zip_code="8000", limit=51),
    lambda: server.show_stores_map(zip_code="8000", limit=21),
    lambda: server.find_food_waste(zip_code="8000", radius_km=500),
    lambda: server.get_similar_products("1", limit=-1),
    lambda: server.price_ingredients(["mælk"], price_tolerance=3),
    lambda: server.price_ingredients(["  "]),
])
async def test_out_of_range_inputs_rejected_before_any_api_call(call):
    with respx.mock(assert_all_called=False) as mock:
        with pytest.raises(ToolError):
            await call()
        assert not mock.calls


@respx.mock
async def test_similar_and_bought_together_trim_and_limit():
    items = [{"prod_id": str(i), "description": f"Vare {i} 1 l", "price": 10.0 + i, "img": "x"}
             for i in range(5)]
    respx.get(f"{BASE_URL}/v1/product-suggestions/similar-products").mock(
        return_value=httpx.Response(200, json=items))
    respx.get(f"{BASE_URL}/v1/product-suggestions/frequently-bought-together").mock(
        return_value=httpx.Response(200, json=items))
    similar = await server.get_similar_products("1", limit=2)
    together = await server.get_frequently_bought_together("1", limit=3)
    assert [p["product_id"] for p in similar["products"]] == ["0", "1"]
    assert len(together["products"]) == 3 and "img" not in together["products"][0]


@respx.mock
async def test_store_food_waste_trims_store_and_offers():
    respx.get(f"{BASE_URL}/v1/food-waste/s1").mock(return_value=httpx.Response(200, json={
        "store": {"id": "s1", "name": "Netto", "coordinates": [10, 56]},
        "clearances": [{"offer": {"newPrice": 5, "originalPrice": 10, "percentDiscount": 50},
                        "product": {"description": "SKYR", "ean": "123"}}]}))
    out = await server.get_store_food_waste("s1")
    assert out["store"]["store_id"] == "s1"
    assert out["clearances"][0]["new_price"] == 5 and "ean" not in out["clearances"][0]


@respx.mock
async def test_store_details_has_week_hours_open_now_and_distance(monkeypatch):
    now = datetime(2026, 10, 21, 12, 0, tzinfo=pricing.DK_TZ)
    monkeypatch.setattr(pricing, "now_dk", lambda: now)
    monkeypatch.setattr(server, "_saved_zip", "8000")
    monkeypatch.setattr(server, "_zip_centers", {"8000": (56.0, 10.0)})
    respx.get(f"{BASE_URL}/v2/stores/s1").mock(return_value=httpx.Response(200, json={
        "id": "s1", "coordinates": [10.0, 56.0], "attributes": {"parking": True, "wifi": False},
        "hours": [{"date": "2026-10-21", "type": "store", "open": "08:00", "close": "20:00"},
                  {"date": "2026-10-22", "type": "store", "closed": True}]}))
    out = await server.get_store_details("s1")
    assert out["open_now"] is True and out["distance_km"] == 0.0
    assert [d["weekday"] for d in out["hours"]] == ["wednesday", "thursday"]
    assert out["attributes"] == {"parking": True}


@respx.mock
async def test_show_stores_map_returns_image_and_saves_file(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "MAP_DIR", tmp_path)
    monkeypatch.setattr(server.mapimage, "render_store_map", lambda stores, center: b"PNG")
    old = tmp_path / "stores-1000.png"
    old.write_bytes(b"old")
    os.utime(old, (0, 0))
    respx.get(f"{BASE_URL}/v2/stores").mock(return_value=httpx.Response(200, json=[
        {"id": "a", "name": "Netto", "coordinates": [10.2, 56.1]}]))
    image, info = await server.show_stores_map(zip_code="8000")
    assert image.data == b"PNG" and info["stores"][0]["n"] == 1
    assert (tmp_path / "stores-8000.png").read_bytes() == b"PNG"
    assert not old.exists()


async def test_lifespan_closes_the_shared_client(fake_client):
    async with server._lifespan(server.mcp):
        assert server.client() is fake_client
    assert server._client is None and fake_client._http.is_closed


async def test_tools_are_annotated():
    tools = {t.name: t.annotations for t in await server.mcp.list_tools()}
    assert tools["set_postal_code"].read_only_hint is False
    assert all(a.read_only_hint and a.open_world_hint
               for name, a in tools.items() if name != "set_postal_code")
