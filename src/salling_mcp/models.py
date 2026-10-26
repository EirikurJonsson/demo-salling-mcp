"""Tool output shapes, published to clients as each tool's output schema.

extra="allow" keeps any field that is not declared here in the structured output, so a
field missing from these types is never silently dropped (it just lacks a schema).
Python 3.11 needs typing_extensions.TypedDict for pydantic to build the schema.
"""
from typing import Any, NotRequired

from pydantic import ConfigDict, with_config
from typing_extensions import TypedDict

OPEN = ConfigDict(extra="allow")


@with_config(OPEN)
class Product(TypedDict):
    product_id: str | int | None
    title: str | None
    price: float | None
    link: str | None
    unit_price: NotRequired[float]
    unit: NotRequired[str]
    pack_size: NotRequired[float]
    packs: NotRequired[int]
    line_total: NotRequired[float]
    packs_note: NotRequired[str]


@with_config(OPEN)
class DayHours(TypedDict):
    date: NotRequired[str]
    weekday: NotRequired[str]
    open: NotRequired[str | None]
    close: NotRequired[str | None]
    closed: NotRequired[bool]


@with_config(OPEN)
class Store(TypedDict):
    store_id: str | None
    name: str | None
    brand: str | None
    address: str
    lat: float | None
    lng: float | None
    distance_km: NotRequired[float]
    map_url: NotRequired[str]
    today: NotRequired[DayHours]
    open_now: NotRequired[bool]


@with_config(OPEN)
class SuggestedStore(Store):
    why: str


@with_config(OPEN)
class StoreDetails(Store):
    hours: list[DayHours]
    attributes: dict[str, Any]


@with_config(OPEN)
class Clearance(TypedDict):
    product: str | None
    category: str | None
    new_price: float | None
    original_price: float | None
    percent_discount: float | None
    stock: float | None
    stock_unit: str | None
    ends: str | None


@with_config(OPEN)
class StoreClearances(TypedDict):
    store: Store
    clearances: list[Clearance]


@with_config(OPEN)
class IngredientPrice(TypedDict):
    found: bool
    options: list[Product]
    compared_on: NotRequired[str]
    cheapest: NotRequired[float]
    suggested: NotRequired[Product]
    amount: NotRequired[str]
    amount_note: NotRequired[str]
    error: NotRequired[str]


@with_config(OPEN)
class ShoppingList(TypedDict):
    items: dict[str, IngredientPrice]
    total_cheapest: float
    total_suggested: float
    not_found: list[str]
    currency: str
    note: str
    postal_code: NotRequired[str]
    suggested_store: NotRequired[SuggestedStore | None]
    food_waste_matches: NotRequired[list[StoreClearances]]
    distance_note: NotRequired[str]
    store_error: NotRequired[str]


@with_config(OPEN)
class ProductList(TypedDict):
    products: list[Product]
    note: str


@with_config(OPEN)
class PostalCodeResult(TypedDict):
    postal_code: str
    area_found: bool
    remembered: bool


@with_config(OPEN)
class FoodWasteResult(TypedDict):
    postal_code: str
    stores: list[StoreClearances]
    currency: str
    note: str


@with_config(OPEN)
class StoreFoodWaste(TypedDict):
    store: Store
    clearances: list[Clearance]
    currency: str


@with_config(OPEN)
class StoreList(TypedDict):
    postal_code: str
    stores: list[Store]
    note: str
