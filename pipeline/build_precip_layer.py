"""Turn fetch_precipitation.py's raw per-point series into a per-day list of
projected (pixel-space) rain markers for the app: data/curated/precipitation.json
is `{"dates": [...], "points": [[x, y], ...], "by_day": [[intensity, ...], ...]}`
where `by_day[i][j]` is grid point `j`'s precipitation (mm) on `dates[i]`
(0.0 where it didn't rain -- the app only draws a marker above a threshold).

Run: `pixi run python pipeline/build_precip_layer.py`
"""
from __future__ import annotations

import json

from pipeline.config import DATA_RAW, PRECIP_JSON
from pipeline.projection import DEFAULT as proj

RAW_JSON = DATA_RAW / "precipitation_raw.json"


def main() -> None:
    rows = json.loads(RAW_JSON.read_text())
    if not rows:
        raise RuntimeError(f"{RAW_JSON} is empty; run fetch_precipitation.py first")

    dates = rows[0]["dates"]
    points = []
    by_day = [[] for _ in dates]
    for row in rows:
        if row["dates"] != dates:
            # Shouldn't happen (every point was queried for the same range),
            # but don't silently misalign data if a batch ever drifts.
            raise RuntimeError("precipitation rows have mismatched date ranges")
        x, y = proj.lonlat_to_px(row["lon"], row["lat"])
        points.append([round(x, 1), round(y, 1)])
        for day_idx, mm in enumerate(row["precip_mm"]):
            by_day[day_idx].append(round(mm or 0.0, 2))

    out = {"dates": dates, "points": points, "by_day": by_day}
    PRECIP_JSON.write_text(json.dumps(out))
    rainy_days = sum(1 for day in by_day if any(mm > 0.2 for mm in day))
    print(f"[precip-layer] wrote {PRECIP_JSON}: {len(points)} points x {len(dates)} days "
          f"({rainy_days} days with rain somewhere on the grid)")


if __name__ == "__main__":
    main()
