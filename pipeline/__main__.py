"""Command-line interface for local, user-initiated data acquisition."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

# Defer pipeline imports until after the request environment is initialized.


def request_from_args(args) -> Request:
    from pipeline.config import Bbox, Request, default_request
    if args.preset:
        base = default_request()
        bbox = base.bbox
        name = base.name
        preset = base.preset
    else:
        bbox = Bbox(args.west, args.south, args.east, args.north)
        name = args.name
        preset = None
    return Request(
        bbox=bbox,
        start_date=dt.date.fromisoformat(args.start_date),
        end_date=dt.date.fromisoformat(args.end_date) if args.end_date else None,
        name=name,
        width=args.width,
        sources=tuple(args.sources.split(",")) if args.sources else default_request().sources,
        preset=preset,
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="fosspulser-pipeline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="fetch and build a local bundle")
    fetch.add_argument("--preset", choices=["eastern-mediterranean"])
    fetch.add_argument("--west", type=float, default=29.0)
    fetch.add_argument("--south", type=float, default=29.3)
    fetch.add_argument("--east", type=float, default=36.0)
    fetch.add_argument("--north", type=float, default=33.4)
    fetch.add_argument("--name", default="custom-area")
    fetch.add_argument("--start-date", default="2022-01-01")
    fetch.add_argument("--end-date")
    fetch.add_argument("--width", type=int, default=1200)
    fetch.add_argument("--sources", help="comma-separated source names")
    fetch.add_argument("--data-root", type=Path)
    fetch.add_argument("--fetch-only", action="store_true")
    inspect = sub.add_parser("inspect", help="show source capabilities for a request")
    inspect.add_argument("--west", type=float, required=True)
    inspect.add_argument("--south", type=float, required=True)
    inspect.add_argument("--east", type=float, required=True)
    inspect.add_argument("--north", type=float, required=True)
    inspect.add_argument("--start-date", default="2022-01-01")
    inspect.add_argument("--end-date")
    inspect.add_argument("--name", default="custom-area")
    inspect.add_argument("--width", type=int, default=1200)
    inspect.add_argument("--sources")
    inspect.add_argument("--preset", action="store_true")
    migrate = sub.add_parser("migrate", help="copy an original data tree into an external bundle")
    migrate.add_argument("source", type=Path)
    migrate.add_argument("destination", type=Path)
    worker = sub.add_parser("worker", help="read a JSON request envelope from stdin")
    args = parser.parse_args()
    if args.command == "migrate":
        from pipeline.migrate import migrate as run_migration
        print(run_migration(args.source, args.destination))
        return 0
    if args.command == "worker":
        from pipeline.worker import main as worker_main
        return worker_main()
    request = request_from_args(args)
    os.environ["FOSSPULSER_REQUEST_JSON"] = json.dumps(request.to_dict())
    if args.command == "inspect":
        from pipeline.sources.registry import capabilities
        print(json.dumps({"request": request.to_dict(), "sources": capabilities(request)}, indent=2))
        return 0
    from pipeline.worker import run_request
    run_request(request, root=args.data_root, build=not args.fetch_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
