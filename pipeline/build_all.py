"""Compatibility entry point for building the current request's bundle."""
from __future__ import annotations

import sys

import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import Request, data_root, default_request
from pipeline.jobs import Reporter, JobContext, BundlePaths
from pipeline.build import build_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-json")
    parser.add_argument("--data-root", type=Path)
    args = parser.parse_args()
    request = Request.from_dict(json.loads(args.request_json)) if args.request_json else default_request()
    root = args.data_root or data_root()
    paths = BundlePaths(root, request.request_id).ensure()
    context = JobContext(request, paths, Reporter(request))
    build_bundle(context)


if __name__ == "__main__":
    main()
