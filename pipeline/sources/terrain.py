"""GMRT terrain and Natural Earth geographic context."""
from __future__ import annotations

import json
from pathlib import Path

import shapefile

from pipeline.config import Bbox
from pipeline.jobs import JobContext
from pipeline.sources.common import download, safe_extract

GMRT_URL = "https://www.gmrt.org/services/GridServer"
NATURAL_EARTH = {
    "countries": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip",
    "places": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_populated_places.zip",
    "lakes": "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_lakes.zip",
}


def fetch_terrain(context: JobContext) -> None:
    bbox = context.request.bbox
    destination = context.paths.raw / "bathymetry.tif"
    download(context, GMRT_URL, destination, source="bathymetry", params={
        "minlongitude": bbox.west, "maxlongitude": bbox.east,
        "minlatitude": bbox.south, "maxlatitude": bbox.north,
        "format": "geotiff", "mresolution": 200, "layer": "topo",
    })
    context.status("bathymetry", "complete", "GMRT terrain downloaded", product="GMRT GridServer")


def _find_shapefile(directory: Path) -> Path:
    matches = list(directory.rglob("*.shp"))
    if not matches:
        raise FileNotFoundError(f"no shapefile under {directory}")
    return matches[0]


def _shape_bbox(shape) -> tuple[float, float, float, float]:
    value = getattr(shape, "bbox", None)
    if value is not None:
        return tuple(value() if callable(value) else value)
    points = shape.points
    if not points:
        raise ValueError("cannot compute bounds of an empty shape")
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def _overlaps(shape_bbox, bbox: Bbox) -> bool:
    west, south, east, north = shape_bbox
    return west <= bbox.east and east >= bbox.west and south <= bbox.north and north >= bbox.south


def _geometry(shape) -> dict:
    # pyshp's geo interface preserves polygon parts and ring orientation for
    # current releases. Keep a fallback for older versions with multipart
    # polygons rather than flattening every ring into one polygon.
    geometry = shape.__geo_interface__
    if geometry.get("type") == "Polygon" and len(shape.parts) > 1:
        parts = list(shape.parts) + [len(shape.points)]
        rings = [[list(point) for point in shape.points[start:end]] for start, end in zip(parts, parts[1:])]
        return {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]}
    return geometry


def fetch_boundaries(context: JobContext) -> None:
    bbox = context.request.bbox.padded(1.0)
    extracted = {}
    for name, url in NATURAL_EARTH.items():
        archive = context.paths.root / "cache" / "natural-earth" / f"{name}.zip"
        directory = context.paths.raw / f"ne_{name}"
        download(context, url, archive, source="boundaries")
        if not directory.exists():
            safe_extract(archive, directory)
        extracted[name] = _find_shapefile(directory)

    for name, output_name in (("countries", "boundaries.geojson"), ("places", "places.geojson"), ("lakes", "lakes.geojson")):
        reader = shapefile.Reader(str(extracted[name]))
        fields = [field[0] for field in reader.fields[1:]]
        features = []
        for index, record in enumerate(reader.iterShapeRecords()):
            context.check_cancelled()
            shape = record.shape
            if not shape.points or not _overlaps(_shape_bbox(shape), bbox):
                continue
            properties = dict(zip(fields, record.record))
            if name == "places":
                x, y = shape.points[0]
                if not (bbox.west <= x <= bbox.east and bbox.south <= y <= bbox.north):
                    continue
                properties = {"name": properties.get("NAME"), "pop_max": properties.get("POP_MAX") or 0,
                              "scalerank": properties.get("SCALERANK", 10)}
            elif name == "countries":
                properties = {"name": properties.get("NAME") or properties.get("ADMIN") or properties.get("SOVEREIGNT"),
                              "iso_a3": properties.get("ISO_A3")}
            else:
                properties = {"name": properties.get("name") or properties.get("NAME") or "Lake"}
            features.append({"type": "Feature", "properties": properties, "geometry": _geometry(shape)})
        (context.paths.curated / output_name).write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        context.report("process", "boundaries", f"clipped {len(features)} {name}", completed=index + 1, total=len(reader))
    context.status("boundaries", "complete", "Natural Earth context clipped", product="Natural Earth 10m")
