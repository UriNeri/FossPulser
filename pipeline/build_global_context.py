"""Build a coarse, whole-world Natural Earth backdrop for zoomed-out context.

The detailed regional basemap remains a separate raster. Both use the same
longitude/latitude-to-pixel scale, so the app can position this image without
resampling or changing the regional map's projection.

Run: `pixi run python pipeline/build_global_context.py`
"""
from __future__ import annotations

import shapefile
from PIL import Image, ImageDraw

from pipeline.config import (
    DATA_RAW,
    GLOBAL_CONTEXT_PNG,
    GLOBAL_HEIGHT,
    GLOBAL_WIDTH,
    LAND_BBOX,
    MAP_WIDTH,
)
from pipeline.fetch_boundaries import _find_shp

PIXELS_PER_DEGREE = MAP_WIDTH / (LAND_BBOX[2] - LAND_BBOX[0])

SEA = (5, 13, 23)
LAND = (35, 39, 43)
COAST = (91, 105, 112)
BORDER = (112, 119, 128)


def project(lon: float, lat: float) -> tuple[float, float]:
    return ((lon + 180.0) * GLOBAL_WIDTH / 360.0, (90.0 - lat) * GLOBAL_HEIGHT / 180.0)


def build() -> None:
    reader = shapefile.Reader(_find_shp(DATA_RAW / "ne_countries"))
    image = Image.new("RGB", (GLOBAL_WIDTH, GLOBAL_HEIGHT), SEA)
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
            # Natural Earth country polygons also supply the coarse political
            # boundaries requested for the global context.
            draw.line(points + [points[0]], fill=BORDER, width=1)
    image.save(GLOBAL_CONTEXT_PNG, optimize=True)
    print(
        f"[global-context] wrote {GLOBAL_CONTEXT_PNG} {image.size}; "
        f"regional scale {PIXELS_PER_DEGREE:.6f} px/degree"
    )


if __name__ == "__main__":
    build()
