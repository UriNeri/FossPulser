"""Non-destructively migrate the original local data tree into a bundle."""
from __future__ import annotations

import sys

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import BundlePaths, Request, data_root, default_request
from pipeline.jobs import atomic_json


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def migrate(source: Path, destination: Path, activate: bool = True) -> Path:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not (source / "frames" / "manifest.json").is_file():
        raise FileNotFoundError(f"no original manifest under {source}")
    if destination == source or source in destination.parents:
        raise ValueError("destination must be outside the original data directory")
    request = default_request()
    manifest = json.loads((source / "frames" / "manifest.json").read_text())
    request = Request.from_dict({
        **request.to_dict(),
        "bbox": manifest["map_bbox"],
        "width": manifest["map_width"],
        "start_date": manifest["chlorophyll_frames"][0]["date"] if manifest.get("chlorophyll_frames") else "2022-01-01",
        "end_date": manifest["chlorophyll_frames"][-1]["date"] if manifest.get("chlorophyll_frames") else None,
    })
    paths = BundlePaths(destination, request.request_id).ensure()
    for name in ("raw", "curated", "frames"):
        source_dir = source / name
        if source_dir.exists():
            target = paths.directory / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source_dir, target)
    for filename in ("HydroWASTE_v10.zip", "hybas_lake_as_lev01-12_v1c.zip"):
        if (source / filename).is_file():
            shutil.copy2(source / filename, paths.raw / filename)
    # Keep the original manifest paths, but add the new metadata alongside it.
    atomic_json(paths.metadata, {
        "schema_version": 2,
        "bundle_id": request.request_id,
        "request": request.to_dict(),
        "migrated_from": str(source),
        "migrated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sources": {"migration": {"status": "complete", "message": "preserved original local research data"}},
        "manifest": "frames/manifest.json",
    })
    if activate:
        atomic_json(destination / "active.json", {
            "schema_version": 2,
            "bundle_id": request.request_id,
            "manifest": str(paths.manifest.relative_to(destination)),
        })
    return paths.manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="original data directory; never modified")
    parser.add_argument("destination", type=Path, help="external local data root")
    parser.add_argument("--no-activate", action="store_true")
    args = parser.parse_args()
    print(migrate(args.source, args.destination, not args.no_activate))


if __name__ == "__main__":
    main()
