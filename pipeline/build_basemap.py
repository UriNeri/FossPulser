"""Build the dark, stylised hillshade basemap (land relief + sea mask) that
every other 2D layer is composited onto. Run once; cached to
data/curated/basemap.png + data/curated/basemap_fields.npz (elevation/land
mask resampled to the map grid and reused by the raster-layer builders).

Run: `pixi run python pipeline/build_basemap.py`
"""
from __future__ import annotations

import json

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from scipy.ndimage import zoom

from pipeline.config import (
    BATHYMETRY_TIF,
    BOUNDARIES_GEOJSON,
    DATA_CURATED,
    LAKES_GEOJSON,
    LAND_BBOX,
    MAP_HEIGHT,
    MAP_WIDTH,
)
from pipeline.projection import DEFAULT as proj

BASEMAP_PNG = DATA_CURATED / "basemap.png"
LAKE_OUTLINES_PNG = DATA_CURATED / "lake_outlines.png"
ISRAEL_MASK_PNG = DATA_CURATED / "israel_mask.png"
FIELDS_NPZ = DATA_CURATED / "basemap_fields.npz"

# Dark theme palette: near-black navy sea and muted warm-dark land relief.
SEA_DEEP = np.array([4, 10, 18])
SEA_SHALLOW = np.array([10, 28, 36])
LAND_LOW = np.array([30, 26, 24])
LAND_HIGH = np.array([58, 50, 42])
# Legible white country boundaries and bright, geographically accurate lakes.
BOUNDARY_COLOR = np.array([235, 235, 242])
BOUNDARY_ALPHA = 0.65
BOUNDARY_WIDTH = 3
LAKE_FILL = (8, 35, 50, 255)
LAKE_OUTLINE = np.array([170, 240, 255])
LAKE_OUTLINE_WIDTH = 5
LAKE_OUTLINE_SHADOW_WIDTH = 9


def load_elevation_on_grid() -> np.ndarray:
    with rasterio.open(BATHYMETRY_TIF) as ds:
        arr = ds.read(1).astype(np.float64)
        bounds = ds.bounds
    # Fill the handful of nodata/NaN cells with their nearest valid neighbour
    # average (cheap: replace with global mean of finite values; the gap
    # fraction is <0.2% so this has no visible effect).
    finite = np.isfinite(arr)
    if not finite.all():
        arr = np.where(finite, arr, np.nanmean(arr))
    zoom_y = MAP_HEIGHT / arr.shape[0]
    zoom_x = MAP_WIDTH / arr.shape[1]
    resampled = zoom(arr, (zoom_y, zoom_x), order=1)
    resampled = resampled[:MAP_HEIGHT, :MAP_WIDTH]
    if resampled.shape != (MAP_HEIGHT, MAP_WIDTH):
        pad_y = MAP_HEIGHT - resampled.shape[0]
        pad_x = MAP_WIDTH - resampled.shape[1]
        resampled = np.pad(resampled, ((0, max(pad_y, 0)), (0, max(pad_x, 0))), mode="edge")
        resampled = resampled[:MAP_HEIGHT, :MAP_WIDTH]
    print(f"[basemap] bathymetry bounds {bounds}, resampled to {resampled.shape}")
    return resampled


def hillshade(elevation: np.ndarray, *, azimuth_deg=315.0, altitude_deg=45.0, z_factor=2.5) -> np.ndarray:
    # Convert pixel spacing to metres at the region's mean latitude so the
    # gradient isn't distorted by the geographic (degree) grid.
    west, south, east, north = LAND_BBOX
    mean_lat = np.radians((south + north) / 2.0)
    m_per_deg_lon = 111_320.0 * np.cos(mean_lat)
    m_per_deg_lat = 111_320.0
    dx = (east - west) / elevation.shape[1] * m_per_deg_lon
    dy = (north - south) / elevation.shape[0] * m_per_deg_lat
    gy, gx = np.gradient(elevation * z_factor, dy, dx)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az = np.radians(azimuth_deg)
    alt = np.radians(altitude_deg)
    shaded = np.cos(alt) * np.cos(slope) + np.sin(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(shaded, 0.0, 1.0)


def rasterize_country_mask(name: str) -> np.ndarray:
    """Rasterize a single country's polygon into a binary mask."""
    data = json.loads(BOUNDARIES_GEOJSON.read_text())
    mask_img = Image.new("L", (MAP_WIDTH, MAP_HEIGHT), 0)
    draw = ImageDraw.Draw(mask_img)
    for feat in data["features"]:
        if feat["properties"].get("name") != name:
            continue
        geom = feat["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            for ring in poly:
                pts = [proj.lonlat_to_px(lon, lat) for lon, lat in ring]
                draw.polygon(pts, fill=255)
    return np.asarray(mask_img)


def rasterize_land_mask() -> np.ndarray:
    """Rasterize all Natural Earth country polygons into a single land mask.

    This replaces the old elevation-derived mask, which classified every
    below-sea-level pixel (including the Jordan Valley) as water. Land is now
    defined by geographic country polygons; lakes and rivers are added
    separately by the chlorophyll layer.
    """
    data = json.loads(BOUNDARIES_GEOJSON.read_text())
    mask_img = Image.new("L", (MAP_WIDTH, MAP_HEIGHT), 0)
    draw = ImageDraw.Draw(mask_img)
    for feat in data["features"]:
        geom = feat["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            for ring in poly:
                pts = [proj.lonlat_to_px(lon, lat) for lon, lat in ring]
                if len(pts) >= 3:
                    draw.polygon(pts, fill=255)
    return np.asarray(mask_img) > 0


def draw_boundaries(draw: ImageDraw.Draw) -> None:
    """Draw legible white country boundary outlines on the basemap."""
    data = json.loads(BOUNDARIES_GEOJSON.read_text())
    for feat in data["features"]:
        geom = feat["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            for ring in poly:
                pts = [proj.lonlat_to_px(lon, lat) for lon, lat in ring]
                if len(pts) >= 2:
                    draw.line(
                        pts,
                        fill=tuple(BOUNDARY_COLOR) + (int(255 * BOUNDARY_ALPHA),),
                        width=BOUNDARY_WIDTH,
                    )


def _lake_rings():
    """Yield projected Natural Earth lake rings in their original order."""
    if not LAKES_GEOJSON.exists():
        raise FileNotFoundError(f"{LAKES_GEOJSON} is missing; run fetch_boundaries.py")
    data = json.loads(LAKES_GEOJSON.read_text())
    for feature in data["features"]:
        geometry = feature["geometry"]
        polygons = (
            geometry["coordinates"]
            if geometry["type"] == "MultiPolygon"
            else [geometry["coordinates"]]
        )
        for polygon in polygons:
            for ring_index, ring in enumerate(polygon):
                points = [proj.lonlat_to_px(lon, lat) for lon, lat in ring]
                if len(points) >= 3:
                    yield ring_index, points


def draw_inland_lakes(draw: ImageDraw.Draw) -> None:
    """Fill and outline Natural Earth lakes, including both requested seas."""
    for ring_index, points in _lake_rings():
        if ring_index == 0:
            draw.polygon(points, fill=LAKE_FILL)
        draw.line(
            points + [points[0]],
            fill=tuple(LAKE_OUTLINE) + (255,),
            width=LAKE_OUTLINE_WIDTH,
            joint="curve",
        )


def draw_lake_outlines(draw: ImageDraw.Draw) -> None:
    """Draw a dark under-stroke and bright shoreline for overlay visibility."""
    for _, points in _lake_rings():
        closed = points + [points[0]]
        draw.line(
            closed,
            fill=(2, 12, 20, 245),
            width=LAKE_OUTLINE_SHADOW_WIDTH,
            joint="curve",
        )
        draw.line(
            closed,
            fill=tuple(LAKE_OUTLINE) + (255,),
            width=LAKE_OUTLINE_WIDTH,
            joint="curve",
        )


def build() -> None:
    elevation = load_elevation_on_grid()
    land_mask = rasterize_land_mask()
    shade = hillshade(elevation)

    rgb = np.zeros((MAP_HEIGHT, MAP_WIDTH, 3), dtype=np.float64)
    # Sea: dark navy, slightly brighter on the shallow shelf, independent of
    # sun hillshade (a flat sunlit sea reads as noise, not water).
    depth_norm = np.clip(-elevation / 400.0, 0.0, 1.0)  # 0 at coast, 1 by ~400 m deep
    sea_color = SEA_SHALLOW[None, None, :] * (1 - depth_norm[..., None]) + SEA_DEEP[None, None, :] * depth_norm[..., None]
    rgb = np.where(land_mask[..., None], rgb, sea_color)

    # Land: hillshade-modulated warm dark relief.
    land_color = LAND_LOW[None, None, :] * (1 - shade[..., None]) + LAND_HIGH[None, None, :] * shade[..., None]
    rgb = np.where(land_mask[..., None], land_color, rgb)

    # Composite translucent outlines over the opaque basemap before saving RGB.
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    img = Image.fromarray(rgb, "RGB").convert("RGBA")
    outlines = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(outlines)
    draw_boundaries(draw)
    draw_inland_lakes(draw)
    Image.alpha_composite(img, outlines).convert("RGB").save(BASEMAP_PNG)

    # Keep a separate transparent shoreline layer above daily observations.
    # Otherwise a high-opacity chlorophyll pixel can hide the basemap stroke.
    lake_outlines = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_lake_outlines(ImageDraw.Draw(lake_outlines))
    lake_outlines.save(LAKE_OUTLINES_PNG)

    # Keep the legacy mask only for the research preset; generic bundles do
    # not acquire a region-specific political mask.
    from pipeline.config import active_request
    israel_mask = rasterize_country_mask("Israel") > 0 if active_request().preset == "eastern_mediterranean" else np.zeros_like(land_mask)
    if active_request().preset == "eastern_mediterranean":
        Image.fromarray((israel_mask * 255).astype(np.uint8), "L").save(ISRAEL_MASK_PNG)
    np.savez_compressed(
        FIELDS_NPZ,
        elevation=elevation.astype(np.float32),
        land_mask=land_mask,
        israel_mask=israel_mask,
    )
    print(f"[basemap] wrote {BASEMAP_PNG}, {LAKE_OUTLINES_PNG}, {FIELDS_NPZ}")


if __name__ == "__main__":
    build()
