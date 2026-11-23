"""Build a request-scoped bundle using the established rendering stages."""
from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path

from pipeline.jobs import JobContext

STAGES = (
    ("global-context", "build_global_context"),
    ("basemap", "build_basemap"),
    ("bathymetry-layer", "build_bathymetry_layer"),
    ("rivers-layer", "build_rivers_layer"),
    ("chlorophyll-layer", "build_chlorophyll_layers"),
    ("sst-layer", "build_sst_layers"),
    ("precipitation-layer", "build_precip_layer"),
    ("labels", "build_labels"),
    ("manifest", "build_manifest"),
)


def _run_stage(module: str) -> None:
    # The legacy modules intentionally remain usable as standalone scripts.
    # Their imports resolve from the pipeline directory, while config.py reads
    # the request and staging directory supplied by the worker.
    runpy.run_module(f"pipeline.{module}", run_name="__main__")


def build_bundle(context: JobContext) -> None:
    os.environ["FOSSPULSER_REQUEST_JSON"] = json.dumps(context.request.to_dict())
    os.environ["FOSSPULSER_BUNDLE_DIR"] = str(context.paths.directory)
    # A stage may be unavailable independently. Do not invoke a renderer when
    # its required input is absent; record the reason in the final manifest.
    for stage, module in STAGES:
        context.check_cancelled()
        if stage == "global-context" and not (context.paths.raw / "ne_countries").exists():
            context.status("global-context", "unavailable", "Natural Earth input was not fetched")
            continue
        if stage == "basemap" and not (context.paths.raw / "bathymetry.tif").exists():
            context.status("basemap", "unavailable", "terrain input was not fetched")
            continue
        if stage == "bathymetry-layer" and not (context.paths.curated / "basemap_fields.npz").exists():
            context.status("bathymetry-layer", "unavailable", "basemap fields are unavailable")
            continue
        if stage == "rivers-layer" and not (context.paths.curated / "rivers.geojson").exists():
            context.status("rivers-layer", "unavailable", "no river input")
            continue
        if stage == "chlorophyll-layer" and not (context.paths.raw / "chlorophyll.nc").exists():
            context.status("chlorophyll-layer", "unavailable", "no chlorophyll input")
            continue
        if stage == "sst-layer" and not (context.paths.raw / "sst.nc").exists():
            context.status("sst-layer", "unavailable", "no SST input")
            continue
        if stage == "precipitation-layer" and not (context.paths.raw / "precipitation_raw.json").exists():
            context.status("precipitation-layer", "unavailable", "no precipitation input")
            continue
        if stage == "labels" and not (context.paths.curated / "facilities.geojson").exists():
            # Labels can still be generated from places with an empty facility
            # collection, so create only the project-owned empty container.
            (context.paths.curated / "facilities.geojson").write_text('{"type":"FeatureCollection","features":[]}')
        context.report("build", stage, f"building {stage}")
        try:
            _run_stage(module)
        except Exception as error:
            if stage == "manifest":
                raise
            import traceback
            traceback.print_exc(file=sys.stderr)
            context.status(stage, "unavailable", f"{type(error).__name__}: rendering failed: {error}")
            # A missing optional layer must not prevent the rest of the map.
            continue
        context.status(stage, "complete", f"{stage} built")
    manifest_path = context.paths.manifest
    if not manifest_path.exists():
        raise RuntimeError("manifest stage did not produce an output")
    manifest = json.loads(manifest_path.read_text())
    manifest.update({
        "schema_version": 2,
        "bundle_id": context.request.request_id,
        "request": context.request.to_dict(),
        "source_status": context.statuses,
        "taxonomy_demo": context.request.preset == "eastern_mediterranean",
        "data_policy": "Local user-fetched data; no provider data is bundled with the source repository.",
    })
    manifest_path.write_text(json.dumps(manifest, indent=2))
    context.report("build", "manifest", "manifest metadata updated")
