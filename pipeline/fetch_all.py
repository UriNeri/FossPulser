"""Compatibility entry point for the request-driven acquisition worker."""
from __future__ import annotations

import sys

import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import Request, data_root, default_request
from pipeline.worker import run_request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-json", help="request object as JSON")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--fetch-only", action="store_true")
    args = parser.parse_args()
    request = Request.from_dict(json.loads(args.request_json)) if args.request_json else default_request()
    run_request(request, root=args.data_root or data_root(), build=not args.fetch_only)


if __name__ == "__main__":
    main()
