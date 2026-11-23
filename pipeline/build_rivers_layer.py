"""Render the static river-network layer as a transparent, glowing PNG on the
same pixel grid as the basemap (see projection.py). Styled by HydroRIVERS'
``ORD_FLOW`` (flow order, 1 = largest) and ``DIS_AV_CMS`` (long-term average
discharge): bigger/higher-discharge rivers get a brighter, wider line,
colour-mapped red (barren/low discharge) -> intense blue (high discharge).
The app additionally pulses this layer's opacity gently over time; see
`main.rs`'s `rivers_pulse_opacity`. The two specifically-requested streams
(Yarkon, Sorek) are called out with a small accent marker near their river
mouths since HydroRIVERS carries no names.

Run: `pixi run python pipeline/build_rivers_layer.py`
"""
from __future__ import annotations

import json

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from pipeline.config import DATA_CURATED, FEATURED_RIVERS, MAP_HEIGHT, MAP_WIDTH, RIVERS_GEOJSON
from pipeline.projection import DEFAULT as proj

RIVERS_PNG = DATA_CURATED / "rivers.png"

# Known lower-course waypoints for the two featured streams, used only to
# place a callout marker (approximate, from public knowledge -- see
# pipeline/facilities.py for the same caveat).
FEATURED_RIVER_MOUTHS = {
    "Yarkon": (34.7685, 32.1015),
    "Sorek": (34.6985, 31.9290),
}

# Discharge colour ramp: red (barren, log10(m^3/s) <= -1) -> amber -> cyan ->
# intense blue (fully flowing, log10 >= 2.5). Inverted from the original
# reference per user request: barren rivers are red, flowing rivers are blue.
RAMP = [
    (-1.0, np.array([230, 60, 50])),    # 0.1 m³/s: red / barren
    (0.0, np.array([235, 180, 60])),    # 1 m³/s: amber / low flow
    (1.0, np.array([70, 200, 220])),    # 10 m³/s: cyan / moderate flow
    (3.0, np.array([40, 100, 240])),    # 1,000+ m³/s: intense blue
]


def colormap(log_dis: float) -> np.ndarray:
    log_dis = min(max(log_dis, RAMP[0][0]), RAMP[-1][0])
    for i in range(len(RAMP) - 1):
        t0, c0 = RAMP[i]
        t1, c1 = RAMP[i + 1]
        if t0 <= log_dis <= t1:
            frac = (log_dis - t0) / ((t1 - t0) or 1.0)
            return c0 * (1 - frac) + c1 * frac
    return RAMP[-1][1]


def style_for(ord_flow: int, dis_av_cms: float) -> tuple[float, float, np.ndarray]:
    """Return (line_width_px, brightness 0..1, RGB colour) for a segment."""
    flow = ord_flow or 10
    width = max(0.6, (11 - flow) * 0.55)
    dis = dis_av_cms or 0.05
    brightness = np.clip(dis ** 0.25 / 2.2, 0.18, 1.0)
    color = colormap(np.log10(dis))
    return width, float(brightness), color


def build() -> None:
    data = json.loads(RIVERS_GEOJSON.read_text())
    # Draw on a larger canvas for the glow, then blur + composite down.
    core = Image.new("RGBA", (MAP_WIDTH, MAP_HEIGHT), (0, 0, 0, 0))
    glow = Image.new("RGBA", (MAP_WIDTH, MAP_HEIGHT), (0, 0, 0, 0))
    core_draw = ImageDraw.Draw(core)
    glow_draw = ImageDraw.Draw(glow)

    for feat in data["features"]:
        props = feat["properties"]
        width, bright, color = style_for(props.get("ord_flow"), props.get("dis_av_cms"))
        coords = feat["geometry"]["coordinates"]
        pts = [proj.lonlat_to_px(lon, lat) for lon, lat in coords]
        if len(pts) < 2:
            continue
        # Lighten the core toward white while retaining the red-to-blue
        # discharge colour in the surrounding glow.
        core_rgb = tuple(int(v) for v in np.clip(color * 0.6 + 255 * 0.4, 0, 255))
        glow_rgb = tuple(int(v) for v in np.clip(color, 0, 255))
        alpha = int(255 * min(1.0, 0.35 + 0.65 * bright))
        core_draw.line(pts, fill=core_rgb + (alpha,), width=max(1, round(width)))
        glow_draw.line(pts, fill=glow_rgb + (alpha,), width=max(2, round(width * 2.2)))

    glow = glow.filter(ImageFilter.GaussianBlur(radius=3.0))
    out = Image.alpha_composite(glow, core)

    # Featured-river callouts belong only to the research preset.
    out_draw = ImageDraw.Draw(out)
    for name, (lon, lat) in FEATURED_RIVER_MOUTHS.items() if FEATURED_RIVERS else ():
        x, y = proj.lonlat_to_px(lon, lat)
        r = 4
        out_draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 230))
        out_draw.ellipse([x - r * 2.5, y - r * 2.5, x + r * 2.5, y + r * 2.5], outline=(140, 255, 235, 140), width=2)

    out.save(RIVERS_PNG)
    print(f"[rivers-layer] wrote {RIVERS_PNG}")

    # Persist only preset-specific callouts; generic areas have none.
    meta = {name: proj.lonlat_to_px(lon, lat) for name, (lon, lat) in FEATURED_RIVER_MOUTHS.items() if FEATURED_RIVERS}
    (DATA_CURATED / "featured_rivers.json").write_text(json.dumps(meta, indent=2))


if __name__ == "__main__":
    build()
