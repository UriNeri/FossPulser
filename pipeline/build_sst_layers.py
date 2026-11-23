"""Build the per-day sea-surface-temperature "glow" layer: a transparent PNG
per day, on the same pixel grid as the chlorophyll layer and basemap,
masked to open water, colour-mapped on a thermal (blue -> red) scale typical
of SST maps (~14-30 degC spans the Eastern Mediterranean's winter-to-summer
range).

Also writes data/frames/sst_stats.json with per-day summary stats, in the
same shape as chlorophyll_stats.json. The app looks SST frames up **by
date**, not by sharing an index with the chlorophyll frames: fetch_sst.py
and fetch_chlorophyll.py each anchor their DAYS_OF_HISTORY window to
"today" independently, so if they're run on different days their date
ranges can be off by a day or two.

Run: `pixi run python pipeline/build_sst_layers.py`
"""
from __future__ import annotations

import json

import netCDF4 as nc
import numpy as np
from PIL import Image, ImageFilter
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import distance_transform_edt

from pipeline.config import DATA_CURATED, DATA_FRAMES, MAP_HEIGHT, MAP_WIDTH, SST_NC
from pipeline.projection import DEFAULT as proj

STATS_JSON = DATA_FRAMES / "sst_stats.json"
FIELDS_NPZ = DATA_CURATED / "basemap_fields.npz"

# Colour ramp: cool blue (cold) -> teal -> amber -> red (warm), degrees C.
RAMP = [
    (14.0, np.array([20, 40, 110])),
    (18.0, np.array([20, 110, 150])),
    (22.0, np.array([60, 170, 130])),
    (26.0, np.array([230, 170, 60])),
    (30.0, np.array([220, 60, 40])),
]


def colormap(temp_c: np.ndarray) -> np.ndarray:
    temp_c = np.clip(temp_c, RAMP[0][0], RAMP[-1][0])
    out = np.zeros(temp_c.shape + (3,), dtype=np.float64)
    for i in range(len(RAMP) - 1):
        t0, c0 = RAMP[i]
        t1, c1 = RAMP[i + 1]
        mask = (temp_c >= t0) & (temp_c <= t1)
        frac = np.zeros_like(temp_c)
        span = (t1 - t0) or 1.0
        frac[mask] = (temp_c[mask] - t0) / span
        out[mask] = c0[None, :] * (1 - frac[mask, None]) + c1[None, :] * frac[mask, None]
    return out


def fill_nearest(arr: np.ndarray) -> np.ndarray:
    mask = ~np.isfinite(arr)
    if not mask.any():
        return arr
    _, indices = distance_transform_edt(mask, return_distances=True, return_indices=True)
    return arr[tuple(indices)]


def mediterranean_only_mask(water_mask: np.ndarray) -> np.ndarray:
    from scipy.ndimage import label

    labeled, _ = label(water_mask)
    edge_labels = set(np.unique(labeled[:, 0])) - {0}
    if not edge_labels:
        return water_mask
    return np.isin(labeled, list(edge_labels))


def main() -> None:
    ds = nc.Dataset(SST_NC)
    time_var = ds.variables["time"]
    times = time_var[:]
    time_units = getattr(time_var, "units", "seconds since 1970-01-01")
    dates = nc.num2date(times, units=time_units, calendar=getattr(time_var, "calendar", "standard"))
    lats = np.asarray(ds.variables["latitude"][:])
    lons = np.asarray(ds.variables["longitude"][:])
    sst_raw = ds.variables["sst_kelvin"][:, :, :]  # (time, lat, lon), masked array
    sst_k = np.ma.filled(sst_raw, np.nan).astype(np.float64)
    sst_c = sst_k - 273.15

    fields = np.load(FIELDS_NPZ)
    land_mask = fields["land_mask"]
    water_mask = mediterranean_only_mask(~land_mask)

    xs = np.arange(MAP_WIDTH)
    ys = np.arange(MAP_HEIGHT)
    gx, gy = np.meshgrid(xs, ys)
    out_lon, out_lat = proj.px_to_lonlat(gx, gy)

    lat_order = np.argsort(lats)
    lats_sorted = lats[lat_order]

    stats = []
    (DATA_FRAMES / "sst").mkdir(parents=True, exist_ok=True)
    for ti in range(sst_c.shape[0]):
        frame = fill_nearest(sst_c[ti][lat_order, :])
        interp = RegularGridInterpolator(
            (lats_sorted, lons), frame, bounds_error=False, fill_value=np.nan
        )
        sampled = interp((out_lat, out_lon))
        sampled_valid = np.isfinite(sampled) & water_mask
        rgb = colormap(sampled)
        alpha = np.where(sampled_valid, 0.75, 0.0)
        rgba = np.concatenate([rgb, (alpha * 255)[..., None]], axis=-1).astype(np.uint8)
        img = Image.fromarray(rgba, "RGBA")
        img = img.filter(ImageFilter.GaussianBlur(radius=2.0))

        date = dates[ti].strftime("%Y-%m-%d")
        fname = DATA_FRAMES / "sst" / f"sst_{ti:03d}.png"
        img.save(fname)

        valid_src = sst_c[ti][np.isfinite(sst_c[ti])]
        stats.append(
            {
                "index": ti,
                "date": date,
                "mean": float(np.mean(valid_src)) if valid_src.size else None,
                "min": float(np.min(valid_src)) if valid_src.size else None,
                "max": float(np.max(valid_src)) if valid_src.size else None,
                "path": str(fname.relative_to(DATA_FRAMES)),
            }
        )
        print(f"[sst-layer] {date}: mean={stats[-1]['mean']:.2f} degC" if valid_src.size else f"[sst-layer] {date}: no data")

    STATS_JSON.write_text(json.dumps(stats, indent=2))
    print(f"[sst-layer] wrote {len(stats)} frames + {STATS_JSON}")


if __name__ == "__main__":
    main()
