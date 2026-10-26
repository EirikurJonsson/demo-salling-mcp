from datetime import date

from salling_mcp import pricing


def p(price, pid="1"):
    return {"product_id": pid, "title": "x", "price": price, "link": None}


def test_within_tolerance_keeps_only_close_to_cheapest_sorted():
    out = pricing.within_tolerance([p(15), p(10), p(12), p(11.99), p(None)], 0.2)
    assert [o["price"] for o in out] == [10, 11.99, 12]


def test_within_tolerance_empty():
    assert pricing.within_tolerance([], 0.2) == []


def test_parse_size_units_and_multipacks():
    assert pricing.parse_size("HAKKET OKSEKØD 8-12% 500 G") == (0.5, "kg")
    assert pricing.parse_size("Yoghurt 4 x 125 g") == (0.5, "kg")
    assert pricing.parse_size("Sødmælk 1,5 L") == (1.5, "l")
    assert pricing.parse_size("Fløde 38% 2,5 dl") == (0.25, "l")
    assert pricing.parse_size("Æg 10 stk") == (10, "stk")
    assert pricing.parse_size("Sødmælk 3,5%") is None
    assert pricing.parse_size(None) is None


def test_trim_product_adds_unit_price():
    out = pricing.trim_product({"prod_id": "1", "description": "Oksekød 500 g", "price": 45.0})
    assert out["unit_price"] == 90.0 and out["unit"] == "kg"
    assert "unit_price" not in pricing.trim_product({"description": "Oksekød", "price": 45.0})


def test_within_tolerance_compares_per_kg_when_sizes_known():
    raw = [{"prod_id": "small", "description": "Oksekød 250 g", "price": 30.0},
           {"prod_id": "mid", "description": "Oksekød 500 g", "price": 45.0},
           {"prod_id": "big", "description": "Oksekød 1 kg", "price": 80.0},
           {"prod_id": "nosize", "description": "Oksekød", "price": 10.0}]
    out = pricing.within_tolerance([pricing.trim_product(r) for r in raw], 0.2)
    assert [o["product_id"] for o in out] == ["big", "mid"]  # 80 and 90 kr/kg; 120 kr/kg is out
    assert pricing.price_summary(out)["compared_on"] == "price per kg"


def test_within_tolerance_falls_back_to_pack_price_with_one_size():
    raw = [{"prod_id": "a", "description": "Løg 1 kg", "price": 12.0},
           {"prod_id": "b", "description": "Løg", "price": 10.0}]
    out = pricing.within_tolerance([pricing.trim_product(r) for r in raw], 0.2)
    assert [o["product_id"] for o in out] == ["b", "a"]
    assert pricing.price_summary(out)["compared_on"] == "pack price"


def test_price_summary_picks_middle_as_suggested():
    s = pricing.price_summary([p(10), p(11), p(12)])
    assert s["cheapest"] == 10 and s["suggested"]["price"] == 11


def test_shopping_total_skips_not_found():
    items = {"a": pricing.price_summary([p(10.5)]), "b": pricing.price_summary([])}
    assert pricing.shopping_total(items) == 10.5


def test_trim_store_flips_coordinates_and_reads_today():
    s = {"id": "s1", "name": "Netto", "brand": "netto",
         "address": {"street": "Vej 1", "zip": "8000", "city": "Aarhus"},
         "coordinates": [10.2, 56.1],
         "hours": [{"date": "2026-10-21", "type": "store", "open": "07", "close": "22", "closed": False}]}
    out = pricing.trim_store(s, today=date(2026, 10, 21))
    assert out["lat"] == 56.1 and out["lng"] == 10.2
    assert out["address"] == "Vej 1, 8000, Aarhus"
    assert out["today"] == {"open": "07", "close": "22"}


def test_matches_any_case_insensitive():
    assert pricing.matches_any("HAKKET OKSEKØD 8-12%", ["oksekød"])
    assert not pricing.matches_any("Agurk", ["oksekød"])


def test_matches_any_compound_head_noun_not_brand_prefix():
    assert pricing.matches_any("PISKEFLØDE 38% LØGISMOSE", ["fløde"])
    assert not pricing.matches_any("VANILJE YOGHURT LØGISMOSE", ["løg"])


def test_haversine_and_centroid():
    assert round(pricing.haversine_km(55.6761, 12.5683, 56.1629, 10.2039)) == 157
    assert pricing.centroid([{"coordinates": [10, 56]}, {"coordinates": [12, 58]}, {}]) == (57, 11)
    assert pricing.centroid([]) is None


def test_add_distance_adds_map_link_and_distance():
    s = pricing.add_distance({"lat": 56.0, "lng": 10.0}, (56.0, 10.0))
    assert s["distance_km"] == 0.0 and "mlat=56.00000" in s["map_url"]
    assert "distance_km" not in pricing.add_distance({"lat": 56.0, "lng": 10.0}, None)
