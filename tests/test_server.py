import httpx
import pytest
import respx

from mcp.server.elicitation import AcceptedElicitation, DeclinedElicitation

from salling_mcp import server
from salling_mcp.client import BASE_URL, SallingAPIError, SallingClient


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    c = SallingClient(api_key="test-key")
    monkeypatch.setattr(server, "_client", c)
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
    with pytest.raises(ValueError, match="postal code"):
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
    with pytest.raises(ValueError, match="postnummer"):
        await server.search_stores(ctx=FakeCtx(DeclinedElicitation()))


@respx.mock
async def test_auth_error_is_readable_and_hides_key():
    respx.get(f"{BASE_URL}/v2/stores/x").mock(return_value=httpx.Response(401))
    with pytest.raises(SallingAPIError) as e:
        await server.get_store_details("x")
    assert "Not authorized" in str(e.value) and "test-key" not in str(e.value)


async def test_location_required():
    with pytest.raises(ValueError, match="postnummer"):
        await server.find_food_waste()
