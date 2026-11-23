"""HydroRIVERS continental archive selection and clipping."""
from __future__ import annotations

import json

import shapefile

from pipeline.config import Bbox
from pipeline.jobs import JobContext
from pipeline.sources.common import download, safe_extract
from pipeline.sources.terrain import _shape_bbox

# HydroSHEDS archive boundaries are drainage-region groupings, not political
# boundaries. The broad envelopes are used only to avoid downloading unrelated
# archives; actual features are clipped by their geometries below.
REGIONS = {
    "af": ((-20, -35, 55, 38), "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_af_shp.zip"),
    "as": ((55, -10, 180, 60), "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_as_shp.zip"),
    "au": ((110, -50, 180, 0), "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_au_shp.zip"),
    "eu": ((-30, 25, 70, 75), "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_eu_shp.zip"),
    "na": ((-180, 5, -50, 85), "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_na_shp.zip"),
    "sa": ((-90, -60, -30, 15), "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_sa_shp.zip"),
}


def selected_regions(bbox: Bbox) -> list[str]:
    return [code for code, (envelope, _) in REGIONS.items()
            if bbox.intersects(Bbox.from_tuple(envelope, enforce_area_limit=False))]


def fetch_rivers(context: JobContext) -> None:
    target = context.request.bbox.padded(0.2)
    features = []
    seen = set()
    regions = selected_regions(target)
    if not regions:
        context.status("rivers", "unavailable", "no HydroRIVERS archive covers this area")
        return
    for code in regions:
        _, url = REGIONS[code]
        archive = context.paths.root / "cache" / "hydrorivers" / f"{code}.zip"
        directory = context.paths.raw / f"hydrorivers_{code}"
        download(context, url, archive, source="rivers")
        shape_path = directory / f"HydroRIVERS_v10_{code}_shp" / f"HydroRIVERS_v10_{code}.shp"
        if not shape_path.exists():
            safe_extract(archive, directory)
        reader = shapefile.Reader(str(shape_path))
        fields = [field[0] for field in reader.fields[1:]]
        kept = 0
        for index, record in enumerate(reader.iterShapeRecords()):
            context.check_cancelled()
            shape = record.shape
            if not shape.points:
                continue
            west, south, east, north = _shape_bbox(shape)
            if south == north:
                north = south + 1e-9
            if west == east:
                east = west + 1e-9
            if not target.intersects(Bbox(west, south, east, north, enforce_area_limit=False)):
                continue
            properties = dict(zip(fields, record.record))
            river_id = properties.get("HYRIV_ID")
            if river_id in seen:
                continue
            seen.add(river_id)
            features.append({
                "type": "Feature",
                "properties": {"hyriv_id": river_id, "dis_av_cms": properties.get("DIS_AV_CMS"),
                               "ord_flow": properties.get("ORD_FLOW"), "ord_stra": properties.get("ORD_STRA"),
                               "length_km": properties.get("LENGTH_KM"), "source_region": code},
                "geometry": shape.__geo_interface__,
            })
            kept += 1
            if index % 100000 == 0:
                context.report("process", "rivers", f"scanning {code}", completed=index, total=len(reader))
        context.report("process", "rivers", f"kept {kept} segments from {code}", completed=len(reader), total=len(reader))
    (context.paths.curated / "rivers.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    context.status("rivers", "complete" if features else "unavailable", f"{len(features)} river segments", regions=regions)
