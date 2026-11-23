"""Project-owned research-preset facility annotations and legacy helpers.

Desalination coordinates are approximate project annotations, not engineering
or navigational records. Generic acquisition is implemented in
``pipeline.sources.facilities`` and uses HydroWASTE for wastewater records.
"""
from __future__ import annotations

import csv
import io
import json
import math
import zipfile

from pipeline.config import (
    FACILITIES_GEOJSON,
    HYDROWASTE_CSV_NAME,
    HYDROWASTE_ZIP,
    LAND_BBOX,
    PLACES_GEOJSON,
)
from pipeline.build_labels import EXTRA_PLACES

# kind: "desalination" | "wwtp"
# outfall: "sea_pipe" (underwater pipe to open sea), "river" (discharges to a
#          named stream), "coastal" (near the coast, exact outfall unreported),
#          "reuse" (inland, exact outfall unreported), or "intake" (seawater
#          intake only).
FACILITIES = [
    {
        "name": "Sorek A/B Desalination",
        "kind": "desalination",
        "lon": 34.6870, "lat": 31.9280,
        "outfall": "sea_pipe",
        "note": "Palmachim coast; among the world's largest SWRO plants; brine returned to the sea via underwater pipe.",
        "country": "Israel",
        "source": "Curated public project references; approximate location",
    },
    {
        "name": "Ashkelon Desalination",
        "kind": "desalination",
        "lon": 34.5460, "lat": 31.6690,
        "outfall": "sea_pipe",
        "note": "South of Ashkelon port; seawater intake and brine outfall both via underwater pipes.",
        "country": "Israel",
        "source": "Curated public project references; approximate location",
    },
    {
        "name": "Ashdod Desalination",
        "kind": "desalination",
        "lon": 34.6210, "lat": 31.7830,
        "outfall": "sea_pipe",
        "note": "Adjacent to Ashdod port.",
        "country": "Israel",
        "source": "Curated public project references; approximate location",
    },
    {
        "name": "Hadera Desalination (Olga/Orot Rabin)",
        "kind": "desalination",
        "lon": 34.8710, "lat": 32.4820,
        "outfall": "sea_pipe",
        "note": "Co-located with the Orot Rabin power station north of Hadera.",
        "country": "Israel",
        "source": "Curated public project references; approximate location",
    },
    {
        "name": "East Port Said Desalination",
        "kind": "desalination",
        "lon": 32.3100, "lat": 31.2600,
        "outfall": "sea_pipe",
        "note": "East Port Said coastal seawater reverse-osmosis plant; approximate map location.",
        "country": "Egypt",
        "source": "Curated public project references; approximate location",
    },
    {
        "name": "El Arish Desalination",
        "kind": "desalination",
        "lon": 33.8000, "lat": 31.1300,
        "outfall": "sea_pipe",
        "note": "El Arish coastal desalination complex; approximate map location.",
        "country": "Egypt",
        "source": "Curated public project references; approximate location",
    },
    {
        "name": "Ain Sokhna Desalination",
        "kind": "desalination",
        "lon": 32.3167, "lat": 29.6000,
        "outfall": "sea_pipe",
        "note": "Ain Sokhna industrial-coast desalination plant; approximate map location.",
        "country": "Egypt",
        "source": "Curated public project references; approximate location",
    },
]


def _load_hydrowaste_rows() -> list[dict]:
    """Read HydroWASTE_v10.csv straight out of the downloaded zip (no need to
    unpack it to disk) and return the rows that fall inside LAND_BBOX."""
    west, south, east, north = LAND_BBOX
    rows = []
    with zipfile.ZipFile(HYDROWASTE_ZIP) as zf:
        with zf.open(HYDROWASTE_CSV_NAME) as raw:
            text = io.TextIOWrapper(raw, encoding="latin-1")
            for row in csv.DictReader(text):
                try:
                    lon, lat = float(row["LON_WWTP"]), float(row["LAT_WWTP"])
                except ValueError:
                    continue
                if not (west <= lon <= east and south <= lat <= north):
                    continue
                rows.append(row)
    return rows


def _optional_float(row: dict, field: str) -> float | None:
    """Return a finite numeric HydroWASTE attribute, or None when unreported."""
    value = row.get(field, "").strip()
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _wwtp_note(row: dict) -> tuple[str, str]:
    """Infer an outfall category + a short human-readable note from
    HydroWASTE's coastal-distance and river-discharge-ratio columns."""
    coast_10km = row.get("COAST_10KM") in ("1", 1)
    river_dis = row.get("RIVER_DIS", "").strip()

    if river_dis:
        outfall = "river"
        note = f"Discharges to a river; effluent is approximately {river_dis} times natural flow."
    elif coast_10km:
        outfall = "coastal"
        note = "Within 10 km of the coast; outfall details are not reported."
    else:
        outfall = "reuse"
        note = "Inland facility; outfall details are not reported."
    return outfall, note


def _load_place_names() -> list[tuple[str, float, float]]:
    """All known place labels (Natural Earth + EXTRA_PLACES), for
    nearest-neighbour naming of unnamed HydroWASTE entries."""
    places = []
    if PLACES_GEOJSON.exists():
        for feat in json.loads(PLACES_GEOJSON.read_text())["features"]:
            name = feat["properties"].get("name")
            if not name:
                continue
            lon, lat = feat["geometry"]["coordinates"]
            places.append((name, lon, lat))
    for extra in EXTRA_PLACES:
        places.append((extra["name"], extra["lon"], extra["lat"]))
    return places


def _nearest_place_name(lon: float, lat: float, places: list[tuple[str, float, float]]) -> str | None:
    if not places:
        return None
    name, _, _ = min(places, key=lambda p: math.hypot(p[1] - lon, p[2] - lat))
    return name


def _wwtp_facilities() -> list[dict]:
    if not HYDROWASTE_ZIP.exists():
        print(f"[facilities] WARNING: {HYDROWASTE_ZIP} not found; skipping WWTPs")
        return []

    rows = _load_hydrowaste_rows()
    rows.sort(key=lambda r: -float(r.get("WASTE_DIS") or 0.0))
    places = _load_place_names()

    facilities = []
    name_counts: dict[str, int] = {}
    for i, row in enumerate(rows, start=1):
        lon, lat = float(row["LON_WWTP"]), float(row["LAT_WWTP"])
        country = row.get("COUNTRY", "").strip() or "?"
        explicit_name = row.get("WWTP_NAME", "").strip()
        if explicit_name:
            name = explicit_name
        else:
            nearest = _nearest_place_name(lon, lat, places)
            name = f"{nearest} area WWTP" if nearest else f"WWTP #{i} ({country})"
        name_counts[name] = name_counts.get(name, 0) + 1
        if name_counts[name] > 1:
            name = f"{name} #{name_counts[name]}"
        outfall, note = _wwtp_note(row)
        facilities.append(
            {
                "name": name,
                "kind": "wwtp",
                "lon": lon,
                "lat": lat,
                "outfall": outfall,
                "note": note,
                "country": country,
                "design_capacity_m3_day": _optional_float(row, "DESIGN_CAP"),
                "discharge_m3_day": _optional_float(row, "WASTE_DIS"),
                "population_served": _optional_float(row, "POP_SERVED"),
                "treatment_level": row.get("LEVEL", "").strip() or "Not reported",
                "source": "HydroWASTE v1.0",
            }
        )
    return facilities


def main() -> None:
    all_facilities = FACILITIES + _wwtp_facilities()
    features = [
        {
            "type": "Feature",
            "properties": {k: v for k, v in f.items() if k not in ("lon", "lat")},
            "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]},
        }
        for f in all_facilities
    ]
    fc = {"type": "FeatureCollection", "features": features}
    FACILITIES_GEOJSON.write_text(json.dumps(fc, indent=2))
    print(f"[facilities] wrote {FACILITIES_GEOJSON} with {len(features)} facilities "
          f"({sum(1 for f in all_facilities if f['kind'] == 'desalination')} desalination, "
          f"{sum(1 for f in all_facilities if f['kind'] == 'wwtp')} WWTP)")


if __name__ == "__main__":
    main()
