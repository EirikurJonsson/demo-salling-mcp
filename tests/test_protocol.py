"""Through a real MCP client, as an agent sees the server: error texts, output schemas
and prompts. Calling tool functions directly skips the SDK, which hides errors."""
import json

import httpx
import pytest
import respx
from mcp import Client

from salling_mcp import server
from salling_mcp.client import BASE_URL, SallingClient

STORE = {"id": "s1", "name": "Netto Vej", "brand": "netto", "coordinates": [10.21, 56.16],
         "address": {"street": "Vej 1", "zip": "8000", "city": "Aarhus"}}


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    monkeypatch.setattr(server, "_client", SallingClient(api_key="test-key", retry_delay=0))
    monkeypatch.setattr(server, "_remember_zip", False)
    monkeypatch.setattr(server, "_saved_zip", None)
    monkeypatch.setattr(server, "_zip_centers", {})


def text(result) -> str:
    return result.content[0].text


async def test_guidance_errors_reach_the_agent():
    async with Client(server.mcp) as c:
        missing = await c.call_tool("search_stores", {})
        bad_zip = await c.call_tool("search_stores", {"zip_code": "Nørrebrogade"})
        bad_limit = await c.call_tool("search_products", {"query": "mælk", "limit": 0})
    assert missing.is_error and "postnummer" in text(missing)
    assert bad_zip.is_error and "not a Danish postal code" in text(bad_zip)
    assert bad_limit.is_error and "limit must be between 1 and 50" in text(bad_limit)


@respx.mock
async def test_api_errors_reach_the_agent_without_the_key():
    respx.get(f"{BASE_URL}/v2/stores/x").mock(return_value=httpx.Response(401))
    async with Client(server.mcp) as c:
        res = await c.call_tool("get_store_details", {"store_id": "x"})
    assert res.is_error and "Not authorized" in text(res) and "test-key" not in text(res)


async def test_every_tool_publishes_an_output_schema():
    async with Client(server.mcp) as c:
        tools = (await c.list_tools()).tools
    missing = [t.name for t in tools if t.name != "show_stores_map" and not t.output_schema]
    assert missing == []


@respx.mock
async def test_structured_output_matches_text_output():
    respx.get(f"{BASE_URL}/v1/product-suggestions/relevant-products").mock(
        return_value=httpx.Response(200, json={"suggestions": [
            {"prod_id": 7, "description": "Hakket oksekød 500 g", "price": 45, "link": "x"}]}))
    respx.get(f"{BASE_URL}/v2/stores").mock(return_value=httpx.Response(200, json=[STORE]))
    respx.get(f"{BASE_URL}/v2/stores/s1").mock(return_value=httpx.Response(200, json=STORE))
    respx.get(f"{BASE_URL}/v1/food-waste/").mock(return_value=httpx.Response(200, json=[]))
    calls = {
        "price_ingredients": {"ingredients": [{"term": "hakket oksekød", "amount": "1 kg"}, "løg"],
                              "zip_code": "8000"},
        "search_products": {"query": "oksekød"},
        "search_stores": {"zip_code": "8000"},
        "find_food_waste": {"zip_code": "8000"},
        "get_store_details": {"store_id": "s1"},
        "set_postal_code": {"zip_code": "8000"},
    }
    async with Client(server.mcp) as c:
        for name, args in calls.items():
            res = await c.call_tool(name, args)
            assert not res.is_error, (name, text(res))
            assert res.structured_content == json.loads(text(res)), name

        priced = (await c.call_tool("price_ingredients", calls["price_ingredients"])).structured_content
    beef = priced["items"]["hakket oksekød"]["suggested"]
    assert (beef["packs"], beef["line_total"]) == (2, 90.0)
    assert priced["suggested_store"]["store_id"] == "s1"


@respx.mock
async def test_postal_code_is_not_shared_when_not_on_stdio():
    respx.get(f"{BASE_URL}/v2/stores").mock(return_value=httpx.Response(200, json=[STORE]))
    async with Client(server.mcp) as a, Client(server.mcp) as b:
        saved = await a.call_tool("set_postal_code", {"zip_code": "8000"})
        other = await b.call_tool("search_stores", {})
    assert saved.structured_content["remembered"] is False
    assert other.is_error and "postnummer" in text(other)


async def test_prompts_are_listed_and_fill_in_arguments():
    async with Client(server.mcp) as c:
        names = {p.name for p in (await c.list_prompts()).prompts}
        recipe = await c.get_prompt("recipe_shopping_list",
                                    {"recipe": "Lasagne: 500 g hakket oksekød", "postal_code": "8000"})
        waste = await c.get_prompt("cook_from_food_waste", {})
    assert names == {"recipe_shopping_list", "cook_from_food_waste"}
    body = recipe.messages[0].content.text
    assert "price_ingredients" in body and "8000" in body and "Lasagne" in body
    assert "find_food_waste" in waste.messages[0].content.text
