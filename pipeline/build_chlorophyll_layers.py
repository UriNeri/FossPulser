"""Build daily chlorophyll overlays on the detailed map's pixel grid.

Copernicus 1 km gap-free Mediterranean observations remain authoritative where
available. NOAA S-NPP VIIRS 4 km daily observations supplement the Red Sea and
provide coarse satellite estimates over Natural Earth lakes and HydroRIVERS
channels. Each output uses only observations from that exact date: there is no
temporal averaging, carry-forward, or nearest-date substitution. Spatial gaps
use a nearby valid source cell within a limited distance. No values are
synthesized where neither source has same-day support.

Run: `pixi run python pipeline/build_chlorophyll_layers.py`
"""
from __future__ import annotations

import datetime as dt
import json

import netCDF4 as nc
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import distance_transform_edt

from pipeline.config import (
    CHLOROPHYLL_GLOBAL_NC,
    CHLOROPHYLL_NC,
    DATA_CURATED,
    DATA_FRAMES,
    LAKES_GEOJSON,
    MAP_HEIGHT,
    MAP_WIDTH,
    RIVERS_GEOJSON,
)
from pipeline.projection import DEFAULT as proj

STATS_JSON = DATA_FRAMES / "chlorophyll_stats.json"
FIELDS_NPZ = DATA_CURATED / "basemap_fields.npz"
WATER_MASK_NPZ = DATA_CURATED / "chlorophyll_water_mask.npz"
MAX_SPATIAL_FILL_DEGREES = 0.16

RAMP = [
    (-2.0, np.array([4, 14, 22])),
    (-1.3, np.array([10, 60, 70])),
    (-0.6, np.array([25, 140, 120])),
    (0.0, np.array([90, 200, 110])),
    (0.5, np.array([200, 235, 70])),
]


def colormap(log_val: np.ndarray) -> np.ndarray:
    log_val = np.clip(log_val, RAMP[0][0], RAMP[-1][0])
    out = np.zeros(log_val.shape + (3,), dtype=np.float64)
    for i in range(len(RAMP) - 1):
        t0, c0 = RAMP[i]
        t1, c1 = RAMP[i + 1]
        mask = (log_val >= t0) & (log_val <= t1)
        frac = np.zeros_like(log_val)
        frac[mask] = (log_val[mask] - t0) / (t1 - t0)
        out[mask] = c0[None, :] * (1 - frac[mask, None]) + c1[None, :] * frac[mask, None]
    return out


def read_dataset(path):
    dataset = nc.Dataset(path)
    time_var = dataset.variables["time"]
    raw_dates = nc.num2date(
        time_var[:],
        units=time_var.units,
        calendar=getattr(time_var, "calendar", "standard"),
    )
    dates = [dt.date(value.year, value.month, value.day) for value in raw_dates]
    values = np.ma.filled(dataset.variables["chlor_a"][:, 0], np.nan).astype(np.float32)
    return {
        "dates": dates,
        "date_indices": {date: index for index, date in enumerate(dates)},
        "lats": np.asarray(dataset.variables["latitude"][:], dtype=np.float64),
        "lons": np.asarray(dataset.variables["longitude"][:], dtype=np.float64),
        "values": values,
    }


def rasterize_inland_water() -> np.ndarray:
    """Rasterize Natural Earth lake polygons and HydroRIVERS channels."""
    inland_image = Image.new("L", (MAP_WIDTH, MAP_HEIGHT), 0)
    draw = ImageDraw.Draw(inland_image)

    lakes = json.loads(LAKES_GEOJSON.read_text())
    for feature in lakes["features"]:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        for polygon in polygons:
            for ring_index, ring in enumerate(polygon):
                points = [proj.lonlat_to_px(lon, lat) for lon, lat in ring]
                if len(points) >= 3:
                    draw.polygon(points, fill=255 if ring_index == 0 else 0)

    rivers = json.loads(RIVERS_GEOJSON.read_text())
    for feature in rivers["features"]:
        geometry = feature["geometry"]
        lines = geometry["coordinates"] if geometry["type"] == "MultiLineString" else [geometry["coordinates"]]
        discharge = feature["properties"].get("DIS_AV_CMS") or 0.0
        width = 3 if discharge >= 100 else 2
        for line in lines:
            points = [proj.lonlat_to_px(lon, lat) for lon, lat in line]
            if len(points) >= 2:
                draw.line(points, fill=255, width=width, joint="curve")

    return np.asarray(inland_image) > 0


def surface_water_mask() -> tuple[np.ndarray, np.ndarray]:
    """Return all surface water and the inland/river subset on the map grid.

    Marine water is everything outside the Natural Earth country land mask.
    Inland water is only the lake polygons and river channels -- below-sea-level
    land such as the Jordan Valley is correctly excluded.
    """
    fields = np.load(FIELDS_NPZ)
    ocean = ~fields["land_mask"]
    inland = rasterize_inland_water()
    return ocean | inland, inland


def sample_source(source: dict, date: dt.date, out_lat: np.ndarray, out_lon: np.ndarray):
    """Sample one source on exactly `date`, without any temporal compositing."""
    source_index = source["date_indices"].get(date)
    if source_index is None:
        empty = np.full(out_lat.shape, np.nan)
        return empty, np.zeros(out_lat.shape, dtype=bool), None
    frame = source["values"][source_index]
    valid = np.isfinite(frame)
    if not valid.any():
        empty = np.full(out_lat.shape, np.nan)
        return empty, np.zeros(out_lat.shape, dtype=bool), None

    lat_order = np.argsort(source["lats"])
    lats = source["lats"][lat_order]
    lons = source["lons"]
    frame = frame[lat_order]
    valid = valid[lat_order]
    direct = RegularGridInterpolator(
        (lats, lons), frame, bounds_error=False, fill_value=np.nan
    )((out_lat, out_lon))
    distances, indices = distance_transform_edt(~valid, return_indices=True)
    nearest = frame[tuple(indices)]
    filled = RegularGridInterpolator(
        (lats, lons), nearest, bounds_error=False, fill_value=np.nan
    )((out_lat, out_lon))
    distance_grid = RegularGridInterpolator(
        (lats, lons), distances, bounds_error=False, fill_value=np.inf
    )((out_lat, out_lon))
    lat_step = float(np.median(np.abs(np.diff(lats))))
    lon_step = float(np.median(np.abs(np.diff(lons))))
    local = distance_grid * max(lat_step, lon_step) <= MAX_SPATIAL_FILL_DEGREES
    values = np.where(np.isfinite(direct), direct, np.where(local, filled, np.nan))
    return values, np.isfinite(values), date


def main() -> None:
    primary = read_dataset(CHLOROPHYLL_NC)
    supplementary = read_dataset(CHLOROPHYLL_GLOBAL_NC) if CHLOROPHYLL_GLOBAL_NC.exists() else None
    water_mask, inland_mask = surface_water_mask()
    np.savez_compressed(WATER_MASK_NPZ, water_mask=water_mask, inland_mask=inland_mask)

    xs = np.arange(MAP_WIDTH)
    ys = np.arange(MAP_HEIGHT)
    gx, gy = np.meshgrid(xs, ys)
    out_lon, out_lat = proj.px_to_lonlat(gx, gy)

    stats = []
    (DATA_FRAMES / "chl").mkdir(parents=True, exist_ok=True)
    for index, date in enumerate(primary["dates"]):
        sampled, primary_valid, primary_date = sample_source(primary, date, out_lat, out_lon)
        source = np.where(primary_valid, "copernicus", "none")
        supplementary_date = None
        if supplementary is not None:
            extra, extra_valid, supplementary_date = sample_source(
                supplementary, date, out_lat, out_lon
            )
            use_extra = ~primary_valid & extra_valid
            sampled = np.where(use_extra, extra, sampled)
            source = np.where(use_extra, "noaa", source)

        valid = np.isfinite(sampled) & water_mask
        log_val = np.log10(np.clip(sampled, 1e-3, None))
        rgb = colormap(log_val)
        alpha = np.where(
            valid,
            np.clip(0.55 + 0.35 * (log_val + 1.3) / 1.5, 0.35, 0.95),
            0.0,
        )
        rgba = np.concatenate([rgb, (alpha * 255)[..., None]], axis=-1).astype(np.uint8)
        image = Image.fromarray(rgba, "RGBA").filter(ImageFilter.GaussianBlur(radius=1.2))
        # Reapply the exact water mask after blurring to prevent colour halos
        # from spreading onto land around narrow rivers and lake shorelines.
        pixels = np.asarray(image).copy()
        pixels[..., 3] = np.where(water_mask, pixels[..., 3], 0)
        image = Image.fromarray(pixels, "RGBA")

        filename = DATA_FRAMES / "chl" / f"chl_{index:03d}.png"
        image.save(filename)
        valid_values = sampled[valid]
        stats.append(
            {
                "index": index,
                "date": date.isoformat(),
                "mean": float(np.mean(valid_values)) if valid_values.size else None,
                "min": float(np.min(valid_values)) if valid_values.size else None,
                "max": float(np.max(valid_values)) if valid_values.size else None,
                "path": str(filename.relative_to(DATA_FRAMES)),
                "copernicus_pixels": int(np.sum(valid & (source == "copernicus"))),
                "noaa_pixels": int(np.sum(valid & (source == "noaa"))),
                "inland_pixels": int(np.sum(valid & inland_mask)),
                "red_sea_pixels": int(np.sum(valid & (out_lat < 30.05) & (out_lon > 33.0))),
                "copernicus_date": primary_date.isoformat() if primary_date else None,
                "noaa_date": supplementary_date.isoformat() if supplementary_date else None,
            }
        )
        print(
            f"[chlorophyll-layer] {date}: {valid_values.size} water pixels, "
            f"{stats[-1]['noaa_pixels']} NOAA, {stats[-1]['red_sea_pixels']} Red Sea"
        )

    STATS_JSON.write_text(json.dumps(stats, indent=2))
    print(f"[chlorophyll-layer] wrote {len(stats)} frames + {STATS_JSON}")


if __name__ == "__main__":
    main()
