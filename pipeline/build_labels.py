"""Project city labels and facility pins into the same pixel space as the
raster layers, for the Rune app to draw as native (crisp, zoomable) text/dot
elements rather than baked-in raster labels.

Run: `pixi run python pipeline/build_labels.py`
"""
from __future__ import annotations

import json

from pipeline.config import DATA_CURATED, FACILITIES_GEOJSON, PLACES_GEOJSON, active_request
from pipeline.projection import DEFAULT as proj

LABELS_JSON = DATA_CURATED / "labels.json"

# Natural Earth's populated-places layer (10 m) is missing a few smaller
# Israeli cities that matter for this map; add them by hand.
EXTRA_PLACES = [
    {"name": "Eilat", "lon": 34.9482, "lat": 29.5577, "pop_max": 50000, "scalerank": 8},
    {"name": "Tiberias", "lon": 35.5312, "lat": 32.7922, "pop_max": 45000, "scalerank": 9},
    {"name": "Netanya", "lon": 34.8516, "lat": 32.3215, "pop_max": 230000, "scalerank": 7},
    {"name": "Ashdod", "lon": 34.6446, "lat": 31.8014, "pop_max": 230000, "scalerank": 7},
]

MIN_SCALERANK = 9  # keep modest-and-larger places; smaller villages dropped


def main() -> None:
    places = json.loads(PLACES_GEOJSON.read_text())["features"] if PLACES_GEOJSON.exists() else []
    facilities = json.loads(FACILITIES_GEOJSON.read_text())["features"] if FACILITIES_GEOJSON.exists() else []

    place_labels = []
    seen = set()
    for feat in places:
        props = feat["properties"]
        if props.get("scalerank", 99) is None or props.get("scalerank", 99) > MIN_SCALERANK:
            continue
        name = props.get("name")
        lon, lat = feat["geometry"]["coordinates"]
        x, y = proj.lonlat_to_px(lon, lat)
        place_labels.append({"name": name, "x": x, "y": y, "pop_max": props.get("pop_max", 0)})
        seen.add(name)
    for extra in EXTRA_PLACES if active_request().preset == "eastern_mediterranean" else ():
        if extra["name"] in seen:
            continue
        x, y = proj.lonlat_to_px(extra["lon"], extra["lat"])
        place_labels.append({"name": extra["name"], "x": x, "y": y, "pop_max": extra["pop_max"]})

    facility_pins = []
    for feat in facilities:
        props = feat["properties"]
        lon, lat = feat["geometry"]["coordinates"]
        x, y = proj.lonlat_to_px(lon, lat)
        facility_pins.append(
            {
                "name": props["name"],
                "kind": props["kind"],
                "outfall": props["outfall"],
                "note": props["note"],
                "country": props.get("country"),
                "design_capacity_m3_day": props.get("design_capacity_m3_day"),
                "discharge_m3_day": props.get("discharge_m3_day"),
                "population_served": props.get("population_served"),
                "treatment_level": props.get("treatment_level"),
                "source": props.get("source"),
                "x": x,
                "y": y,
            }
        )

    out = {
        "map_width": proj.width,
        "map_height": proj.height,
        "places": place_labels,
        "facilities": facility_pins,
    }
    LABELS_JSON.write_text(json.dumps(out, indent=2))
    print(f"[labels] wrote {LABELS_JSON}: {len(place_labels)} places, {len(facility_pins)} facilities")


if __name__ == "__main__":
    main()
