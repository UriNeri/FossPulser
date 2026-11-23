# Development and validation

## Architecture

The Rust app owns presentation, runtime bundle discovery, and worker lifecycle. The Python pipeline owns provider access, request-scoped processing, rendering, and manifest publication. They communicate through a newline-delimited JSON worker protocol.

A request contains bounds, dates, selected sources, map width, and an optional preset. A job writes into a staging bundle, validates the manifest and required basemap, then atomically activates the result. The previous active bundle is not replaced by a failed job.

## Useful commands

```bash
pixi install
pixi run python -m pipeline inspect --west -120 --south 20 --east -110 --north 30
pixi run python -m pipeline fetch --preset eastern-mediterranean --data-root /absolute/path/to/local/data
pixi run python -m pipeline migrate /path/to/original/data /path/to/external/data
pixi run python -m compileall -q pipeline
cargo fmt --manifest-path app/Cargo.toml -- --check
cargo check --manifest-path app/Cargo.toml
cargo test --manifest-path app/Cargo.toml
```

Use synthetic fixtures for tests. Do not commit provider samples, generated frames, local credentials, or environment directories.

## Worker protocol

Start the worker with `python -m pipeline.worker` from the repository root. Send one JSON object on standard input:

```json
{
  "request": {
    "name": "example",
    "bbox": [-120, 20, -110, 30],
    "start_date": "2022-01-01",
    "end_date": "2024-01-02",
    "width": 1200,
    "sources": ["bathymetry", "boundaries"]
  },
  "data_root": "/absolute/local/data/root",
  "build": true
}
```

An optional `credentials.copernicus` object can contain `username` and `password`. It is read from standard input only and is never emitted in progress events. Output events include `request_id`, `stage`, `status`, `source`, optional counts/bytes, a message, and a final manifest path.

## Validation expectations

Test both a valid local bundle and an empty data root. Test non-Mediterranean bounds, missing credentials, missing optional layers, exact-date gaps, cancellation, and partial source failures. Confirm that no job writes into the repository unless the repository is explicitly selected as the local data root.

The current verified build environment is Linux/Pixi. Windows cross-compilation instructions are retained in [`WINDOWS_CROSS_COMPILE.md`](../WINDOWS_CROSS_COMPILE.md), but runtime packaging must not include provider data.
