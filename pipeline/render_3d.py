"""Render the oblique 3D "hero" view of the Tel Aviv coast with forge3d, using
the real bathymetry/topography mesh. Each month gets its own water tint driven
by that month's mean coastal chlorophyll concentration (forge3d's live
per-image raster draping (`load_overlay`) was found to be non-functional in
this forge3d build when probed directly against its IPC/shader path, so the
reliable, documented `set_terrain` water_color control is used instead --
real data still drives the scene, just through a single tinted colour rather
than a per-pixel texture).

A few height-exaggeration ("z_scale") presets are pre-rendered so the app's
slider can snap between them without needing a live GPU process.

Run: `pixi run python pipeline/render_3d.py`
"""
from __future__ import annotations

import json
import math
import time

import forge3d as f3d

from pipeline.config import (
    BATHYMETRY_TIF,
    CAMERA_FOV_DEG,
    CAMERA_PHI_DEG,
    CAMERA_RADIUS_M,
    CAMERA_TARGET_LAT,
    CAMERA_TARGET_LON,
    CAMERA_THETA_DEG,
    DATA_FRAMES,
    LAND_BBOX,
    Z_SCALE_PRESETS,
)
from pipeline.build_chlorophyll_layers import RAMP, colormap as chl_colormap

OUT_DIR = DATA_FRAMES / "oblique"
STATS_JSON = DATA_FRAMES / "chlorophyll_stats.json"
MANIFEST_3D = DATA_FRAMES / "oblique_manifest.json"
RENDER_SIZE = (900, 560)
FRAME_STRIDE = 5  # every 5th day keeps optional rendering bounded


def world_target() -> tuple[float, float, float]:
    west, south, east, north = LAND_BBOX
    mean_lat = math.radians((south + north) / 2.0)
    m_per_deg_lon = 111_320.0 * math.cos(mean_lat)
    m_per_deg_lat = 111_320.0
    x = (CAMERA_TARGET_LON - west) * m_per_deg_lon
    z = (north - CAMERA_TARGET_LAT) * m_per_deg_lat
    return (x, 0.0, z)


def water_color_for_chl(mean_mgm3: float | None) -> list[float]:
    """Map a monthly mean chlorophyll concentration (mg/m^3) to an RGB water
    tint using the same log colour ramp as the 2D glow layer, so the 3D and
    2D views agree on what "high chlorophyll" looks like."""
    import numpy as np

    value = mean_mgm3 if mean_mgm3 else 0.1
    log_val = np.log10(max(value, 1e-3))
    rgb = chl_colormap(np.array([[log_val]]))[0, 0]
    # Blend toward a plausible open-water blue so it still reads as "sea"
    # rather than literally the flat glow colour.
    base = np.array([10.0, 45.0, 70.0])
    mixed = 0.55 * base + 0.45 * rgb
    return [float(c / 255.0) for c in mixed]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats = json.loads(STATS_JSON.read_text())
    # 364 days x 3 z_scale presets would be 1092 snapshots; stride down to
    # every FRAME_STRIDE-th day to keep this to a manageable few minutes.
    # The app's ensure_oblique_image() picks the
    # closest available day to whatever day is scrubbed to, so a sparser set
    # still "just works".
    frames = stats[::FRAME_STRIDE]
    target = world_target()
    print(f"[render3d] world target = {target}")
    print(f"[render3d] rendering {len(frames)}/{len(stats)} days x {len(Z_SCALE_PRESETS)} z_scales "
          f"(stride={FRAME_STRIDE})")

    manifest = []
    with f3d.open_viewer_async(terrain_path=str(BATHYMETRY_TIF), width=RENDER_SIZE[0], height=RENDER_SIZE[1]) as viewer:
        time.sleep(2.0)
        viewer.set_orbit_camera(
            phi_deg=CAMERA_PHI_DEG, theta_deg=CAMERA_THETA_DEG, radius=CAMERA_RADIUS_M,
            fov_deg=CAMERA_FOV_DEG, target=target,
        )
        viewer.set_sun(azimuth_deg=255.0, elevation_deg=38.0)
        for z_scale in Z_SCALE_PRESETS:
            viewer.set_z_scale(z_scale)
            for frame in frames:
                color = water_color_for_chl(frame["mean"])
                viewer.send_ipc({"cmd": "set_terrain", "water_color": color, "water_level": 0.0})
                time.sleep(0.05)
                fname = OUT_DIR / f"oblique_z{z_scale:g}_{frame['index']:03d}.png"
                viewer.snapshot(str(fname), width=RENDER_SIZE[0], height=RENDER_SIZE[1])
                manifest.append(
                    {
                        "z_scale": z_scale,
                        "index": frame["index"],
                        "date": frame["date"],
                        "path": str(fname.relative_to(DATA_FRAMES)),
                    }
                )
            print(f"[render3d] finished z_scale={z_scale} ({len(frames)} frames)")

    MANIFEST_3D.write_text(json.dumps(manifest, indent=2))
    print(f"[render3d] wrote {len(manifest)} renders + {MANIFEST_3D}")


if __name__ == "__main__":
    main()
