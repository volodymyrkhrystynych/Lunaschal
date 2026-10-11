"""Draw one day's trail and journal pins over map tiles, as a PNG.

Web Mercator throughout, the projection every slippy-map tile is cut in, so a
point's pixel is the same arithmetic the tile grid uses. The tile source is a
parameter (`fetch_tile(z, x, y) -> Image | None`) so tests draw without a
network; a missing tile is a plain background square, never an error.

Lines are drawn on a 2x overlay and scaled down, which is the antialiasing
Pillow's ImageDraw doesn't do on its own. Tiles go on the base unscaled,
because upscaling a tile to downscale it again only blurs it.
"""
import io
import math
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from backend.location.trail import Fix, Segment

TILE = 256
MIN_ZOOM, MAX_ZOOM = 2, 16
SIZE = (1200, 800)
PAD = 70
SS = 2  # overlay supersampling

ROUTE = (37, 99, 235)
ENTRY = (234, 88, 12)
WHITE = (255, 255, 255)
BACKGROUND = (232, 234, 237)
# Tiles are washed toward white so the trail, not the street names, is what
# the eye lands on.
TILE_WASH = 0.3
ATTRIBUTION = '© OpenStreetMap contributors'

TileFetcher = Callable[[int, int, int], 'Image.Image | None']


def world_px(lat: float, lon: float, z: int) -> tuple[float, float]:
    """A coordinate's pixel in the whole-world image at zoom z."""
    scale = TILE * 2 ** z
    lat = max(min(lat, 85.05112878), -85.05112878)
    s = math.sin(math.radians(lat))
    return (lon + 180) / 360 * scale, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * scale


def fit_zoom(coords: list[tuple[float, float]], size=SIZE, pad=PAD) -> int:
    """The closest zoom at which every coordinate fits inside the padded frame."""
    w, h = size[0] - 2 * pad, size[1] - 2 * pad
    for z in range(MAX_ZOOM, MIN_ZOOM - 1, -1):
        xs, ys = zip(*(world_px(lat, lon, z) for lat, lon in coords))
        if max(xs) - min(xs) <= w and max(ys) - min(ys) <= h:
            return z
    return MIN_ZOOM


def _dashed(draw: ImageDraw.ImageDraw, a, b, fill, width, dash=14 * SS, gap=10 * SS) -> None:
    length = math.dist(a, b)
    if length == 0:
        return
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    pos = 0.0
    while pos < length:
        end = min(pos + dash, length)
        draw.line([(a[0] + ux * pos, a[1] + uy * pos), (a[0] + ux * end, a[1] + uy * end)], fill=fill, width=width)
        pos = end + gap


def _dot(draw: ImageDraw.ImageDraw, p, r, fill, ring=3) -> None:
    r, ring = r * SS, ring * SS
    draw.ellipse([p[0] - r - ring, p[1] - r - ring, p[0] + r + ring, p[1] + r + ring], fill=WHITE)
    draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=fill)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has only the bitmap font
        return ImageFont.load_default()


def render(fixes: list[Fix], segments: list[Segment], places: list[Fix],
           pins: list[tuple[float, float]], fetch_tile: TileFetcher, size=SIZE) -> bytes:
    coords = [(f.lat, f.lon) for f in fixes] + list(pins)
    if not coords:
        raise ValueError('nothing to draw')
    z = fit_zoom(coords, size)
    xs, ys = zip(*(world_px(lat, lon, z) for lat, lon in coords))
    left = (min(xs) + max(xs)) / 2 - size[0] / 2
    top = (min(ys) + max(ys)) / 2 - size[1] / 2

    base = Image.new('RGB', size, BACKGROUND)
    tiles = 2 ** z
    wash = Image.new('RGB', (TILE, TILE), WHITE)
    for ty in range(math.floor(top / TILE), math.floor((top + size[1]) / TILE) + 1):
        if not 0 <= ty < tiles:
            continue
        for tx in range(math.floor(left / TILE), math.floor((left + size[0]) / TILE) + 1):
            tile = fetch_tile(z, tx % tiles, ty)
            if tile is not None:
                tile = Image.blend(tile.convert('RGB').resize((TILE, TILE)), wash, TILE_WASH)
                base.paste(tile, (round(tx * TILE - left), round(ty * TILE - top)))

    overlay = Image.new('RGBA', (size[0] * SS, size[1] * SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def px(lat, lon):
        x, y = world_px(lat, lon, z)
        return (x - left) * SS, (y - top) * SS

    for seg in segments:
        if seg.style == 'jump':
            _dashed(draw, px(seg.a.lat, seg.a.lon), px(seg.b.lat, seg.b.lon), (*ROUTE, 170), 3 * SS)
    # Casing first, then every route on top, so crossings don't show seams.
    tracks = [(px(s.a.lat, s.a.lon), px(s.b.lat, s.b.lon)) for s in segments if s.style == 'track']
    for width, fill in ((10 * SS, WHITE), (6 * SS, ROUTE)):
        r = width / 2
        for a, b in tracks:
            draw.line([a, b], fill=fill, width=width)
            for p in (a, b):
                draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=fill)
    for place in places:
        _dot(draw, px(place.lat, place.lon), 8, ROUTE)
    # A day with fixes but no route still shows where they were.
    if not segments and fixes:
        for f in fixes:
            _dot(draw, px(f.lat, f.lon), 5, ROUTE, ring=2)
    for lat, lon in pins:
        _dot(draw, px(lat, lon), 9, ENTRY)

    base = base.convert('RGBA')
    base.alpha_composite(overlay.resize(size, Image.LANCZOS))
    label = ImageDraw.Draw(base)
    font = _font(14)
    box = label.textbbox((0, 0), ATTRIBUTION, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    x, y = size[0] - tw - 10, size[1] - th - 10
    label.rectangle([x - 5, y - 4, x + tw + 5, y + th + 6], fill=(255, 255, 255, 210))
    label.text((x, y - box[1]), ATTRIBUTION, fill=(60, 60, 60), font=font)

    out = io.BytesIO()
    base.convert('RGB').save(out, 'PNG', optimize=True)
    return out.getvalue()
