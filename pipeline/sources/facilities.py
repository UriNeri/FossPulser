"""HydroWASTE acquisition with preset-owned desalination metadata."""
from __future__ import annotations

import csv
import io
import json
import zipfile

from pipeline.jobs import JobContext
from pipeline.sources.common import download

# Stable file endpoint listed by the HydroWASTE Figshare record. The DOI and
# record URL remain in the source registry for citation and terms review.
HYDROWASTE_URL = "https://ndownloader.figshare.com/files/31910714"


def fetch_facilities(context: JobContext) -> None:
    archive = context.paths.raw / "HydroWASTE_v10.zip"
    try:
        download(context, HYDROWASTE_URL, archive, source="facilities")
        with zipfile.ZipFile(archive) as zipped:
            names = [name for name in zipped.namelist() if name.lower().endswith("hydrowaste_v10.csv")]
            if not names:
                raise RuntimeError("HydroWASTE archive does not contain HydroWASTE_v10.csv")
            with zipped.open(names[0]) as raw:
                rows = list(csv.DictReader(io.TextIOWrapper(raw, encoding="latin-1")))
    except Exception as error:
        # A previously supplied local archive remains useful, but a failed
        # download must not silently turn into an empty observed facility set.
        if not archive.is_file():
            context.status("facilities", "unavailable", f"HydroWASTE download failed: {type(error).__name__}")
            return
        raise
    bbox = context.request.bbox
    features = []
    if context.request.preset == "eastern_mediterranean":
        # Project-owned, approximate research annotations; these are not
        # presented as provider observations and are not used for other areas.
        from pipeline.facilities import FACILITIES as CURATED_DESALINATION
        for item in CURATED_DESALINATION:
            lon, lat = item["lon"], item["lat"]
            if bbox.west <= lon <= bbox.east and bbox.south <= lat <= bbox.north:
                features.append({"type": "Feature", "properties": {key: value for key, value in item.items() if key not in {"lon", "lat"}},
                                 "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    for row in rows:
        try:
            lon, lat = float(row["LON_WWTP"]), float(row["LAT_WWTP"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (bbox.west <= lon <= bbox.east and bbox.south <= lat <= bbox.north):
            continue
        features.append({"type": "Feature", "properties": {
            "name": row.get("WWTP_NAME", "").strip() or f"Wastewater facility {len(features) + 1}",
            "kind": "wwtp", "outfall": "river" if row.get("RIVER_DIS") else "coastal",
            "note": "HydroWASTE record; outfall details are not independently verified.",
            "country": row.get("COUNTRY", ""), "design_capacity_m3_day": row.get("DESIGN_CAP") or None,
            "discharge_m3_day": row.get("WASTE_DIS") or None, "population_served": row.get("POP_SERVED") or None,
            "treatment_level": row.get("LEVEL") or None, "source": "HydroWASTE v1.0",
        }, "geometry": {"type": "Point", "coordinates": [lon, lat]}})
    (context.paths.curated / "facilities.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    context.status("facilities", "complete", f"{len(features)} facility records", product="HydroWASTE v1.0 + preset annotations")
