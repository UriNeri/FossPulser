"""Shared network, cache, and credential helpers for source adapters."""
from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Callable

import requests

from pipeline.jobs import JobContext

USER_AGENT = "FossPulser/0.2 (research visualization; user-initiated download)"


def cache_key(url: str, **parameters) -> str:
    payload = json.dumps({"url": url, **parameters}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def download(context: JobContext, url: str, destination: Path, *, source: str,
             params: dict | None = None, timeout: int = 180, force: bool = False) -> Path:
    """Stream a response to a temporary file and atomically publish it."""
    if destination.is_file() and destination.stat().st_size > 0 and not force:
        context.report("download", source, f"using cached {destination.name}", completed=1, total=1)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    context.check_cancelled()
    with requests.get(url, params=params, stream=True, timeout=timeout,
                      headers={"User-Agent": USER_AGENT}) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", "0")) or None
        completed = 0
        with temporary.open("wb") as output:
            for block in response.iter_content(chunk_size=1024 * 1024):
                context.check_cancelled()
                if not block:
                    continue
                output.write(block)
                completed += len(block)
                context.report("download", source, f"downloading {destination.name}",
                               bytes_completed=completed, bytes_total=total)
    if temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"{source} returned an empty response")
    temporary.replace(destination)
    context.report("download", source, f"saved {destination.name}", completed=1, total=1,
                   bytes_completed=destination.stat().st_size, bytes_total=destination.stat().st_size)
    return destination


def get_json(context: JobContext, url: str, *, source: str, params: dict | None = None,
             timeout: int = 30, tries: int = 3):
    last_error = None
    for attempt in range(1, tries + 1):
        context.check_cancelled()
        try:
            response = requests.get(url, params=params, timeout=timeout,
                                    headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as error:
            last_error = error
            if attempt < tries:
                time.sleep(min(2 ** attempt, 15))
    raise RuntimeError(f"{source} metadata request failed") from last_error


def safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        root = destination.resolve()
        for member in zipped.infolist():
            target = (destination / member.filename).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError(f"unsafe path in archive: {member.filename}")
        zipped.extractall(destination)


def copernicus_credentials(context: JobContext) -> dict:
    """Return only in-memory credentials; never log or persist their values."""
    supplied = context.credentials.get("copernicus", {})
    username = supplied.get("username") or os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME")
    password = supplied.get("password") or os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD")
    if username and password:
        return {"username": username, "password": password}
    # An omitted argument lets the official client use its existing user store.
    return {}


def source_status(context: JobContext, source: str, status: str, message: str, **metadata) -> None:
    context.status(source, status, message, **metadata)
