import math
import re
from collections import Counter
from datetime import date
from typing import Any

# unit -> (base unit, factor to base)
_UNITS = {"kg": ("kg", 1.0), "g": ("kg", 0.001), "gr": ("kg", 0.001),
          "l": ("l", 1.0), "ltr": ("l", 1.0), "dl": ("l", 0.1), "cl": ("l", 0.01),
          "ml": ("l", 0.001), "stk": ("stk", 1.0)}
_SIZE_RE = re.compile(r"(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*(kg|gr|g|ltr|dl|cl|ml|l|stk)\b",
                      re.IGNORECASE)


def parse_size(text: str | None) -> tuple[float, str] | None:
    """Pack size from a product title, as (amount in base unit, base unit).
    '4 x 125 g' -> (0.5, 'kg'), '1,5 L' -> (1.5, 'l'), '10 stk' -> (10, 'stk')."""
    m = _SIZE_RE.search(text or "")
    if not m:
        return None
    count = int(m.group(1)) if m.group(1) else 1
    base, factor = _UNITS[m.group(3).lower()]
    amount = count * float(m.group(2).replace(",", ".")) * factor
    return (amount, base) if amount > 0 else None


def trim_product(p: dict) -> dict:
    title = p.get("description") or p.get("title")
    out = {
        "product_id": p.get("prod_id"),
        "title": title,
        "price": p.get("price"),
        "link": p.get("link"),
    }
    size = parse_size(f"{p.get('title') or ''} {p.get('description') or ''}")
    if size and isinstance(out["price"], (int, float)):
        out["unit_price"] = round(out["price"] / size[0], 2)
        out["unit"] = size[1]
    return out


def comparison_unit(products: list[dict]) -> str | None:
    """Unit to compare on ('kg', 'l', 'stk'), or None to compare pack prices.
    Unit prices are used when at least two priced products share a unit."""
    counts = Counter(p["unit"] for p in products
                     if isinstance(p.get("price"), (int, float)) and p.get("unit"))
    if not counts:
        return None
    unit, n = counts.most_common(1)[0]
    return unit if n >= 2 else None


def within_tolerance(products: list[dict], tolerance: float = 0.2) -> list[dict]:
    """Products within `tolerance` above the cheapest, sorted cheapest first.

    Compares price per kg/l/stk when pack sizes can be read from titles (so a 1 kg
    pack at 80 kr beats a 250 g pack at 30 kr); otherwise compares pack prices.
    Products whose size is unknown or in another unit are left out of a unit comparison.
    """
    priced = [p for p in products if isinstance(p.get("price"), (int, float))]
    unit = comparison_unit(priced)
    if unit:
        priced = [p for p in priced if p.get("unit") == unit]
    key = (lambda p: p["unit_price"]) if unit else (lambda p: p["price"])
    if not priced:
        return []
    limit = min(key(p) for p in priced) * (1 + tolerance)
    return sorted((p for p in priced if key(p) <= limit), key=key)


def price_summary(options: list[dict]) -> dict:
    if not options:
        return {"found": False, "options": []}
    unit = comparison_unit(options)
    return {
        "found": True,
        "compared_on": f"price per {unit}" if unit else "pack price",
        "cheapest": options[0]["price"],
        "suggested": options[len(options) // 2],
        "options": options,
    }


def shopping_total(items: dict[str, dict], key: str = "suggested") -> float:
    return round(sum(i[key]["price"] for i in items.values() if i.get("found")), 2)


def trim_clearance(c: dict) -> dict:
    offer, product = c.get("offer", {}), c.get("product", {})
    return {
        "product": product.get("description"),
        "category": (product.get("categories") or {}).get("da"),
        "new_price": offer.get("newPrice"),
        "original_price": offer.get("originalPrice"),
        "percent_discount": offer.get("percentDiscount"),
        "stock": offer.get("stock"),
        "stock_unit": offer.get("stockUnit"),
        "ends": offer.get("endTime"),
    }


def todays_hours(hours: list[dict] | None, today: date | None = None) -> dict | None:
    today_s = (today or date.today()).isoformat()
    for h in hours or []:
        if h.get("date") == today_s and h.get("type", "store") == "store":
            if h.get("closed"):
                return {"closed": True}
            return {"open": h.get("open"), "close": h.get("close")}
    return None


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def centroid(stores: list[dict]) -> tuple[float, float] | None:
    """Average (lat, lng) of raw API stores (coordinates are [lng, lat])."""
    pts = [s["coordinates"] for s in stores
           if isinstance(s.get("coordinates"), list) and len(s["coordinates"]) == 2]
    if not pts:
        return None
    return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))


def map_url(lat: float, lng: float) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lng:.5f}#map=16/{lat:.5f}/{lng:.5f}"


def add_distance(store: dict, origin: tuple[float, float] | None) -> dict:
    """Add distance_km (from origin) and map_url to a trimmed store."""
    lat, lng = store.get("lat"), store.get("lng")
    if lat is None or lng is None:
        return store
    store["map_url"] = map_url(lat, lng)
    if origin:
        store["distance_km"] = round(haversine_km(origin[0], origin[1], lat, lng), 1)
    return store


def trim_store(s: dict, today: date | None = None) -> dict:
    addr = s.get("address") or {}
    coords = s.get("coordinates") or [None, None]
    out: dict[str, Any] = {
        "store_id": s.get("id"),
        "name": s.get("name"),
        "brand": s.get("brand"),
        "address": ", ".join(x for x in (addr.get("street"), addr.get("zip"), addr.get("city")) if x),
        "lat": coords[1] if len(coords) > 1 else None,
        "lng": coords[0],
    }
    if s.get("distance_km") is not None:
        out["distance_km"] = s["distance_km"]
    hours = todays_hours(s.get("hours"), today)
    if hours:
        out["today"] = hours
    return out


def matches_any(text: str | None, terms: list[str]) -> bool:
    """True if any term ends a word in text. Danish compounds put the head noun last,
    so 'fløde' matches 'PISKEFLØDE' but 'løg' does not match 'LØGISMOSE'."""
    t = (text or "").lower()
    return any(re.search(rf"{re.escape(term.lower().strip())}\b", t) for term in terms if term.strip())
