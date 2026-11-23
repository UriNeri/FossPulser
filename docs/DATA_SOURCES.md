# Data sources, access, and limitations

FossPulser downloads data locally at the user’s request. This document is a sourcing guide, not a grant of redistribution rights. Provider terms can change; review the linked terms and product metadata before publishing derived work.

| Layer | Provider and product | Coverage / cadence | Access | Attribution and limitations |
|---|---|---|---|---|
| Terrain and bathymetry | [GMRT](https://www.gmrt.org/) GridServer API | Global merged terrain; requested resolution | No account | Use the GMRT citation and terms linked from the service. Coarse visual context, not navigational bathymetry. |
| Countries, places, lakes | [Natural Earth](https://www.naturalearthdata.com/), [terms](https://www.naturalearthdata.com/about/terms-of-use/) | Global static vectors | No account | Natural Earth public-domain data. Preserve source attribution in derived documentation. |
| Rivers | [HydroRIVERS v1.0](https://www.hydrosheds.org/products/hydrorivers), [HydroSHEDS license](https://www.hydrosheds.org/page/license) | Continental archives; static network | No account | `DIS_AV_CMS` is long-term-average discharge, not live flow. Regional archives are selected by drainage-region coverage. |
| Wastewater | [HydroWASTE paper](https://doi.org/10.5194/essd-14-559-2022), [Figshare record](https://doi.org/10.6084/m9.figshare.14847786.v1) | Global facility inventory | No account | Cite Macedo et al. (2022). Facility attributes and outfall details are not independently verified by FossPulser. |
| Mediterranean chlorophyll | [Copernicus Marine](https://marine.copernicus.eu/), [reprocessed product](https://data.marine.copernicus.eu/product/OCEANCOLOUR_MED_BGC_L4_MY_009_142) | Mediterranean; daily, 1 km gap-free | Free Copernicus account | Dataset IDs: `cmems_obs-oc_med_bgc-plankton_my_l4-gapfree-multi-1km_P1D` and `cmems_obs-oc_med_bgc-plankton_nrt_l4-gapfree-multi-1km_P1D`. Review [service commitments and licence](https://marine.copernicus.eu/user-corner/service-commitments-and-licence). |
| Global chlorophyll | [NOAA CoastWatch daily VIIRS](https://coastwatch.noaa.gov/erddap/griddap/noaacwNPPVIIRSSQchlaDaily.html) | Global ocean; daily, 4 km, latency applies | No account | Dataset ID: `noaacwNPPVIIRSSQchlaDaily`. Follow the [NOAA data policy and citation instructions](https://coastwatch.noaa.gov/erddap/information.html#citeDataset). Exact-date gaps remain transparent. |
| Mediterranean SST | [Copernicus Marine SST](https://data.marine.copernicus.eu/product/SST_MED_SST_L4_REP_OBSERVATIONS_010_021) | Mediterranean; daily | Free Copernicus account | Dataset IDs: `cmems_SST_MED_SST_L4_REP_OBSERVATIONS_010_021` and `SST_MED_SST_L4_NRT_OBSERVATIONS_010_004_a_V2`. |
| Global SST | [NOAA Geo-polar Blended GHRSST](https://coastwatch.noaa.gov/erddap/griddap/noaacwBLENDEDsstDaily.html) | Global ocean; daily, 5 km, 2002-present | No account | Dataset ID: `noaacwBLENDEDsstDaily`; variable `analysed_sst`, Kelvin. Night-only blended foundation SST; not an in-situ river measurement. |
| Precipitation | [Open-Meteo Historical Weather API](https://open-meteo.com/en/docs/historical-weather-api), [terms](https://open-meteo.com/en/terms) | Global point queries; daily ERA5-derived values | No account for normal use | FossPulser uses a coarse point grid, not a full high-resolution precipitation raster. Respect API limits and cite ERA5/Open-Meteo as requested. |

## Coverage behavior

- Copernicus Mediterranean products are requested only when the requested area intersects their coverage. They are not silently clamped into a different region.
- NOAA daily chlorophyll and blended SST provide global ocean fallbacks, subject to their actual temporal and spatial coverage.
- No source is treated as a validated inland-water chlorophyll measurement merely because a satellite grid intersects a lake or river polygon.
- Missing dates, unavailable credentials, out-of-coverage products, and failed independent sources are reported in the manifest and UI.
- A monthly product is never silently substituted for a requested daily series.

## Local storage and redistribution

Raw downloads, clipped vectors, rendered rasters, and manifests are local user data. They are excluded from Git and should not be uploaded as release assets unless the relevant provider terms explicitly permit that distribution and required notices are included. The repository’s MIT license applies to project code, not to third-party observations or derived provider data.
