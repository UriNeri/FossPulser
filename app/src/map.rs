use crate::model::Manifest;

#[derive(Clone, Copy, Debug)]
pub struct ViewTransform {
    pub zoom: f32,
    pub pan: (f32, f32),
    pub viewport: (f32, f32),
}

pub fn visible_bbox(manifest: &Manifest, transform: ViewTransform) -> [f32; 4] {
    let [west, south, east, north] = manifest.map_bbox;
    let width = manifest.map_width * transform.zoom;
    let height = manifest.map_height * transform.zoom;
    let x0 = (-transform.pan.0 / width).clamp(0.0, 1.0);
    let x1 = ((transform.viewport.0 - transform.pan.0) / width).clamp(0.0, 1.0);
    let y0 = (-transform.pan.1 / height).clamp(0.0, 1.0);
    let y1 = ((transform.viewport.1 - transform.pan.1) / height).clamp(0.0, 1.0);
    [
        west + (east - west) * x0,
        north - (north - south) * y1,
        west + (east - west) * x1,
        north - (north - south) * y0,
    ]
}

#[allow(dead_code)]
pub fn format_bbox(bbox: [f32; 4]) -> String {
    format!(
        "W {:.4}  S {:.4}  E {:.4}  N {:.4}",
        bbox[0], bbox[1], bbox[2], bbox[3]
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Manifest;
    use std::collections::HashMap;

    fn manifest() -> Manifest {
        Manifest {
            schema_version: 2,
            bundle_id: None,
            map_width: 100.0,
            map_height: 100.0,
            map_bbox: [10.0, 20.0, 30.0, 40.0],
            global_context: String::new(),
            global_bbox: [-180.0, -90.0, 180.0, 90.0],
            global_width: 1440.0,
            global_height: 720.0,
            bathymetry_layer: None,
            lake_outlines: None,
            basemap: String::new(),
            rivers_layer: None,
            chlorophyll_frames: vec![],
            sst_frames: vec![],
            precipitation: None,
            places: vec![],
            facilities: vec![],
            source_status: HashMap::new(),
            taxonomy_demo: false,
        }
    }

    #[test]
    fn visible_area_is_clamped_to_map() {
        let result = visible_bbox(
            &manifest(),
            ViewTransform {
                zoom: 1.0,
                pan: (-10.0, -10.0),
                viewport: (50.0, 50.0),
            },
        );
        assert_eq!(result, [12.0, 28.0, 22.0, 38.0]);
    }

    #[test]
    fn bbox_format_is_readable() {
        assert!(format_bbox([1.0, 2.0, 3.0, 4.0]).contains("W 1.0000"));
    }
}
