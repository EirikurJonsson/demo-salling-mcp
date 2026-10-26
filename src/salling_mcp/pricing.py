import math
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

DK_TZ = ZoneInfo("Europe/Copenhagen")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

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
        out["pack_size"] = round(size[0], 4)
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


def packs_needed(need: tuple[float, str], option: dict) -> int | None:
    """Packs to buy for a needed amount, or None if the pack size is unknown or in
    another unit. 1 kg of 400 g packs -> 3."""
    if option.get("unit") != need[1] or not option.get("pack_size"):
        return None
    return max(1, math.ceil(need[0] / option["pack_size"] - 1e-9))


def apply_amount(summary: dict, amount: str | None) -> dict:
    if not amount or not summary.get("found"):
        return summary
    summary["amount"] = amount
    need = parse_size(amount)
    if need is None:
        summary["amount_note"] = "Could not read the amount; each option is priced as one pack."
        return summary
    for option in summary["options"]:
        packs = packs_needed(need, option)
        option["packs"] = packs or 1
        option["line_total"] = round(option["price"] * option["packs"], 2)
        if packs is None:
            option["packs_note"] = "Pack size unknown, counted as one pack."
    return summary


def _line_total(option: dict) -> float:
    return option.get("line_total", option["price"])


def shopping_total(items: dict[str, dict], key: str = "suggested") -> float:
    """Sum of the 'suggested' (or 'cheapest' = first option) pick per found item,
    using line totals when an amount was given."""
    picks = (i["options"][0] if key == "cheapest" else i[key] for i in items.values() if i.get("found"))
    return round(sum(_line_total(p) for p in picks), 2)


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


def now_dk() -> datetime:
    return datetime.now(DK_TZ)


def _clock(value: Any) -> time | None:
    """'07', '07:30' or an ISO datetime like '2026-10-21T07:00:00' -> time(7, 0)."""
    if not isinstance(value, str) or not value:
        return None
    text = value.split("T")[-1]
    try:
        parts = [int(x) for x in text.split(":")[:2]]
        return time(parts[0], parts[1] if len(parts) > 1 else 0)
    except ValueError:
        return None


def _store_hours(hours: list[dict] | None) -> dict[str, dict]:
    return {h["date"]: h for h in hours or []
            if h.get("date") and h.get("type", "store") == "store"}


def _day(h: dict) -> dict:
    if h.get("closed"):
        return {"closed": True}
    opens, closes = _clock(h.get("open")), _clock(h.get("close"))
    return {"open": opens.strftime("%H:%M") if opens else None,
            "close": closes.strftime("%H:%M") if closes else None}


def todays_hours(hours: list[dict] | None, today: date | None = None) -> dict | None:
    h = _store_hours(hours).get((today or now_dk().date()).isoformat())
    return _day(h) if h else None


def upcoming_hours(hours: list[dict] | None, today: date | None = None, days: int = 7) -> list[dict]:
    by_date, start = _store_hours(hours), today or now_dk().date()
    out = []
    for d in (start + timedelta(n) for n in range(days)):
        if d.isoformat() in by_date:
            out.append({"date": d.isoformat(), "weekday": WEEKDAYS[d.weekday()],
                        **_day(by_date[d.isoformat()])})
    return out


def is_open(hours: list[dict] | None, now: datetime | None = None) -> bool | None:
    """True/False if today's hours are known, else None. A closing time at or before
    the opening time (e.g. 07:00-02:00) means the store closes after midnight, so at
    01:00 yesterday's hours decide."""
    now = now or now_dk()
    by_date, t = _store_hours(hours), now.time().replace(tzinfo=None)
    yesterday = by_date.get((now.date() - timedelta(1)).isoformat())
    if yesterday and not yesterday.get("closed"):
        y_open, y_close = _clock(yesterday.get("open")), _clock(yesterday.get("close"))
        if y_open and y_close and y_close <= y_open and t < y_close:
            return True
    h = by_date.get(now.date().isoformat())
    if not h:
        return None
    if h.get("closed"):
        return False
    opens, closes = _clock(h.get("open")), _clock(h.get("close"))
    if opens is None or closes is None:
        return None
    return opens <= t < closes if closes > opens else t >= opens


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


def trim_store(s: dict, now: datetime | None = None) -> dict:
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
    now = now or now_dk()
    hours = todays_hours(s.get("hours"), now.date())
    if hours:
        out["today"] = hours
    open_now = is_open(s.get("hours"), now)
    if open_now is not None:
        out["open_now"] = open_now
    return out


def matches_any(text: str | None, terms: list[str]) -> bool:
    """True if any term ends a word in text. Danish compounds put the head noun last,
    so 'fløde' matches 'PISKEFLØDE' but 'løg' does not match 'LØGISMOSE'."""
    t = (text or "").lower()
    return any(re.search(rf"{re.escape(term.lower().strip())}\b", t) for term in terms if term.strip())
