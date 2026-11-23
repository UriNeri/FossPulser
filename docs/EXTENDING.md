# Reusing and extending FossPulser

## Add a geographic area

Use the GUI numeric bounds or the CLI:

```bash
pixi run python -m pipeline fetch \
  --west 170 --south -35 --east 180 --north -25 \
  --name southwest-pacific \
  --start-date 2022-01-01 --end-date 2024-01-02 \
  --data-root /absolute/path/to/local/fosspulser-data
```

Bounds are WGS84 `(west, south, east, north)`. Longitudes are in `[-180, 180]`, latitudes in `[-90, 90]`, and south must be below north. A west value greater than east represents a dateline-crossing request; source adapters may split such requests into two ordinary intervals. Requests have area, raster-pixel, and date validation limits to avoid accidental global high-resolution downloads.

The default date range starts at `2022-01-01`; a blank end date means latest available. Each source can have a shorter actual range. Inspect the resulting bundle metadata and source statuses before interpreting the map.

## Add a reusable preset

Edit [`config/regions.toml`](../config/regions.toml) and add a named preset with bounds, default start date, and optional project-owned callouts. Keep regional facility curation and featured-river labels in preset configuration rather than hardcoding them into generic source adapters. The Eastern Mediterranean preset is the only preset with the built-in synthetic taxonomy demonstration.

A preset should contain project-authored metadata only. Do not add downloaded shapefiles, NetCDF files, provider rasters, or credentials.

## Add a source adapter

1. Add a module under [`pipeline/sources/`](../pipeline/sources/) implementing a function that accepts a `JobContext`.
2. Declare official product URLs, dataset IDs, citations, license/terms links, coverage, and authentication requirements in [`config/sources.toml`](../config/sources.toml) and [`docs/DATA_SOURCES.md`](DATA_SOURCES.md).
3. Use the shared streaming downloader and safe archive extraction helpers. Write to a temporary path, validate the response, and rename atomically.
4. Use `context.request.bbox` and the requested dates. Do not read a process-global regional bounding box.
5. Emit progress through the context and return explicit `complete`, `partial`, or `unavailable` status. Do not silently substitute a different cadence, region, or variable.
6. Keep credentials in memory. Use the official client’s environment/user-store behavior or the session credential envelope; never log secrets.
7. Register the adapter in the worker and expose its capability in the source registry and GUI.

## Add a rendered layer

Rendering stages receive a request-scoped bundle directory through the worker environment. Use the shared projection and manifest geometry checks. Every layer must have the same bounds and pixel dimensions as the active request, or be rejected before publication.

Add optional layers as optional manifest fields. The app should hide unavailable controls and explain the absence rather than displaying a blank layer as if it were an observation.

## Add taxonomy observations

Synthetic taxonomy values are intentionally limited to the Eastern Mediterranean research preset. For another region, supply an explicit user-owned taxonomy configuration and label its provenance. Do not generate values that could be mistaken for measurements. Keep observations and their source metadata outside the provider-data redistribution path unless you have permission to share them.

## Scientific cautions

- HydroRIVERS discharge is a static long-term average.
- Open-Meteo precipitation is sampled at coarse points.
- Marine satellite algorithms are not automatically valid for inland lakes, rivers, or extremely saline water bodies.
- Missing same-day observations remain missing; spatial interpolation must not be described as new observation coverage.
- Equirectangular rendering is a visualization choice, not a geodesic analysis projection. High-latitude and very large-area requests need additional validation.
