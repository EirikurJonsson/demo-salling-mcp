import asyncio
import logging
import re
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.utilities.types import Image
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from salling_mcp import mapimage, pricing
from salling_mcp.client import SallingAPIError, SallingClient
from salling_mcp.models import (
    FoodWasteResult,
    PostalCodeResult,
    ProductList,
    ShoppingList,
    StoreDetails,
    StoreFoodWaste,
    StoreList,
)

load_dotenv()
logging.getLogger("httpx").setLevel(logging.WARNING)

_client: SallingClient | None = None


def client() -> SallingClient:
    global _client
    if _client is None:
        _client = SallingClient()
    return _client


@asynccontextmanager
async def _lifespan(_: MCPServer) -> AsyncIterator[None]:
    global _client
    try:
        yield
    finally:
        if _client is not None:
            await _client.aclose()
            _client = None


mcp = MCPServer("salling-group", lifespan=_lifespan)

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True)
# Only changes this session's saved postal code; calling it twice has the same effect.
SESSION_SETTING = ToolAnnotations(read_only_hint=False, destructive_hint=False,
                                  idempotent_hint=True, open_world_hint=True)

PRICE_NOTE = "Prices are Bilka ToGo online prices: a reference, not exact prices for a physical store."
DISTANCE_NOTE = "Distances are approximate, measured from the middle of the postal code area."
ASK_ZIP = ("Ask the user for their Danish postal code (postnummer, e.g. '8000') "
           "and call this tool again with zip_code.")

BILKA_RADIUS_KM = 30
FOOD_WASTE_RADIUS_KM = 5
MAX_FOOD_WASTE_STORES = 3
STORE_FETCH_PAGE = 50
MAX_RADIUS_KM = 50
MAX_LIST_LIMIT = 50
MAX_MAP_STORES = 20
MAP_DIR = Path(tempfile.gettempdir()) / "salling-mcp-maps"
MAP_FILE_MAX_AGE_S = 24 * 3600


# Only the postal code is ever kept (in memory), never an address. Over stdio the process
# serves exactly one user, so main() turns remembering on. Over HTTP one process serves
# many users and the 2026 protocol has no lasting sessions, so nothing is remembered and
# each call carries zip_code: one user's postal code can never reach another user.
_remember_zip = False
_saved_zip: str | None = None
# Postal code -> center of its stores. Public store data, safe to share between users.
_zip_centers: dict[str, tuple[float, float] | None] = {}


def _remembered_zip() -> str | None:
    return _saved_zip if _remember_zip else None


class PostalCode(BaseModel):
    zip_code: str = Field(description="Danish postal code (postnummer), 4 digits, e.g. 8000")


class Ingredient(BaseModel):
    term: str = Field(description="Short Danish product search term, e.g. 'hakket oksekød'")
    amount: str | None = Field(None, description="Amount the recipe needs, e.g. '500 g', '1 l', '3 stk'")


def _check_range(name: str, value: float, low: float, high: float) -> None:
    if not low <= value <= high:
        raise ToolError(f"{name} must be between {low} and {high}, got {value}")


async def _ask_zip(ctx: Context | None) -> str | None:
    if ctx is None:
        return None
    try:
        res = await ctx.elicit("Which postal code (postnummer) should I look for stores near?",
                               PostalCode)
    except Exception:  # client does not support elicitation
        return None
    return res.data.zip_code if res.action == "accept" else None


async def _resolve_zip(zip_code: str | None, ctx: Context | None,
                       ask: bool = True) -> tuple[str, tuple[float, float] | None]:
    """Return (zip, approximate area center). The center is the average position of
    Salling stores in that postal code, so no third-party geocoder sees the user's data."""
    global _saved_zip
    z = (zip_code or _remembered_zip() or (await _ask_zip(ctx) if ask else None) or "").strip()
    if not z:
        raise ToolError(ASK_ZIP)
    if not re.fullmatch(r"\d{4}", z):
        raise ToolError(f"'{z}' is not a Danish postal code (4 digits, e.g. '8000').")
    if z not in _zip_centers:
        _zip_centers[z] = pricing.centroid(await client().stores(zip=z, per_page=100))
    if _remember_zip:
        _saved_zip = z
    return z, _zip_centers[z]


def _geo(center: tuple[float, float]) -> str:
    return f"{center[0]:.5f},{center[1]:.5f}"


@mcp.tool(annotations=SESSION_SETTING)
async def set_postal_code(zip_code: str, ctx: Context | None = None) -> PostalCodeResult:
    """Remember the user's postal code (postnummer) for this session, so store and
    food-waste searches show distances without asking again. Only the postal code is
    stored, never an address. If 'remembered' is false (shared HTTP server), pass
    zip_code to each tool instead."""
    z, center = await _resolve_zip(zip_code, ctx)
    return {"postal_code": z, "area_found": center is not None, "remembered": _remember_zip}


@mcp.tool(annotations=READ_ONLY)
async def price_ingredients(ingredients: list[str | Ingredient], price_tolerance: float = 0.2,
                            zip_code: str | None = None,
                            ctx: Context | None = None) -> ShoppingList:
    """Price a recipe's ingredients. Use this when the user gives a recipe or ingredient list.

    Pass one short Danish product search term per ingredient, either as a plain string
    (e.g. "løg") or with the amount the recipe needs: {"term": "hakket oksekød",
    "amount": "1 kg"}. Amounts use g/kg/ml/dl/l/stk.

    For each ingredient, returns products within `price_tolerance` (0.2 = 20%) above the
    cheapest, a cheapest price and a 'suggested' mid-range pick. Products are compared on
    price per kg/l/stk when pack sizes are known ('compared_on' says which). With an
    amount, each option gets 'packs' (how many to buy) and 'line_total'; totals use those.
    Without one, totals assume one pack of each item.

    If a postal code is given or already saved, also returns a suggested store (nearest
    Bilka, since prices are Bilka ToGo prices) and discounted near-expiry items matching
    the ingredients in nearby stores.

    Search is fuzzy: always check option titles match the ingredient (e.g. a search for
    'parmesan' may return other cheeses) and retry with a different term if not.
    """
    wanted = [i if isinstance(i, Ingredient) else Ingredient(term=i) for i in ingredients]
    wanted = [w.model_copy(update={"term": w.term.strip()}) for w in wanted if w.term.strip()]
    if not wanted:
        raise ToolError("ingredients must not be empty")
    _check_range("price_tolerance", price_tolerance, 0, 2)

    async def one(item: Ingredient) -> tuple[str, dict]:
        try:
            raw = await client().relevant_products(item.term)
        except SallingAPIError as e:
            return item.term, {"found": False, "error": str(e), "options": []}
        options = pricing.within_tolerance([pricing.trim_product(p) for p in raw], price_tolerance)
        return item.term, pricing.apply_amount(pricing.price_summary(options), item.amount)

    terms = [w.term for w in wanted]
    area = await _resolve_zip(zip_code, ctx, ask=False) if zip_code or _remembered_zip() else None
    pricing_job = asyncio.gather(*(one(w) for w in wanted))
    if area:
        results, stores = await asyncio.gather(pricing_job, _store_suggestion(*area, terms))
    else:
        results, stores = await pricing_job, None
    items = dict(results)
    out: dict[str, Any] = {
        "items": items,
        "total_cheapest": pricing.shopping_total(items, "cheapest"),
        "total_suggested": pricing.shopping_total(items),
        "not_found": [t for t, i in items.items() if not i.get("found")],
        "currency": "DKK",
        "note": PRICE_NOTE,
    }
    if stores:
        out.update(stores)
    return out


async def _store_suggestion(z: str, center: tuple[float, float] | None, terms: list[str]) -> dict:
    try:
        bilka, waste = await asyncio.gather(
            _stores_near(z, center, BILKA_RADIUS_KM, "bilka", 1),
            _food_waste_near(z, center, FOOD_WASTE_RADIUS_KM, terms))
        if bilka:
            suggested = {**bilka[0], "why": "Nearest Bilka: the prices above are Bilka ToGo prices."}
        else:
            nearest = await _stores_near(z, center, FOOD_WASTE_RADIUS_KM, None, 1)
            suggested = ({**nearest[0], "why": "Nearest store (no Bilka nearby): prices may differ."}
                         if nearest else None)
    except SallingAPIError as e:
        return {"postal_code": z, "store_error": str(e)}
    return {"postal_code": z, "suggested_store": suggested,
            "food_waste_matches": waste[:MAX_FOOD_WASTE_STORES], "distance_note": DISTANCE_NOTE}


def _product_list(raw: list[dict], limit: int) -> ProductList:
    return {"products": [pricing.trim_product(p) for p in raw[:limit]], "note": PRICE_NOTE}


@mcp.tool(annotations=READ_ONLY)
async def search_products(query: str, limit: int = 10) -> ProductList:
    """Search Bilka ToGo products by a Danish search term (e.g. 'mælk'). Use for a single
    product lookup or to browse options; use price_ingredients for a whole recipe."""
    _check_range("limit", limit, 1, MAX_LIST_LIMIT)
    return _product_list(await client().relevant_products(query), limit)


@mcp.tool(annotations=READ_ONLY)
async def get_similar_products(product_id: str, limit: int = 10) -> ProductList:
    """Find alternatives to a product (by product_id from another tool), e.g. a cheaper
    or organic substitute."""
    _check_range("limit", limit, 1, MAX_LIST_LIMIT)
    return _product_list(await client().similar_products(product_id), limit)


@mcp.tool(annotations=READ_ONLY)
async def get_frequently_bought_together(product_id: str, limit: int = 10) -> ProductList:
    """Products often bought together with a product (by product_id). Use for
    'what else might I need' suggestions."""
    _check_range("limit", limit, 1, MAX_LIST_LIMIT)
    return _product_list(await client().frequently_bought_together(product_id), limit)


@mcp.tool(annotations=READ_ONLY)
async def find_food_waste(zip_code: str | None = None, radius_km: float = 5,
                          ingredients: list[str] | None = None,
                          ctx: Context | None = None) -> FoodWasteResult:
    """Find discounted near-expiry items (food waste clearances) in stores near the user's
    postal code. These prices are real per-store prices. zip_code is a Danish postnummer
    (e.g. '8000'); if omitted, the saved one is used or the user is asked. Stores are
    sorted by approximate distance and include a map link. Optionally pass ingredient
    terms to only return matching items."""
    _check_range("radius_km", radius_km, 0.1, MAX_RADIUS_KM)
    z, center = await _resolve_zip(zip_code, ctx)
    stores = await _food_waste_near(z, center, radius_km, ingredients)
    return {"postal_code": z, "stores": stores, "currency": "DKK", "note": DISTANCE_NOTE}


async def _food_waste_near(z: str, center: tuple[float, float] | None, radius_km: float,
                           ingredients: list[str] | None) -> list[dict]:
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
    return stores


@mcp.tool(annotations=READ_ONLY)
async def get_store_food_waste(store_id: str) -> StoreFoodWaste:
    """List all discounted near-expiry items at one specific store (by store_id)."""
    raw = await client().store_food_waste(store_id)
    return {
        "store": pricing.trim_store(raw.get("store", {})),
        "clearances": [pricing.trim_clearance(c) for c in raw.get("clearances", [])],
        "currency": "DKK",
    }


async def _stores_near(z: str, center: tuple[float, float] | None, radius_km: float,
                       brand: str | None, limit: int) -> list[dict]:
    if center:
        # The API does not document its sort order, so fetch a wider page and sort here.
        raw = await client().stores(geo=_geo(center), radius=radius_km, brand=brand,
                                    country="dk", per_page=max(limit, STORE_FETCH_PAGE))
    else:
        raw = await client().stores(zip=z, brand=brand, country="dk", per_page=limit)
    stores = [pricing.add_distance(pricing.trim_store(s), center) for s in raw]
    stores.sort(key=lambda s: s.get("distance_km", float("inf")))
    return stores[:limit]


async def _nearby_stores(zip_code: str | None, radius_km: float, brand: str | None, limit: int,
                         max_limit: int, ctx: Context | None,
                         ) -> tuple[str, tuple[float, float] | None, list[dict]]:
    _check_range("radius_km", radius_km, 0.1, MAX_RADIUS_KM)
    _check_range("limit", limit, 1, max_limit)
    z, center = await _resolve_zip(zip_code, ctx)
    return z, center, await _stores_near(z, center, radius_km, brand, limit)


@mcp.tool(annotations=READ_ONLY)
async def search_stores(zip_code: str | None = None, radius_km: float = 5,
                        brand: str | None = None, limit: int = 10,
                        ctx: Context | None = None) -> StoreList:
    """Find Salling Group stores in Denmark near the user's postal code, nearest first,
    with approximate distance, today's hours, whether it is open now, and a map link.
    zip_code is a Danish postnummer (e.g. '8000'); if omitted, the saved one is used or
    the user is asked. brand: bilka, foetex, netto, salling, br, carlsjr, starbucks."""
    z, _, stores = await _nearby_stores(zip_code, radius_km, brand, limit, MAX_LIST_LIMIT, ctx)
    return {"postal_code": z, "stores": stores, "note": DISTANCE_NOTE}


def _save_map(png: bytes, z: str) -> Path:
    """Write the map for clients that cannot show images, and delete maps older than a day."""
    MAP_DIR.mkdir(exist_ok=True)
    cutoff = time.time() - MAP_FILE_MAX_AGE_S
    for old in MAP_DIR.glob("*.png"):
        if old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)
    path = MAP_DIR / f"stores-{z}.png"
    path.write_bytes(png)
    return path


@mcp.tool(annotations=READ_ONLY)
async def show_stores_map(zip_code: str | None = None, radius_km: float = 3,
                          brand: str | None = None, limit: int = 8,
                          ctx: Context | None = None) -> list:
    """Show a small map image of the nearest Salling Group stores around the user's postal
    code, so the user gets a rough idea of where they are. Stores are numbered dots
    (nearest = 1) colored by brand; the faint ring is the postal code area. Use when the
    user wants to see stores on a map. Same arguments as search_stores (limit up to 20)."""
    z, center, stores = await _nearby_stores(zip_code, radius_km, brand, limit, MAX_MAP_STORES, ctx)
    if not stores:
        return [{"postal_code": z, "stores": [], "note": "No stores found nearby."}]
    png = await asyncio.to_thread(mapimage.render_store_map, stores, center)
    path = await asyncio.to_thread(_save_map, png, z)
    legend = [{"n": i, "name": s["name"], "distance_km": s.get("distance_km"),
               "map_url": s.get("map_url")} for i, s in enumerate(stores, start=1)]
    return [Image(data=png, format="png"),
            {"postal_code": z, "image_file": str(path), "stores": legend, "note": DISTANCE_NOTE}]


@mcp.tool(annotations=READ_ONLY)
async def get_store_details(store_id: str) -> StoreDetails:
    """Details for one store (by store_id): address, opening hours for the coming week,
    whether it is open now, facilities and a map link. Includes distance if the user's
    postal code is known."""
    raw = await client().store(store_id)
    z = _remembered_zip()
    out = pricing.add_distance(pricing.trim_store(raw), _zip_centers.get(z) if z else None)
    out["hours"] = pricing.upcoming_hours(raw.get("hours"))
    out["attributes"] = {k: v for k, v in (raw.get("attributes") or {}).items() if v}
    return out


@mcp.prompt()
def recipe_shopping_list(recipe: str, postal_code: str = "") -> str:
    """Turn a recipe into a priced shopping list with a suggested store."""
    where = (f"The user's postal code is {postal_code}; pass it as zip_code."
             if postal_code else "If you know the user's postal code, pass it as zip_code.")
    return f"""Make a shopping list for this recipe, priced with the Salling Group tools.

1. Extract the ingredients. Skip basics most kitchens have (salt, pepper, water, oil)
   unless the user asks for them. Translate each to a short Danish search term and keep
   the amount the recipe needs, e.g. {{"term": "hakket oksekød", "amount": "500 g"}}.
2. Call price_ingredients once with all ingredients. {where}
3. Check each item's option titles match the ingredient; retry a mismatched or missing
   one with a different Danish term.
4. Reply with a short list: ingredient, product, packs, price. Then the total, the
   suggested store, and any matching food-waste deals nearby.
5. Say that prices are Bilka ToGo online prices, not exact in-store prices. Only
   food-waste prices are real per-store prices.

Recipe:
{recipe}"""


@mcp.prompt()
def cook_from_food_waste(postal_code: str = "", preferences: str = "") -> str:
    """Suggest meals built around discounted near-expiry food in nearby stores."""
    where = f"Use postal code {postal_code}." if postal_code else "Ask for the user's postal code if needed."
    wishes = f"\nThe user's preferences: {preferences}" if preferences else ""
    return f"""Suggest 1-3 meals the user can cook from discounted near-expiry items nearby.

1. Call find_food_waste. {where}
2. Pick a store with several useful items (prefer the nearest) and build meals around
   them. Note the stock and when each offer ends.
3. For the extra ingredients each meal needs, call price_ingredients.
4. Reply per meal: the discounted items and their store, the extra ingredients and
   their reference prices, and the rough total.{wishes}"""


def main() -> None:
    global _remember_zip
    _remember_zip = True  # stdio: this process serves a single user
    mcp.run()


if __name__ == "__main__":
    main()
