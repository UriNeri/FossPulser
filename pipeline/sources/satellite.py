"""Coverage-aware Copernicus Marine and NOAA CoastWatch satellite adapters."""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import netCDF4 as nc
import requests
import xarray as xr

from pipeline.config import Bbox
from pipeline.jobs import JobContext
from pipeline.sources.common import copernicus_credentials, get_json

ERDDAP = "https://coastwatch.noaa.gov/erddap"
MEDITERRANEAN = Bbox(-6.0, 30.0, 36.5, 46.0, enforce_area_limit=False)
CHL_MY = "cmems_obs-oc_med_bgc-plankton_my_l4-gapfree-multi-1km_P1D"
CHL_NRT = "cmems_obs-oc_med_bgc-plankton_nrt_l4-gapfree-multi-1km_P1D"
SST_REP = "cmems_SST_MED_SST_L4_REP_OBSERVATIONS_010_021"
SST_NRT = "SST_MED_SST_L4_NRT_OBSERVATIONS_010_004_a_V2"


def _date_from_time(value) -> dt.date:
    return dt.date(value.year, value.month, value.day)


def _dataset_last_day(cm, dataset_id: str) -> dt.date:
    info = cm.describe(dataset_id=dataset_id, disable_progress_bar=True).products[0]
    for version in info.datasets[0].versions:
        for part in version.parts:
            for service in part.services:
                for variable in service.variables:
                    for coordinate in variable.coordinates:
                        if coordinate.coordinate_id == "time":
                            return dt.date(1970, 1, 1) + dt.timedelta(milliseconds=coordinate.maximum_value)
    raise RuntimeError(f"no time coordinate for {dataset_id}")


def _copernicus_field(context: JobContext, *, source: str, variable: str,
                      reprocessed: str, nrt: str, output: Path) -> bool:
    bbox = context.request.bbox
    if bbox.crosses_dateline or not bbox.intersects(MEDITERRANEAN):
        context.status(source, "unavailable", "Mediterranean Copernicus product does not cover this area")
        return False
    coverage = bbox.intersection(MEDITERRANEAN)
    if coverage is None:
        context.status(source, "unavailable", "no Mediterranean product intersection")
        return False
    try:
        import copernicusmarine as cm
        credentials = copernicus_credentials(context)
        end = context.request.end_date
        start = context.request.start_date
        last = _dataset_last_day(cm, reprocessed)
        parts = []
        rep_end = min(end, last)
        if start <= rep_end:
            context.report("download", source, f"requesting reprocessed {start}..{rep_end}")
            parts.append(cm.open_dataset(
                dataset_id=reprocessed, variables=[variable],
                minimum_longitude=coverage.west, maximum_longitude=coverage.east,
                minimum_latitude=coverage.south, maximum_latitude=coverage.north,
                start_datetime=f"{start}T00:00:00", end_datetime=f"{rep_end}T00:00:00",
                **credentials,
            ).load()[variable])
        if end > last:
            nrt_start = max(start, last + dt.timedelta(days=1))
            context.report("download", source, f"requesting near-real-time {nrt_start}..{end}")
            parts.append(cm.open_dataset(
                dataset_id=nrt, variables=[variable],
                minimum_longitude=coverage.west, maximum_longitude=coverage.east,
                minimum_latitude=coverage.south, maximum_latitude=coverage.north,
                start_datetime=f"{nrt_start}T00:00:00", end_datetime=f"{end}T00:00:00",
                **credentials,
            ).load()[variable])
        if not parts:
            raise RuntimeError("no observations in requested range")
        field = xr.concat(parts, dim="time").sortby("time") if len(parts) > 1 else parts[0]
        if source == "chlorophyll":
            field = field.rename("chlor_a").expand_dims("altitude", axis=1)
            field.attrs["units"] = "mg m-3"
        else:
            field = field.rename("sst_kelvin")
            field.attrs["units"] = "kelvin"
        temporary = output.with_suffix(".tmp.nc")
        field.to_netcdf(temporary)
        temporary.replace(output)
        context.status(source, "complete", "Mediterranean Copernicus data downloaded",
                       product=reprocessed, coverage=list(coverage.as_tuple()))
        return True
    except Exception as error:
        context.status(source, "unavailable", f"Copernicus unavailable: {type(error).__name__}; NOAA fallback will be used where possible")
        return False


def _latest_erddap_date(context: JobContext, dataset: str, source: str) -> dt.date:
    payload = get_json(context, f"{ERDDAP}/info/{dataset}/index.json", source=source)
    for row in payload["table"]["rows"]:
        if row[1] == "NC_GLOBAL" and row[2] == "time_coverage_end":
            return dt.datetime.strptime(row[4][:19], "%Y-%m-%dT%H:%M:%S").date()
    raise RuntimeError(f"NOAA {dataset} has no time coverage end")


def _erddap_download(context: JobContext, *, source: str, dataset: str, variable: str,
                     output: Path, altitude: bool = False) -> None:
    bbox = context.request.bbox
    end = min(context.request.end_date, _latest_erddap_date(context, dataset, source))
    start = context.request.start_date
    if start > end:
        raise RuntimeError("requested dates precede or exceed available observations")
    chunks = []
    intervals = bbox.intervals()
    for west, east in intervals:
        chunk_start = start
        while chunk_start <= end:
            context.check_cancelled()
            chunk_end = min(end, chunk_start + dt.timedelta(days=29))
            altitude_query = "[(0.0)]" if altitude else ""
            query = (f"{variable}[({chunk_start}T12:00:00Z):1:({chunk_end}T12:00:00Z)]"
                     f"{altitude_query}[({bbox.south}):({bbox.north})][({west}):({east})]")
            url = f"{ERDDAP}/griddap/{dataset}.nc?{query}"
            temporary = output.with_name(f".{output.stem}-{west}-{chunk_start}.nc")
            context.report("download", source, f"requesting {chunk_start}..{chunk_end} ({west}..{east})")
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            temporary.write_bytes(response.content)
            try:
                with xr.open_dataset(temporary) as dataset_value:
                    chunks.append(dataset_value.load())
            finally:
                temporary.unlink(missing_ok=True)
            chunk_start = chunk_end + dt.timedelta(days=1)
    if not chunks:
        raise RuntimeError("NOAA returned no observations")
    combined = xr.concat(chunks, dim="time").sortby("time")
    combined = combined.sel(time=~combined.get_index("time").duplicated())
    if source == "chlorophyll":
        combined["chlor_a"].attrs.update(units="mg m-3", source=f"NOAA CoastWatch {dataset}")
    else:
        combined = combined.rename({variable: "sst_kelvin"})
        combined["sst_kelvin"].attrs.update(units="kelvin", source=f"NOAA CoastWatch {dataset}")
    temporary = output.with_suffix(".tmp.nc")
    combined.to_netcdf(temporary)
    temporary.replace(output)
    context.status(source, "complete", "NOAA daily data downloaded", product=dataset,
                   actual_end=end.isoformat())


def fetch_chlorophyll(context: JobContext) -> None:
    primary = context.paths.raw / "chlorophyll.nc"
    has_primary = _copernicus_field(context, source="chlorophyll", variable="CHL",
                                    reprocessed=CHL_MY, nrt=CHL_NRT, output=primary)
    # Always acquire the global product when the primary is absent or only
    # covers part of the requested area. It is also useful for exact-date
    # supplementary pixels in the Mediterranean preset.
    try:
        _erddap_download(context, source="chlorophyll", dataset="noaacwNPPVIIRSSQchlaDaily",
                         variable="chlor_a", output=context.paths.raw / "chlorophyll_global_noaa.nc", altitude=True)
        if not has_primary:
            # The renderer accepts a NOAA-only primary file as well.
            source = context.paths.raw / "chlorophyll_global_noaa.nc"
            target = primary
            if not target.exists():
                target.write_bytes(source.read_bytes())
    except Exception as error:
        if not has_primary:
            context.status("chlorophyll", "unavailable", f"no chlorophyll source available: {type(error).__name__}: {error}")
        else:
            context.status("chlorophyll", "partial", "Copernicus available; NOAA supplement unavailable")


def fetch_sst(context: JobContext) -> None:
    primary = context.paths.raw / "sst.nc"
    has_primary = _copernicus_field(context, source="sst", variable="analysed_sst",
                                    reprocessed=SST_REP, nrt=SST_NRT, output=primary)
    if not has_primary:
        try:
            _erddap_download(context, source="sst", dataset="noaacwBLENDEDsstDaily",
                             variable="analysed_sst", output=primary)
        except Exception as error:
            context.status("sst", "unavailable", f"no SST source available: {type(error).__name__}: {error}")
