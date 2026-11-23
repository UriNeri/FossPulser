use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Deserialize, Clone, Debug)]
pub struct RasterFrame {
    #[allow(dead_code)]
    pub index: usize,
    pub date: String,
    pub mean: Option<f64>,
    #[serde(default)]
    pub noaa_date: Option<String>,
    #[allow(dead_code)]
    pub min: Option<f64>,
    #[allow(dead_code)]
    pub max: Option<f64>,
    pub path: String,
}

#[derive(Deserialize, Clone, Debug)]
pub struct PlaceLabel {
    pub name: String,
    pub x: f32,
    pub y: f32,
    #[allow(dead_code)]
    pub pop_max: f64,
}

#[derive(Deserialize, Clone, Debug)]
pub struct FacilityPin {
    pub name: String,
    pub kind: String,
    pub outfall: String,
    pub note: String,
    pub country: Option<String>,
    pub design_capacity_m3_day: Option<f64>,
    pub discharge_m3_day: Option<f64>,
    pub population_served: Option<f64>,
    pub treatment_level: Option<String>,
    pub source: Option<String>,
    pub x: f32,
    pub y: f32,
}

#[derive(Deserialize, Clone, Debug)]
pub struct PrecipData {
    #[allow(dead_code)]
    pub dates: Vec<String>,
    pub points: Vec<[f32; 2]>,
    pub by_day: Vec<Vec<f32>>,
}

#[derive(Deserialize, Clone, Debug, Default)]
pub struct SourceStatus {
    pub status: String,
    #[allow(dead_code)]
    #[serde(default)]
    pub message: String,
}

#[derive(Deserialize, Clone, Debug)]
pub struct Manifest {
    #[allow(dead_code)]
    #[serde(default)]
    pub schema_version: u32,
    #[allow(dead_code)]
    #[serde(default)]
    pub bundle_id: Option<String>,
    pub map_width: f32,
    pub map_height: f32,
    pub map_bbox: [f32; 4],
    pub global_context: String,
    pub global_bbox: [f32; 4],
    pub global_width: f32,
    pub global_height: f32,
    #[serde(default)]
    pub bathymetry_layer: Option<String>,
    #[serde(default)]
    pub lake_outlines: Option<String>,
    pub basemap: String,
    #[serde(default)]
    pub rivers_layer: Option<String>,
    #[serde(default)]
    pub chlorophyll_frames: Vec<RasterFrame>,
    #[serde(default)]
    pub sst_frames: Vec<RasterFrame>,
    #[serde(default)]
    pub precipitation: Option<PrecipData>,
    #[serde(default)]
    pub places: Vec<PlaceLabel>,
    #[serde(default)]
    pub facilities: Vec<FacilityPin>,
    #[serde(default)]
    pub source_status: HashMap<String, SourceStatus>,
    #[serde(default)]
    pub taxonomy_demo: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct FetchRequest {
    pub name: String,
    pub bbox: [f32; 4],
    pub start_date: String,
    pub end_date: Option<String>,
    pub width: u32,
    pub sources: Vec<String>,
    pub preset: Option<String>,
}

impl Default for FetchRequest {
    fn default() -> Self {
        Self {
            name: "custom-area".into(),
            bbox: [29.0, 29.3, 36.0, 33.4],
            start_date: "2022-01-01".into(),
            end_date: None,
            width: 1200,
            sources: [
                "bathymetry",
                "boundaries",
                "rivers",
                "facilities",
                "chlorophyll",
                "sst",
                "precipitation",
            ]
            .into_iter()
            .map(str::to_owned)
            .collect(),
            preset: None,
        }
    }
}

#[derive(Serialize, Clone, Debug)]
pub struct Credentials {
    pub username: String,
    pub password: String,
}

#[derive(Serialize, Clone, Debug)]
pub struct CredentialEnvelope {
    pub copernicus: Credentials,
}

#[derive(Serialize, Clone, Debug)]
pub struct WorkerEnvelope {
    pub request: FetchRequest,
    pub data_root: String,
    pub build: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub credentials: Option<CredentialEnvelope>,
}

#[derive(Deserialize, Clone, Debug)]
pub struct WorkerEvent {
    #[allow(dead_code)]
    pub request_id: Option<String>,
    pub stage: String,
    pub status: String,
    #[allow(dead_code)]
    pub source: Option<String>,
    pub completed: Option<u64>,
    pub total: Option<u64>,
    pub bytes_completed: Option<u64>,
    pub bytes_total: Option<u64>,
    pub message: Option<String>,
    #[allow(dead_code)]
    pub warning: Option<String>,
    pub manifest: Option<String>,
}
