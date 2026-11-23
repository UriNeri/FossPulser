"""Render rough, labelled Mediterranean depth contours from the GMRT grid.

The transparent overlay uses the same equirectangular pixel grid as every other
map layer. Dashed contours mark 50, 100, 200, 500, 1000, 2000 and 3000 metre
isobaths. They are a coarse visual guide, not navigational bathymetry.

Run: `pixi run python pipeline/build_bathymetry_layer.py`
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pipeline.config import DATA_CURATED, LAND_BBOX, MAP_HEIGHT, MAP_WIDTH

FIELDS_NPZ = DATA_CURATED / "basemap_fields.npz"
BATHYMETRY_PNG = DATA_CURATED / "bathymetry_contours.png"
DEPTHS = (50, 100, 200, 500, 1000, 2000, 3000)


def dashed_line(draw: ImageDraw.ImageDraw, points, *, fill, width=1, dash=7, gap=5) -> None:
    """Draw a geographic contour as constant-distance screen-space dashes."""
    distance_to_dash = 0.0
    for start, end in zip(points, points[1:]):
        x0, y0 = start
        x1, y1 = end
        dx, dy = x1 - x0, y1 - y0
        length = float(np.hypot(dx, dy))
        if length < 1e-6:
            continue
        position = 0.0
        while position < length:
            step = min(dash if distance_to_dash <= dash else gap, length - position)
            if distance_to_dash <= dash:
                a = position / length
                b = (position + step) / length
                draw.line(
                    [(x0 + dx * a, y0 + dy * a), (x0 + dx * b, y0 + dy * b)],
                    fill=fill,
                    width=width,
                )
            position += step
            distance_to_dash += step
            if distance_to_dash >= dash + gap:
                distance_to_dash = 0.0


def build() -> None:
    fields = np.load(FIELDS_NPZ)
    elevation = fields["elevation"].astype(np.float64)
    # Elevation alone cannot distinguish the below-sea-level Jordan Valley
    # from the Mediterranean. Use the geographic land mask for marine water.
    marine = ~fields["land_mask"]
    elevation = np.where(marine, elevation, np.nan)
    west, south, east, north = LAND_BBOX
    lons = np.linspace(west, east, MAP_WIDTH)
    lats = np.linspace(north, south, MAP_HEIGHT)

    figure = plt.figure(figsize=(MAP_WIDTH / 100, MAP_HEIGHT / 100), dpi=100)
    axis = figure.add_axes((0, 0, 1, 1))
    axis.set_xlim(west, east)
    axis.set_ylim(south, north)
    axis.axis("off")
    contours = axis.contour(
        lons,
        lats,
        elevation,
        levels=[-depth for depth in reversed(DEPTHS)],
        colors=["#8bc6dc"],
        linewidths=0.65,
        linestyles="dashed",
        alpha=0.58,
    )
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", transparent=True, dpi=100)
    plt.close(figure)
    buffer.seek(0)
    image = Image.open(buffer).convert("RGBA").resize((MAP_WIDTH, MAP_HEIGHT), Image.Resampling.LANCZOS)

    # Label one long, on-screen segment per depth where possible. Labels are
    # deliberately sparse so the daily data remains the visual focus.
    labels = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(labels)
    font = ImageFont.load_default(size=13)
    for depth, path in zip(reversed(DEPTHS), contours.get_paths()):
        candidates = []
        for vertices in path.to_polygons(closed_only=False):
            if len(vertices) < 8:
                continue
            x = (vertices[:, 0] - west) / (east - west) * MAP_WIDTH
            y = (north - vertices[:, 1]) / (north - south) * MAP_HEIGHT
            inside = (x > 35) & (x < MAP_WIDTH - 70) & (y > 25) & (y < MAP_HEIGHT - 25)
            if inside.sum() >= 5:
                candidates.append((float(inside.sum()), x, y))
        if not candidates:
            continue
        _, x, y = max(candidates, key=lambda item: item[0])
        index = int(np.argmin(np.abs(x - MAP_WIDTH * 0.38)))
        px, py = float(x[index]), float(y[index])
        label = f"{depth} m"
        box = draw.textbbox((px, py), label, font=font, anchor="mm", stroke_width=1)
        draw.rounded_rectangle((box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2), radius=3, fill=(4, 12, 20, 205))
        draw.text((px, py), label, font=font, fill=(174, 220, 235, 235), anchor="mm")

    result = Image.alpha_composite(image, labels)
    # Remove antialiased contour/label halos from land as well.
    pixels = np.asarray(result).copy()
    pixels[..., 3] = np.where(marine, pixels[..., 3], 0)
    Image.fromarray(pixels, "RGBA").save(BATHYMETRY_PNG)
    print(f"[bathymetry-layer] wrote {BATHYMETRY_PNG} ({len(DEPTHS)} depth levels)")


if __name__ == "__main__":
    build()
