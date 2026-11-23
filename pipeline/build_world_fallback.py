"""Generate the embedded fallback world map for the desktop app.

The app shows this when no local bundle is loaded so the world view is never
blank. Output is committed as app/assets/world_context.png and included in
the binary with include_bytes!.

Run: `pixi run python pipeline/build_world_fallback.py`
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import shapefile
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "assets" / "world_context.png"

WIDTH, HEIGHT = 1440, 720
SEA = (5, 13, 23)
LAND = (35, 39, 43)
COAST = (91, 105, 112)


def project(lon: float, lat: float) -> tuple[float, float]:
    return ((lon + 180.0) * WIDTH / 360.0, (90.0 - lat) * HEIGHT / 180.0)


def main() -> None:
    archive = ROOT / "data" / "cache" / "natural-earth" / "countries.zip"
    with zipfile.ZipFile(archive) as zipped:
        shp_name = next(n for n in zipped.namelist() if n.endswith(".shp"))
        shp_bytes = zipped.read(shp_name)
        dbf_name = next(n for n in zipped.namelist() if n.endswith(".dbf"))
        dbf_bytes = zipped.read(dbf_name)
    reader = shapefile.Reader(shp=io.BytesIO(shp_bytes), dbf=io.BytesIO(dbf_bytes))
    image = Image.new("RGB", (WIDTH, HEIGHT), SEA)
    draw = ImageDraw.Draw(image)
    for shape in reader.iterShapes():
        if not shape.points:
            continue
        parts = list(shape.parts) + [len(shape.points)]
        for start, end in zip(parts, parts[1:]):
            points = [project(lon, lat) for lon, lat in shape.points[start:end]]
            if len(points) < 3:
                continue
            draw.polygon(points, fill=LAND)
            draw.line(points + [points[0]], fill=COAST, width=1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, optimize=True)
    print(f"[world-fallback] wrote {OUT} {image.size}")


if __name__ == "__main__":
    main()
