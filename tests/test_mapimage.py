from io import BytesIO

from PIL import Image
from staticmap import StaticMap

from salling_mcp import mapimage


def test_render_store_map_returns_png_without_network(monkeypatch):
    tile = BytesIO()
    Image.new("RGB", (256, 256), "white").save(tile, format="PNG")

    monkeypatch.setattr(StaticMap, "get", lambda self, url, **kw: (200, tile.getvalue()))
    stores = [{"lat": 55.696, "lng": 12.546, "brand": "netto"},
              {"lat": 55.699, "lng": 12.554, "brand": "foetex"}]
    png = mapimage.render_store_map(stores, (55.695, 12.550))
    img = Image.open(BytesIO(png))
    assert img.format == "PNG" and img.size == (640, 420)
