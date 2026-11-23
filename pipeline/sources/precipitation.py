"""Open-Meteo ERA5-derived point precipitation samples."""
from __future__ import annotations

import datetime as dt
import json
import time

import numpy as np
import requests

from pipeline.jobs import JobContext

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def fetch_precipitation(context: JobContext) -> None:
    bbox = context.request.bbox
    # Keep request size bounded for large areas; this is intentionally a point
    # sample, not a claim of a high-resolution precipitation field.
    nx = min(8, max(2, round(bbox.width_deg)))
    ny = min(10, max(2, round(bbox.height_deg)))
    lons = np.linspace(bbox.west + bbox.width_deg / (nx * 2), bbox.west + bbox.width_deg - bbox.width_deg / (nx * 2), nx)
    lats = np.linspace(bbox.south + bbox.height_deg / (ny * 2), bbox.north - bbox.height_deg / (ny * 2), ny)
    points = [(round(float(lat), 4), round(float(((lon + 180) % 360) - 180), 4)) for lat in lats for lon in lons]
    rows = []
    for start in range(0, len(points), 12):
        context.check_cancelled()
        batch = points[start:start + 12]
        response = requests.get(ARCHIVE_URL, params={
            "latitude": ",".join(str(point[0]) for point in batch),
            "longitude": ",".join(str(point[1]) for point in batch),
            "start_date": context.request.start_date.isoformat(),
            "end_date": context.request.end_date.isoformat(),
            "daily": "precipitation_sum", "timezone": "UTC",
        }, timeout=90)
        response.raise_for_status()
        payload = response.json()
        payload = payload if isinstance(payload, list) else [payload]
        for (lat, lon), entry in zip(batch, payload):
            rows.append({"lat": lat, "lon": lon, "dates": entry["daily"]["time"],
                         "precip_mm": entry["daily"]["precipitation_sum"]})
        context.report("download", "precipitation", "downloading point batches",
                       completed=min(start + 12, len(points)), total=len(points))
        time.sleep(0.2)
    (context.paths.raw / "precipitation_raw.json").write_text(json.dumps(rows))
    context.status("precipitation", "complete", f"{len(rows)} coarse precipitation points", product="Open-Meteo ERA5")
