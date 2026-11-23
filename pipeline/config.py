"""Request-scoped configuration for the FossPulser pipeline.

The request and bundle environment are read whenever a legacy renderer asks
for a path. This avoids import-order-dependent writes to a checkout directory.
New adapters should use their JobContext directly.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_START = dt.date(2022, 1, 1)
DEFAULT_BBOX = (29.0, 29.3, 36.0, 33.4)
DEFAULT_WIDTH = 1200
MAX_PIXELS = 16_000_000
MAX_AREA_DEGREES = 2500.0


@dataclass(frozen=True)
class Bbox:
    west: float
    south: float
    east: float
    north: float
    enforce_area_limit: bool = True

    def __post_init__(self) -> None:
        values = (self.west, self.south, self.east, self.north)
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
            raise ValueError("bounds must be numeric")
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("bounds must be finite")
        if not all(-180.0 <= value <= 180.0 for value in (self.west, self.east)):
            raise ValueError("longitudes must be between -180 and 180")
        if not all(-90.0 <= value <= 90.0 for value in (self.south, self.north)):
            raise ValueError("latitudes must be between -90 and 90")
        if self.south >= self.north:
            raise ValueError("south must be less than north")
        if self.west == self.east:
            raise ValueError("west and east must differ")
        if self.enforce_area_limit and self.area_degrees > MAX_AREA_DEGREES:
            raise ValueError(f"requested area exceeds {MAX_AREA_DEGREES} square degrees")

    @classmethod
    def from_tuple(cls, values: Iterable[float], *, enforce_area_limit: bool = True) -> "Bbox":
        values = tuple(values)
        if len(values) != 4:
            raise ValueError("bounds require west, south, east, and north")
        return cls(*values, enforce_area_limit=enforce_area_limit)

    @property
    def crosses_dateline(self) -> bool:
        return self.west > self.east

    @property
    def width_deg(self) -> float:
        return (360.0 - self.west + self.east) if self.crosses_dateline else self.east - self.west

    @property
    def height_deg(self) -> float:
        return self.north - self.south

    @property
    def area_degrees(self) -> float:
        return self.width_deg * self.height_deg

    @property
    def center(self) -> tuple[float, float]:
        longitude = self.west + self.width_deg / 2.0
        return ((longitude + 180.0) % 360.0) - 180.0, (self.south + self.north) / 2.0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.west, self.south, self.east, self.north

    def padded(self, degrees: float) -> "Bbox":
        west = ((self.west - degrees + 180.0) % 360.0) - 180.0
        east = ((self.east + degrees + 180.0) % 360.0) - 180.0
        return Bbox(west, max(-90.0, self.south - degrees), east, min(90.0, self.north + degrees))

    def intervals(self) -> list[tuple[float, float]]:
        return [(self.west, self.east)] if not self.crosses_dateline else [(self.west, 180.0), (-180.0, self.east)]

    def intersects(self, other: "Bbox") -> bool:
        return any(a0 <= b1 and a1 >= b0 and self.south <= other.north and self.north >= other.south
                   for a0, a1 in self.intervals() for b0, b1 in other.intervals())

    def intersection(self, other: "Bbox") -> "Bbox | None":
        if not self.intersects(other):
            return None
        if self.crosses_dateline or other.crosses_dateline:
            raise ValueError("split dateline-crossing requests before intersection")
        west, east = max(self.west, other.west), min(self.east, other.east)
        south, north = max(self.south, other.south), min(self.north, other.north)
        return Bbox(west, south, east, north) if west < east and south < north else None


@dataclass(frozen=True)
class Request:
    bbox: Bbox
    start_date: dt.date = DEFAULT_START
    end_date: dt.date | None = None
    name: str = "custom-area"
    width: int = DEFAULT_WIDTH
    sources: tuple[str, ...] = ("bathymetry", "boundaries", "rivers", "facilities", "chlorophyll", "sst", "precipitation")
    preset: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "end_date", self.end_date or dt.date.today())
        if self.start_date > self.end_date:
            raise ValueError("start date must not be after end date")
        if not 64 <= self.width <= 4096:
            raise ValueError("map width must be between 64 and 4096 pixels")
        if self.height * self.width > MAX_PIXELS:
            raise ValueError("requested raster exceeds the pixel/memory limit")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,59}", self.name):
            raise ValueError("name must be a portable lowercase identifier")
        if not self.sources:
            raise ValueError("at least one source must be selected")

    @property
    def height(self) -> int:
        return max(1, round(self.width * self.bbox.height_deg / self.bbox.width_deg))

    @property
    def request_id(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return f"{self.name}-{hashlib.sha256(payload.encode()).hexdigest()[:12]}"

    def to_dict(self) -> dict:
        return {"name": self.name, "bbox": list(self.bbox.as_tuple()),
                "start_date": self.start_date.isoformat(), "end_date": self.end_date.isoformat(),
                "width": self.width, "height": self.height, "sources": list(self.sources), "preset": self.preset}

    @classmethod
    def from_dict(cls, value: dict) -> "Request":
        return cls(Bbox.from_tuple(value["bbox"]),
                   dt.date.fromisoformat(value.get("start_date", DEFAULT_START.isoformat())),
                   dt.date.fromisoformat(value["end_date"]) if value.get("end_date") else None,
                   value.get("name", "custom-area"), int(value.get("width", DEFAULT_WIDTH)),
                   tuple(value.get("sources", cls.__dataclass_fields__["sources"].default)), value.get("preset"))


@dataclass(frozen=True)
class BundlePaths:
    root: Path
    bundle_id: str

    @property
    def directory(self) -> Path:
        return self.root / "bundles" / self.bundle_id

    @property
    def raw(self) -> Path:
        return self.directory / "raw"

    @property
    def curated(self) -> Path:
        return self.directory / "curated"

    @property
    def frames(self) -> Path:
        return self.directory / "frames"

    @property
    def manifest(self) -> Path:
        return self.frames / "manifest.json"

    @property
    def metadata(self) -> Path:
        return self.directory / "bundle.json"

    def ensure(self) -> "BundlePaths":
        for path in (self.raw, self.curated, self.frames):
            path.mkdir(parents=True, exist_ok=True)
        return self


def data_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get("FOSSPULSER_DATA_DIR"):
        return Path(os.environ["FOSSPULSER_DATA_DIR"]).expanduser().resolve()
    return (ROOT / "data").resolve()


def default_request() -> Request:
    return Request(Bbox.from_tuple(DEFAULT_BBOX), name="eastern-mediterranean", preset="eastern_mediterranean")


def active_request() -> Request:
    raw = os.environ.get("FOSSPULSER_REQUEST_JSON")
    return Request.from_dict(json.loads(raw)) if raw else default_request()


def bundle_dir() -> Path:
    return Path(os.environ.get("FOSSPULSER_BUNDLE_DIR", ROOT / "data")).resolve()


def _request_value(name: str):
    request = active_request()
    values = {
        "LAND_BBOX": request.bbox.as_tuple(), "SEA_BBOX": request.bbox.as_tuple(),
        "RIVER_BBOX": request.bbox.padded(0.2).as_tuple(), "HISTORY_START": request.start_date,
        "MAP_WIDTH": request.width, "MAP_HEIGHT": request.height,
        "FEATURED_RIVERS": ("Yarkon", "Sorek") if request.preset == "eastern_mediterranean" else (),
        "CAMERA_TARGET_LON": request.bbox.center[0], "CAMERA_TARGET_LAT": request.bbox.center[1],
    }
    return values[name]


def __getattr__(name: str):
    if name in {"LAND_BBOX", "SEA_BBOX", "RIVER_BBOX", "HISTORY_START", "MAP_WIDTH", "MAP_HEIGHT", "FEATURED_RIVERS", "CAMERA_TARGET_LON", "CAMERA_TARGET_LAT"}:
        return _request_value(name)
    paths = {
        "DATA_RAW": ("raw",), "DATA_CURATED": ("curated",), "DATA_FRAMES": ("frames",),
        "BATHYMETRY_TIF": ("raw", "bathymetry.tif"), "CHLOROPHYLL_NC": ("raw", "chlorophyll.nc"),
        "CHLOROPHYLL_GLOBAL_NC": ("raw", "chlorophyll_global_noaa.nc"), "SST_NC": ("raw", "sst.nc"),
        "PRECIP_JSON": ("curated", "precipitation.json"), "RIVERS_GEOJSON": ("curated", "rivers.geojson"),
        "BOUNDARIES_GEOJSON": ("curated", "boundaries.geojson"), "PLACES_GEOJSON": ("curated", "places.geojson"),
        "LAKES_GEOJSON": ("curated", "lakes.geojson"), "FACILITIES_GEOJSON": ("curated", "facilities.geojson"),
        "HYDROWASTE_ZIP": ("raw", "HydroWASTE_v10.zip"), "MANIFEST_JSON": ("frames", "manifest.json"),
        "GLOBAL_CONTEXT_PNG": ("curated", "global_context.png"),
    }
    if name in paths:
        return bundle_dir().joinpath(*paths[name])
    constants = {
        "GLOBAL_BBOX": (-180.0, -90.0, 180.0, 90.0), "GLOBAL_WIDTH": 1440, "GLOBAL_HEIGHT": 720,
        "HYDROWASTE_CSV_NAME": "HydroWASTE_v10.csv", "PRECIP_GRID_LONS": 6, "PRECIP_GRID_LATS": 8,
        "TIME_STEPS": 365, "GMRT_URL": "https://www.gmrt.org/services/GridServer",
        "NATURAL_EARTH_COUNTRIES": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_admin_0_countries.zip",
        "NATURAL_EARTH_PLACES": "https://naturalearth.s3.amazonaws.com/10m_cultural/ne_10m_populated_places.zip",
        "NATURAL_EARTH_LAKES": "https://naturalearth.s3.amazonaws.com/10m_physical/ne_10m_lakes.zip",
        "Z_SCALE_PRESETS": (1.0, 3.0, 6.0), "CAMERA_PHI_DEG": 205.0, "CAMERA_THETA_DEG": 58.0,
        "CAMERA_FOV_DEG": 42.0, "CAMERA_RADIUS_M": 55000.0,
    }
    if name in constants:
        return constants[name]
    raise AttributeError(name)
