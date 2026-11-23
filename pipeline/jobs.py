"""Job orchestration, structured progress, and atomic local bundle publication."""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from pipeline.config import BundlePaths, Request, data_root


class JobCancelled(RuntimeError):
    """Raised when the caller requests cooperative cancellation."""


@dataclass
class Event:
    request_id: str
    stage: str
    status: str
    source: str | None = None
    completed: int | None = None
    total: int | None = None
    bytes_completed: int | None = None
    bytes_total: int | None = None
    message: str = ""
    warning: str | None = None
    manifest: str | None = None

    def to_dict(self) -> dict:
        return {key: value for key, value in self.__dict__.items() if value is not None}


class Reporter:
    def __init__(self, request: Request, stream=None):
        self.request = request
        self.stream = stream or sys.stdout
        self._lock = threading.Lock()

    def emit(self, stage: str, status: str = "running", **kwargs) -> None:
        event = Event(self.request.request_id, stage, status, **kwargs)
        with self._lock:
            self.stream.write(json.dumps(event.to_dict(), separators=(",", ":")) + "\n")
            self.stream.flush()

    def progress(self, stage: str, source: str, completed: int | None = None,
                 total: int | None = None, message: str = "", **kwargs) -> None:
        self.emit(stage, source=source, completed=completed, total=total, message=message, **kwargs)


@dataclass
class JobContext:
    request: Request
    paths: BundlePaths
    reporter: Reporter
    cancel_event: threading.Event = field(default_factory=threading.Event)
    credentials: dict = field(default_factory=dict)
    statuses: dict = field(default_factory=dict)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled("job cancelled by user")

    def report(self, stage: str, source: str, message: str = "", **kwargs) -> None:
        self.check_cancelled()
        self.reporter.progress(stage, source, message=message, **kwargs)

    def status(self, source: str, status: str, message: str = "", **metadata) -> None:
        self.statuses[source] = {"status": status, "message": message, **metadata}
        self.reporter.emit("source", status=status, source=source, message=message)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def publish_bundle(context: JobContext, extra_metadata: dict | None = None) -> Path:
    """Validate and publish a completed staging bundle without damaging the old one."""
    context.check_cancelled()
    if not context.paths.manifest.is_file():
        raise RuntimeError("cannot publish a bundle without a manifest")
    manifest = json.loads(context.paths.manifest.read_text())
    if manifest.get("map_bbox") != list(context.request.bbox.as_tuple()):
        raise RuntimeError("manifest bounds do not match the request")
    if not manifest.get("basemap") or not (context.paths.curated / "basemap.png").is_file():
        raise RuntimeError("bundle has no basemap; inspect source errors before retrying")
    partial = any(value.get("status") in {"unavailable", "partial"} for value in context.statuses.values())
    metadata = {
        "schema_version": 2,
        "bundle_id": context.request.request_id,
        "request": context.request.to_dict(),
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sources": context.statuses,
        "manifest": "frames/manifest.json",
        **(extra_metadata or {}),
    }
    atomic_json(context.paths.metadata, metadata)
    final = BundlePaths(context.paths.root, context.request.request_id)
    final.directory.parent.mkdir(parents=True, exist_ok=True)
    if final.directory.exists():
        backup = final.directory.with_name(f".{final.directory.name}.old-{uuid.uuid4().hex}")
        os.replace(final.directory, backup)
        try:
            os.replace(context.paths.directory, final.directory)
        except Exception:
            os.replace(backup, final.directory)
            raise
        shutil.rmtree(backup)
    else:
        os.replace(context.paths.directory, final.directory)
    atomic_json(context.paths.root / "active.json", {
        "schema_version": 2,
        "bundle_id": context.request.request_id,
        "manifest": str(final.manifest.relative_to(context.paths.root)),
    })
    context.reporter.emit("complete", status="partial" if partial else "complete",
                          message="bundle activated with unavailable layers" if partial else "bundle activated",
                          manifest=str(final.manifest))
    return final.manifest


def staging_context(request: Request, root: Path | None = None,
                    stream=None, credentials: dict | None = None) -> JobContext:
    root = (root or data_root()).resolve()
    staging_id = f".{request.request_id}.{uuid.uuid4().hex}.staging"
    paths = BundlePaths(root / "staging", staging_id).ensure()
    # Shared provider caches belong to the data root, not the disposable
    # staging bundle. BundlePaths.root remains the publication root.
    paths = BundlePaths(root, f"staging/{staging_id}").ensure()
    return JobContext(request, paths, Reporter(request, stream), credentials=credentials or {})


def discard_staging(context: JobContext) -> None:
    shutil.rmtree(context.paths.directory, ignore_errors=True)
