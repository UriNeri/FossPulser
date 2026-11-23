"""JSON-lines worker used by the desktop app and automation."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def run_request(request, *, root: Path | None = None, credentials: dict | None = None,
                build: bool = True, stream=None) -> Path:
    # This function deliberately imports no request-dependent module before
    # setting both values. The legacy renderers read them during import.
    root = (root or Path(os.environ.get("FOSSPULSER_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))).resolve()
    from pipeline.jobs import staging_context
    context = staging_context(request, root, stream=stream, credentials=credentials)
    os.environ["FOSSPULSER_REQUEST_JSON"] = json.dumps(request.to_dict())
    os.environ["FOSSPULSER_BUNDLE_DIR"] = str(context.paths.directory)
    from pipeline.jobs import JobCancelled, discard_staging, publish_bundle
    from pipeline.sources.facilities import fetch_facilities
    from pipeline.sources.precipitation import fetch_precipitation
    from pipeline.sources.rivers import fetch_rivers
    from pipeline.sources.satellite import fetch_chlorophyll, fetch_sst
    from pipeline.sources.terrain import fetch_boundaries, fetch_terrain
    fetchers = {
        "bathymetry": fetch_terrain, "boundaries": fetch_boundaries, "rivers": fetch_rivers,
        "facilities": fetch_facilities, "chlorophyll": fetch_chlorophyll, "sst": fetch_sst,
        "precipitation": fetch_precipitation,
    }
    try:
        context.reporter.emit("job", status="running", message="starting acquisition")
        for name, fetcher in fetchers.items():
            if name not in request.sources:
                context.status(name, "skipped", "not selected")
                continue
            context.check_cancelled()
            try:
                fetcher(context)
            except Exception as error:
                import traceback
                traceback.print_exc(file=sys.stderr)
                context.status(name, "unavailable", f"{type(error).__name__}: acquisition failed: {error}")
        if build:
            from pipeline.build import build_bundle
            build_bundle(context)
        return publish_bundle(context)
    except JobCancelled:
        context.reporter.emit("job", status="cancelled", message="job cancelled")
        raise
    except Exception as error:
        context.reporter.emit("job", status="failed", message=f"{type(error).__name__}: {error}")
        raise
    finally:
        discard_staging(context)


def main() -> int:
    envelope = json.loads(sys.stdin.readline())
    from pipeline.config import Request, data_root
    request = Request.from_dict(envelope["request"])
    root = Path(envelope["data_root"]) if envelope.get("data_root") else data_root()
    os.environ["FOSSPULSER_REQUEST_JSON"] = json.dumps(request.to_dict())
    try:
        run_request(request, root=root, credentials=envelope.get("credentials"), build=envelope.get("build", True))
        return 0
    except Exception as error:
        if error.__class__.__name__ == "JobCancelled":
            return 130
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
