import asyncio
import logging
import re
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.utilities.types import Image
from pydantic import BaseModel, Field

from salling_mcp import mapimage, pricing
from salling_mcp.client import SallingAPIError, SallingClient

load_dotenv()
logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = MCPServer("salling-group")

_client: SallingClient | None = None

PRICE_NOTE = "Prices are Bilka ToGo online prices: a reference, not exact prices for a physical store."


def client() -> SallingClient:
    global _client
    if _client is None:
        _client = SallingClient()
    return _client


# Only the postal code is kept (in memory, for this session) — never an address.
_saved_zip: str | None = None
_zip_centers: dict[str, tuple[float, float] | None] = {}

DISTANCE_NOTE = "Distances are approximate, measured from the middle of the postal code area."
ASK_ZIP = ("Ask the user for their Danish postal code (postnummer, e.g. '8000') "
           "and call this tool again with zip_code.")


class PostalCode(BaseModel):
    zip_code: str = Field(description="Danish postal code (postnummer), 4 digits, e.g. 8000")


async def _ask_zip(ctx: Context | None) -> str | None:
    if ctx is None:
        return None
    try:
        res = await ctx.elicit("Which postal code (postnummer) should I look for stores near?",
                               PostalCode)
    except Exception:  # client does not support elicitation
        return None
    return res.data.zip_code if res.action == "accept" else None


async def _resolve_zip(zip_code: str | None, ctx: Context | None) -> tuple[str, tuple[float, float] | None]:
    """Return (zip, approximate area center). The center is the average position of
    Salling stores in that postal code, so no third-party geocoder sees the user's data."""
    global _saved_zip
    z = (zip_code or _saved_zip or await _ask_zip(ctx) or "").strip()
    if not z:
        raise ValueError(ASK_ZIP)
    if not re.fullmatch(r"\d{4}", z):
        raise ValueError(f"'{z}' is not a Danish postal code (4 digits, e.g. '8000').")
    if z not in _zip_centers:
        _zip_centers[z] = pricing.centroid(await client().stores(zip=z, per_page=100))
    _saved_zip = z
    return z, _zip_centers[z]


def _geo(center: tuple[float, float]) -> str:
    return f"{center[0]:.5f},{center[1]:.5f}"


@mcp.tool()
async def set_postal_code(zip_code: str, ctx: Context | None = None) -> dict:
    """Remember the user's postal code (postnummer) for this session, so store and
    food-waste searches show distances without asking again. Only the postal code is
    stored, never an address."""
    z, center = await _resolve_zip(zip_code, ctx)
    return {"postal_code": z, "area_found": center is not None}


@mcp.tool()
async def price_ingredients(ingredients: list[str], price_tolerance: float = 0.2) -> dict:
    """Price a recipe's ingredients. Use this when the user gives a recipe or ingredient list.

    Pass short Danish product search terms, one per ingredient, without amounts
    (e.g. ["hakket oksekød", "løg", "fløde"]). For each ingredient, returns products priced
    within `price_tolerance` (0.2 = 20%) above the cheapest, a cheapest price and a
    'suggested' mid-range pick, plus totals for the whole list.

    Search is fuzzy: always check option titles match the ingredient (e.g. a search for
    'parmesan' may return other cheeses) and retry with a different term if not.
    """
    if not ingredients:
        raise ValueError("ingredients must not be empty")
    if not 0 <= price_tolerance <= 2:
        raise ValueError("price_tolerance must be between 0 and 2")

    async def one(term: str) -> tuple[str, dict]:
        try:
            raw = await client().relevant_products(term)
        except SallingAPIError as e:
            return term, {"found": False, "error": str(e), "options": []}
        options = pricing.within_tolerance([pricing.trim_product(p) for p in raw], price_tolerance)
        return term, pricing.price_summary(options)

    items = dict(await asyncio.gather(*(one(t.strip()) for t in ingredients if t.strip())))
    return {
        "items": items,
        "total_cheapest": round(sum(i["cheapest"] for i in items.values() if i.get("found")), 2),
        "total_suggested": pricing.shopping_total(items),
        "not_found": [t for t, i in items.items() if not i.get("found")],
        "currency": "DKK",
        "note": PRICE_NOTE,
    }


@mcp.tool()
async def search_products(query: str, limit: int = 10) -> dict:
    """Search Bilka ToGo products by a Danish search term (e.g. 'mælk'). Use for a single
    product lookup or to browse options; use price_ingredients for a whole recipe."""
    raw = await client().relevant_products(query)
    return {"products": [pricing.trim_product(p) for p in raw[:limit]], "note": PRICE_NOTE}


@mcp.tool()
async def get_similar_products(product_id: str, limit: int = 10) -> dict:
    """Find alternatives to a product (by product_id from another tool), e.g. a cheaper
    or organic substitute."""
    raw = await client().similar_products(product_id)
    return {"products": [pricing.trim_product(p) for p in raw[:limit]], "note": PRICE_NOTE}


@mcp.tool()
async def get_frequently_bought_together(product_id: str, limit: int = 10) -> dict:
    """Products often bought together with a product (by product_id). Use for
    'what else might I need' suggestions."""
    raw = await client().frequently_bought_together(product_id)
    return {"products": [pricing.trim_product(p) for p in raw[:limit]], "note": PRICE_NOTE}


@mcp.tool()
async def find_food_waste(zip_code: str | None = None, radius_km: float = 5,
                          ingredients: list[str] | None = None,
                          ctx: Context | None = None) -> dict:
    """Find discounted near-expiry items (food waste clearances) in stores near the user's
    postal code. These prices are real per-store prices. zip_code is a Danish postnummer
    (e.g. '8000'); if omitted, the saved one is used or the user is asked. Stores are
    sorted by approximate distance and include a map link. Optionally pass ingredient
    terms to only return matching items."""
    z, center = await _resolve_zip(zip_code, ctx)
    if center:
        raw = await client().food_waste(geo=_geo(center), radius=radius_km)
    else:
        raw = await client().food_waste(zip_code=z)
    stores = []
    for entry in raw:
        clearances = [pricing.trim_clearance(c) for c in entry.get("clearances", [])]
        if ingredients:
            clearances = [c for c in clearances
                          if pricing.matches_any(f"{c['product']} {c['category']}", ingredients)]
        if clearances:
            store = pricing.add_distance(pricing.trim_store(entry.get("store", {})), center)
            stores.append({"store": store, "clearances": clearances})
    stores.sort(key=lambda e: e["store"].get("distance_km", float("inf")))
    return {"postal_code": z, "stores": stores, "currency": "DKK", "note": DISTANCE_NOTE}


@mcp.tool()
async def get_store_food_waste(store_id: str) -> dict:
    """List all discounted near-expiry items at one specific store (by store_id)."""
    raw = await client().store_food_waste(store_id)
    return {
        "store": pricing.trim_store(raw.get("store", {})),
        "clearances": [pricing.trim_clearance(c) for c in raw.get("clearances", [])],
        "currency": "DKK",
    }


async def _nearby_stores(zip_code: str | None, radius_km: float, brand: str | None,
                         limit: int, ctx: Context | None) -> tuple[str, tuple[float, float] | None, list[dict]]:
    z, center = await _resolve_zip(zip_code, ctx)
    if center:
        raw = await client().stores(geo=_geo(center), radius=radius_km, brand=brand,
                                    country="dk", per_page=limit)
    else:
        raw = await client().stores(zip=z, brand=brand, country="dk", per_page=limit)
    stores = [pricing.add_distance(pricing.trim_store(s), center) for s in raw]
    stores.sort(key=lambda s: s.get("distance_km", float("inf")))
    return z, center, stores


@mcp.tool()
async def search_stores(zip_code: str | None = None, radius_km: float = 5,
                        brand: str | None = None, limit: int = 10,
                        ctx: Context | None = None) -> dict:
    """Find Salling Group stores in Denmark near the user's postal code, nearest first,
    with approximate distance and a map link. zip_code is a Danish postnummer (e.g. '8000');
    if omitted, the saved one is used or the user is asked. brand: bilka, foetex, netto,
    salling, br, carlsjr, starbucks."""
    z, _, stores = await _nearby_stores(zip_code, radius_km, brand, limit, ctx)
    return {"postal_code": z, "stores": stores, "note": DISTANCE_NOTE}


@mcp.tool()
async def show_stores_map(zip_code: str | None = None, radius_km: float = 3,
                          brand: str | None = None, limit: int = 8,
                          ctx: Context | None = None) -> list:
    """Show a small map image of the nearest Salling Group stores around the user's postal
    code, so the user gets a rough idea of where they are. Stores are numbered dots
    (nearest = 1) colored by brand; the faint ring is the postal code area. Use when the
    user wants to see stores on a map. Same arguments as search_stores."""
    z, center, stores = await _nearby_stores(zip_code, radius_km, brand, limit, ctx)
    if not stores:
        return [{"postal_code": z, "stores": [], "note": "No stores found nearby."}]
    png = await asyncio.to_thread(mapimage.render_store_map, stores, center)
    path = Path(tempfile.gettempdir()) / f"salling-stores-{z}.png"
    path.write_bytes(png)
    legend = [{"n": i, "name": s["name"], "distance_km": s.get("distance_km"),
               "map_url": s.get("map_url")} for i, s in enumerate(stores, start=1)]
    return [Image(data=png, format="png"),
            {"postal_code": z, "image_file": str(path), "stores": legend, "note": DISTANCE_NOTE}]


@mcp.tool()
async def get_store_details(store_id: str) -> dict:
    """Details for one store (by store_id): address, today's hours, facilities, map link.
    Includes distance if the user's postal code is known."""
    raw = await client().store(store_id)
    center = _zip_centers.get(_saved_zip) if _saved_zip else None
    out = pricing.add_distance(pricing.trim_store(raw), center)
    out["attributes"] = {k: v for k, v in (raw.get("attributes") or {}).items() if v}
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
