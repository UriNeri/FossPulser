"""Combine everything the app needs into data/frames/manifest.json: the
static layer image paths, per-day chlorophyll frame paths + stats, and the
projected label/pin positions.

Run: `pixi run python pipeline/build_manifest.py`
"""
from __future__ import annotations

import json

from PIL import Image

from pipeline.config import (
    DATA_CURATED,
    DATA_FRAMES,
    GLOBAL_BBOX,
    GLOBAL_CONTEXT_PNG,
    GLOBAL_HEIGHT,
    GLOBAL_WIDTH,
    LAND_BBOX,
    MANIFEST_JSON,
    MAP_HEIGHT,
    MAP_WIDTH,
    PRECIP_JSON,
)

STATS_JSON = DATA_FRAMES / "chlorophyll_stats.json"
SST_STATS_JSON = DATA_FRAMES / "sst_stats.json"
LABELS_JSON = DATA_CURATED / "labels.json"
OBLIQUE_MANIFEST = DATA_FRAMES / "oblique_manifest.json"


def rel_to_frames(p) -> str:
    try:
        return str(p.relative_to(DATA_FRAMES))
    except ValueError:
        return str(p)


def validate_geometry(stats: list[dict], sst_stats: list[dict], labels: dict) -> None:
    """Reject stale rasters or labels before publishing a mixed-extent map."""
    expected = (MAP_WIDTH, MAP_HEIGHT)
    paths = [path for path in (DATA_CURATED / "basemap.png", DATA_CURATED / "lake_outlines.png", DATA_CURATED / "rivers.png") if path.exists()]
    paths += [DATA_FRAMES / stats[0]["path"]] if stats else []
    paths += [DATA_FRAMES / sst_stats[0]["path"]] if sst_stats else []
    for path in paths:
        with Image.open(path) as image:
            if image.size != expected:
                raise RuntimeError(
                    f"{path} has size {image.size}, expected {expected}; "
                    "rebuild all raster layers after changing the bounding box"
                )
    if (labels["map_width"], labels["map_height"]) != expected:
        raise RuntimeError("labels.json does not match the configured map dimensions")


def main() -> None:
    stats = json.loads(STATS_JSON.read_text()) if STATS_JSON.exists() else []
    labels = json.loads(LABELS_JSON.read_text()) if LABELS_JSON.exists() else {"map_width": MAP_WIDTH, "map_height": MAP_HEIGHT, "places": [], "facilities": []}

    oblique = []
    if OBLIQUE_MANIFEST.exists():
        oblique = json.loads(OBLIQUE_MANIFEST.read_text())

    sst_stats = []
    if SST_STATS_JSON.exists():
        sst_stats = json.loads(SST_STATS_JSON.read_text())

    precipitation = None
    if PRECIP_JSON.exists():
        precipitation = json.loads(PRECIP_JSON.read_text())

    validate_geometry(stats, sst_stats, labels)
    if not GLOBAL_CONTEXT_PNG.exists():
        raise RuntimeError("global context is required for a publishable bundle")
    with Image.open(GLOBAL_CONTEXT_PNG) as image:
        if image.size != (GLOBAL_WIDTH, GLOBAL_HEIGHT):
            raise RuntimeError("global context raster does not match its configured dimensions")
    manifest = {
        "schema_version": 2,
        "map_width": MAP_WIDTH,
        "map_height": MAP_HEIGHT,
        "map_bbox": LAND_BBOX,
        "global_context": "../curated/global_context.png",
        "global_bbox": GLOBAL_BBOX,
        "global_width": GLOBAL_WIDTH,
        "global_height": GLOBAL_HEIGHT,
        "bathymetry_layer": "../curated/bathymetry_contours.png" if (DATA_CURATED / "bathymetry_contours.png").exists() else None,
        "lake_outlines": "../curated/lake_outlines.png" if (DATA_CURATED / "lake_outlines.png").exists() else None,
        "basemap": "../curated/basemap.png" if (DATA_CURATED / "basemap.png").exists() else "",
        "rivers_layer": "../curated/rivers.png" if (DATA_CURATED / "rivers.png").exists() else None,
        "chlorophyll_frames": stats,
        "sst_frames": sst_stats,
        "oblique_frames": oblique,
        "precipitation": precipitation,
        "places": labels["places"],
        "facilities": labels["facilities"],
    }
    MANIFEST_JSON.write_text(json.dumps(manifest))
    print(
        f"[manifest] wrote {MANIFEST_JSON} ({len(stats)} chlorophyll frames, "
        f"{len(sst_stats)} SST frames, {len(oblique)} oblique frames, "
        f"{'with' if precipitation else 'no'} precipitation overlay)"
    )


if __name__ == "__main__":
    main()
