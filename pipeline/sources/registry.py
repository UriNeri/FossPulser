"""Source registry and capability metadata used by CLI and UI."""
from __future__ import annotations

from pipeline.config import Bbox, Request
from pipeline.sources.satellite import MEDITERRANEAN

SOURCES = {
    "bathymetry": {"label": "Terrain / bathymetry", "auth": "none", "coverage": "global"},
    "boundaries": {"label": "Countries, places, and lakes", "auth": "none", "coverage": "global"},
    "rivers": {"label": "River network", "auth": "none", "coverage": "continental archives"},
    "facilities": {"label": "Wastewater facilities", "auth": "none", "coverage": "global"},
    "chlorophyll": {"label": "Chlorophyll-a", "auth": "optional Copernicus", "coverage": "NOAA global + Copernicus Mediterranean"},
    "sst": {"label": "Sea-surface temperature", "auth": "optional Copernicus", "coverage": "NOAA global + Copernicus Mediterranean"},
    "precipitation": {"label": "Precipitation point samples", "auth": "none", "coverage": "global"},
}


def capabilities(request: Request) -> dict:
    result = {}
    for name, metadata in SOURCES.items():
        item = dict(metadata)
        item["selected"] = name in request.sources
        item["mediterranean_intersection"] = request.bbox.intersects(MEDITERRANEAN) if name in {"chlorophyll", "sst"} else None
        result[name] = item
    return result
