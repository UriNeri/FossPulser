"""GMRT terrain compatibility entry point. Use ``python -m pipeline fetch`` for new work."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import Request, data_root, default_request
from pipeline.jobs import BundlePaths, JobContext, Reporter
from pipeline.sources.terrain import fetch_terrain


def main() -> None:
    request = Request.from_dict(json.loads(os.environ["FOSSPULSER_REQUEST_JSON"])) if os.environ.get("FOSSPULSER_REQUEST_JSON") else default_request()
    root = data_root()
    paths = BundlePaths(root, request.request_id).ensure()
    os.environ["FOSSPULSER_BUNDLE_DIR"] = str(paths.directory)
    context = JobContext(request, paths, Reporter(request))
    fetch_terrain(context)


if __name__ == "__main__":
    main()
