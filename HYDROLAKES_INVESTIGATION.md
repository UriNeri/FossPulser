# Inland-lake data investigation

## Finding

HydroLAKES v1.0 is a suitable source for replacing the current approximate Sea of Galilee and Dead Sea outlines with surveyed lake polygons. It is a geometry and lake-attribute dataset, not a chlorophyll time series.

- Product: https://www.hydrosheds.org/products/hydrolakes
- Citation: Messager et al. (2016), *Estimating the volume and age of water stored in global lakes using a geo-statistical approach*, Nature Communications 7, 13603. https://doi.org/10.1038/ncomms13603
- License: Creative Commons Attribution 4.0 International.
- Coverage: global lakes and reservoirs of at least 10 ha, including shoreline polygons and pour points.
- Attributes: primarily geometry, surface area, shoreline length, estimated mean depth, volume, and residence time, plus HydroRIVERS/HydroBASINS identifiers.
- Chlorophyll: not provided by HydroLAKES.

The official polygon and point downloads were reachable on 2026-11-16. The polygon archive is approximately 820 MB and the point archive approximately 79 MB, so neither was downloaded during this investigation.

## Chlorophyll implications

The existing Copernicus Marine product is a Mediterranean marine product. It must not be assumed to provide valid observations for the inland Sea of Galilee or Dead Sea. The current inland outlines are visual context only; no inland chlorophyll values are displayed or inferred.

A future inland-water overlay should use a separately validated inland-water product and should mask the Sea of Galilee and Dead Sea independently. The Dead Sea's extreme salinity makes it a useful negative/control case, but generic marine chlorophyll algorithms are not scientifically transferable to either lake without validation.

## Recommended integration

1. Download HydroLAKES polygons outside the repository's generated-data tree.
2. Extract only features intersecting the FossPulser bounds and identify the Sea of Galilee and Dead Sea by name and location.
3. Save the two clipped polygons as a small curated GeoJSON file, retaining source identifiers and attribution.
4. Replace `INLAND_LAKES` in `pipeline/build_basemap.py` with those polygons.
5. Investigate an inland-water chlorophyll source separately; do not fill lake pixels from the marine overlay.

Until step 4 is completed, the two outlines in the basemap remain approximate, hand-authored polygons.
