"""Render a small static map of nearby stores (PNG) with subtle numbered dot markers."""
import contextlib
import sys
from io import BytesIO

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps
from staticmap import CircleMarker, StaticMap
from staticmap.staticmap import _lat_to_y, _lon_to_x

TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
ATTRIBUTION = "© OpenStreetMap contributors"
USER_AGENT = "salling-mcp/0.1 (store map)"

BRAND_COLORS = {
    "netto": "#d9a400",
    "foetex": "#1d4f91",
    "bilka": "#0077c8",
    "salling": "#222222",
    "br": "#e2001a",
    "carlsjr": "#c8102e",
    "starbucks": "#00704a",
}
DEFAULT_COLOR = "#555555"
SCALE = 2  # draw at 2x and downsample for anti-aliased dots


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except OSError:
        return ImageFont.load_default(size)


def render_store_map(stores: list[dict], center: tuple[float, float] | None,
                     width: int = 640, height: int = 420) -> bytes:
    """stores: trimmed stores (lat/lng/brand) in display order. center: (lat, lng) of the
    postal code area, drawn as a faint ring. Returns PNG bytes."""
    points = [(s["lng"], s["lat"]) for s in stores if s.get("lat") is not None and s.get("lng") is not None]
    if center:
        points.append((center[1], center[0]))
    if not points:
        raise ValueError("No store locations to draw.")

    m = StaticMap(width, height, padding_x=40, padding_y=40, url_template=TILES,
                  headers={"User-Agent": USER_AGENT}, tile_request_timeout=10)
    for p in points:  # invisible: only used so the map fits all points
        m.add_marker(CircleMarker(p, "#00000000", 0))
    zoom = min(m._calculate_zoom(), 16)
    # Fade the basemap to light gray so the small markers stand out without being loud.
    # staticmap print()s failed tile requests; stdout is the MCP stdio channel.
    with contextlib.redirect_stdout(sys.stderr):
        rendered = m.render(zoom=zoom)
    base = ImageOps.grayscale(rendered)
    base = ImageEnhance.Contrast(base).enhance(0.55)
    base = Image.blend(base.convert("RGBA"), Image.new("RGBA", base.size, "white"), 0.25)

    def px(lng: float, lat: float) -> tuple[int, int]:
        return (m._x_to_px(_lon_to_x(lng, m.zoom)) * SCALE,
                m._y_to_px(_lat_to_y(lat, m.zoom)) * SCALE)

    overlay = Image.new("RGBA", (width * SCALE, height * SCALE), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    if center:
        x, y = px(center[1], center[0])
        r = 14 * SCALE
        d.ellipse((x - r, y - r, x + r, y + r), fill=(80, 80, 80, 25), outline=(80, 80, 80, 140), width=SCALE)

    label_font = _font(10 * SCALE)
    for i, s in enumerate(stores, start=1):
        if s.get("lat") is None or s.get("lng") is None:
            continue
        x, y = px(s["lng"], s["lat"])
        r = 4 * SCALE
        ring = r + int(1.5 * SCALE)
        d.ellipse((x - ring, y - ring, x + ring, y + ring), fill="white")
        d.ellipse((x - r, y - r, x + r, y + r), fill=BRAND_COLORS.get(s.get("brand") or "", DEFAULT_COLOR))
        d.text((x + ring + 2 * SCALE, y), str(i), font=label_font, fill=(40, 40, 40, 230),
               anchor="lm", stroke_width=2 * SCALE // 2, stroke_fill="white")

    overlay = overlay.resize((width, height), Image.LANCZOS)
    img = Image.alpha_composite(base, overlay)

    d = ImageDraw.Draw(img)
    small = _font(9)
    tw = d.textlength(ATTRIBUTION, font=small)
    d.rectangle((width - tw - 8, height - 14, width, height), fill=(255, 255, 255, 200))
    d.text((width - tw - 4, height - 13), ATTRIBUTION, font=small, fill=(90, 90, 90))

    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
