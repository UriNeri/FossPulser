# FossPulser

An interactive research visualization for river networks, coastal satellite observations (chl + SST), precipitation point samples, wastewater facilities and desalination plants. It combines a native Rust/Rune desktop application with a Python acquisition and rendering pipeline.

The application is designed for local, user-initiated data acquisition. **Third-party data, derived provider rasters, credentials, and tokens are not included in this repository or in source releases.** Public availability does not by itself grant permission to redistribute every dataset; review each provider’s terms before sharing any local output.  

This app was built in large part using Large Language Models (LLMs) for research and development purposes.  

## Motivation for the app:
1. Demonstrate agentic app dev via Rune + Forge3D.
2. Counter paid data monopolies by enabling local, user-driven research.  

I saw several public facing researchers make infographics/animations for river networks/coastal observations, and mostly my request for sharing the underlying code were ignored. As far as I can see, the underlying data is PUBLIC and FREE, and some of it requires proper attribution and licensing compliance (including not redistributing, especially for commercial purposes).  
Secondly, without access to the code, reproducibility and verification of the results are impossible, which undermines scientific integrity and transparency. In that public facing (x + linkedin post) animation that led to this, some of the visuals look off, inconsistent, or misleading to me, but without the underlying code and data, I cannot confirm or correct these issues.  
Manipulation of visualisations is a dangerous practice and having the fallacy of looking polished, authoritative or scientific creates a false sense of credibility. 
For example, imagine the bounding box in the demo (webm) below did not include the Egypt's nile delta or waste water outflows - the source of algal blooms could not appear as starting from a location that is out of frame.  
Similarly, imagine including historical (e.g. previous year, or weekly cumulative values) river flow data without clearly indicating the time frame; or even worse - as some who lives next to some of these rivers - the percipitation data does not account for the watershed region and outflow when the waste water plants pipe out directly to the rivers/basins. In fact, many of the large rivers are diverted and any measured flow recorded could very likely not reach the sea at all, baring extreme events like floods.  
Other potential issues include selective data omission, exaggerated visual effects, or misleading color scales, all of which can distort the interpretation of the underlying scientific phenomena. E.g. the mg/ml3 values can be set to a scale that accounts for `[min,max]` of measured data used rather than currently displayed (bounded) values...  

And the data itself may be inadequate: is cloud coverage accounted for in the satellite observations? is the river chl/flow/layout calibrated and accounted for human interventions such as dams?...  

Note - this isn't an attack on any specific researcher or visualisation, just a personal rant. I'm also not at all an expert in this field but an observer, I really don't assume any misconduct or intentional misrepresentation on anyone's part.

## demo

![FossPulser Demo](demo.webm)


## Features

- Zoomable map with global Natural Earth context and detailed local bundles.
- Daily chlorophyll and sea-surface-temperature overlays where source coverage exists.
- Static long-term-average HydroRIVERS discharge styling, not live river-flow measurements.
- Coarse Open-Meteo/ERA5-derived precipitation point samples.
- HydroWASTE wastewater facilities and preset-owned desalination metadata.
- Numeric WGS84 bounds and a “fetch displayed area” action.
- Background downloads and rendering with per-source progress, warnings, cancellation, and partial-layer status.
- Local prefetched bundles loaded by default when available.
- Optional Copernicus Marine credentials supplied through the environment, the official user credential store, or session-only GUI fields.
- Synthetic taxonomy demonstrations only for the Eastern Mediterranean research preset or an explicit user-supplied configuration.

## Requirements

- Linux for the currently verified development environment.
- [Pixi](https://pixi.sh/) for the Python environment and native build libraries.
- A Rust toolchain matching [`rust-toolchain.toml`](rust-toolchain.toml).
- Network access for data acquisition. Copernicus Marine layers additionally require a free account; global NOAA layers do not use that account.

Install the environment and build the app:

```bash
pixi install
pixi run app-build
```

Run the app:

```bash
pixi run app-run
```

> **Build with the pixi tasks, not a raw `pixi run cargo build`.** The pixi
> environment includes a MinGW cross toolchain for the Windows build, and its
> activation exports `CC`/`CXX`/`AR` pointing at the MinGW compilers. The
> `app-build`/`app-run` tasks override those back to the conda Linux
> toolchain; a raw `pixi run cargo build --manifest-path ./app/Cargo.toml`
> compiles native C build scripts (e.g. blake3) into Windows COFF objects and
> fails to link on Linux. Building outside pixi (`cargo build`) works as usual.

The app discovers the Pixi Python worker automatically. Set `FOSSPULSER_WORKER` to a Python executable if the worker is elsewhere. The Python environment must contain the dependencies declared in [`pixi.toml`](pixi.toml).

## Local data and prefetched bundles

Data is resolved at runtime in this order:

1. `FOSSPULSER_DATA_DIR`, when set.
2. A `data/` directory beside the running executable, when present.
3. The repository `data/` directory as a development fallback.

A local data root contains an `active.json` pointer and versioned directories under `bundles/`. Each bundle has its own raw inputs, curated layers, frames, manifest, and non-secret metadata. New work is built in staging and activated only after validation. Existing bundles remain usable if a job fails or is cancelled.

To use an existing research bundle without copying it into the repository:

```bash
export FOSSPULSER_DATA_DIR=/absolute/path/to/local/fosspulser-data
pixi run app-run
```

To migrate an original FossPulser `data/` tree non-destructively:

```bash
pixi run python -m pipeline migrate /path/to/original/data /path/to/external/fosspulser-data
```

The source directory is never modified. Verify the migrated bundle before deleting any original copy.

## Fetch data from the GUI

1. Open **Fetch data**.
2. Enter west, south, east, and north in WGS84 degrees, or navigate the map and choose **Fetch displayed area**.
3. Set the start date and optionally an end date. A blank end date means the latest available observation. New requests default to `2022-01-01`.
4. Optionally enter Copernicus Marine credentials. These values are sent to the worker in memory for the current job only and are never saved by FossPulser.
5. Start the job and monitor source progress and warnings. Independent sources may complete even when another source is unavailable.

Large areas and long daily time ranges can require substantial bandwidth, memory, and disk space. Reduce the area or date range when necessary. A partial bundle is labeled explicitly; missing observations are not invented.

## Fetch data from the CLI

Inspect source capabilities without downloading:

```bash
pixi run python -m pipeline inspect \
  --west -120 --south 20 --east -110 --north 30 \
  --start-date 2022-01-01 --end-date 2024-01-02
```

Fetch and build a custom area:

```bash
pixi run python -m pipeline fetch \
  --west -120 --south 20 --east -110 --north 30 \
  --name pacific-example \
  --start-date 2022-01-01 --end-date 2024-01-02 \
  --data-root /absolute/path/to/local/fosspulser-data
```

Use the Eastern Mediterranean preset:

```bash
pixi run python -m pipeline fetch --preset eastern-mediterranean \
  --data-root /absolute/path/to/local/fosspulser-data
```

The worker protocol is newline-delimited JSON. It accepts a request envelope on standard input and emits progress events on standard output. Credentials must be supplied in that input envelope, never as command-line arguments.

## Credentials

The Copernicus client supports its normal user-level login flow and environment variables:

Set `COPERNICUSMARINE_SERVICE_USERNAME` and `COPERNICUSMARINE_SERVICE_PASSWORD` in your shell or local credential manager. Keep real values out of source files and command history where possible.

See [Copernicus Marine registration](https://data.marine.copernicus.eu/register) and the [Copernicus Marine Python client](https://github.com/mercator-ocean/copernicus-marine-toolbox). Do not commit a populated `.env`, credential store, token, or password. [` .env.example`](.env.example) contains variable names only.

## Source data and terms

See [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) for exact product IDs, official links, citations, coverage, authentication, and limitations. The main providers are:

- [Copernicus Marine Service](https://marine.copernicus.eu/) — Mediterranean chlorophyll and SST products.
- [NOAA CoastWatch ERDDAP](https://coastwatch.noaa.gov/erddap) — global daily VIIRS chlorophyll and blended GHRSST.
- [HydroRIVERS / HydroSHEDS](https://www.hydrosheds.org/products/hydrorivers) — static river network and long-term-average discharge.
- [HydroWASTE](https://doi.org/10.5194/essd-14-559-2022) — wastewater facility records; [Figshare archive](https://doi.org/10.6084/m9.figshare.14847786.v1).
- [Natural Earth](https://www.naturalearthdata.com/) — countries, populated places, and lakes.
- [GMRT](https://www.gmrt.org/) — terrain and bathymetry through its GridServer API.
- [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api) — ERA5-derived precipitation point queries.

## Libraries

- [Rune](https://github.com/anomalyco/rune) — native UI and animation.
- [Forge3D](https://github.com/milos-agathon/forge3d) — optional terrain rendering.
- [Pixi](https://pixi.sh/) — environment and task management.
- [Rust](https://www.rust-lang.org/) and [Serde](https://serde.rs/) — application and manifest/worker serialization.
- [Requests](https://requests.readthedocs.io/) — HTTP acquisition.
- [Xarray](https://docs.xarray.dev/) and [NetCDF4](https://unidata.github.io/netcdf4-python/) — scientific data handling.
- [Rasterio](https://rasterio.readthedocs.io/) — GeoTIFF input.
- [NumPy](https://numpy.org/) and [SciPy](https://scipy.org/) — numerical processing and interpolation.
- [Matplotlib](https://matplotlib.org/) and [Pillow](https://python-pillow.org/) — raster rendering.
- [PyShp](https://github.com/GeospatialPython/pyshp) — shapefile reading.
- [Copernicus Marine Toolbox](https://github.com/mercator-ocean/copernicus-marine-toolbox) — authenticated marine product access.

## Reuse and extension

See [`docs/EXTENDING.md`](docs/EXTENDING.md) for adding a geographic preset, source adapter, layer, or taxonomy configuration. See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for architecture and validation commands.

The code is released under the MIT license in [`LICENSE`](LICENSE). Provider data remains subject to its own licenses, terms, citations, and redistribution restrictions. The MIT license does not relicense third-party data.

## Publication safety

The source tree contains code and documentation only. Before publishing, review the source snapshot, release assets, and local configuration for credentials or generated data. Release guidance is provided in [`docs/PUBLICATION.md`](docs/PUBLICATION.md).
