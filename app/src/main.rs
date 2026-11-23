//! FossPulser: an interactive day-by-day map of rivers and coastal observations,
//! built on the Rune UI/animation framework and fed by a local Python data
//! acquisition and rendering pipeline.
//!
//! `cargo run` from this directory, or `pixi run app-run` from the repo root.

mod assets;
mod map;
mod model;
mod worker;

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use assets::Bundle;
use model::{
    CredentialEnvelope, Credentials, FacilityPin, FetchRequest, Manifest, RasterFrame,
    WorkerEnvelope, WorkerEvent,
};
use rune::color::hex;
use rune::tok::type_;
use rune::{
    canvas, col, el, rich, row, text, text_input, AppSpec, Color, Cursor, Cx, El,
    FileDialogOptions, FileDialogResult, Image, ImageCache, Link, LinkPaint, Phase, ScrollSpec,
    Shape, Span, SurfaceFit, TextEdit, Tx, Waker, WheelEvent,
};

// ---------------------------------------------------------------------------
// App state
// ---------------------------------------------------------------------------

/// Frames within this many days are requested immediately (smooth scrub);
/// the rest of the LRU cache (`FRAME_CACHE_CAP`) just accumulates whatever's
/// been visited so scrubbing back over recent ground doesn't re-decode.
const FRAME_WINDOW: usize = 2;
/// Total decoded frames (per overlay) to keep resident before evicting the
/// least-recently-used one. Generous on purpose: unlike a hard ± window,
/// this never throws away a frame that's still mid-decode just because the
/// day moved on, which was starving the decoder during fast scrubbing/play.
const FRAME_CACHE_CAP: usize = 48;

/// Which daily raster overlay is currently drawn on the map.
#[derive(Clone, Copy, PartialEq)]
enum OverlayLayer {
    Chlorophyll,
    Sst,
}

/// Decoded-image LRU cache for one daily raster series (chlorophyll or
/// SST): requesting a frame never evicts another one just because the
/// window moved past it, only once the whole cache exceeds its cap -- the
/// fix for chlorophyll frames never finishing decode during fast
/// scrubbing/playback.
#[derive(Default)]
struct FrameCache {
    images: HashMap<usize, Image>,
    access_order: Vec<usize>, // most-recently-used last; drives LRU eviction
}

impl FrameCache {
    /// Ensures frame `i` is requested (a no-op if already cached) and marks
    /// it most-recently-used.
    fn touch(
        &mut self,
        cache: &ImageCache,
        frames: &[RasterFrame],
        frames_dir: &Path,
        map_width: f32,
        i: usize,
    ) {
        if !self.images.contains_key(&i) {
            let Some(frame) = frames.get(i) else { return };
            let path = frames_dir.join(&frame.path);
            let image = cache.file(path, map_width, 2.0);
            self.images.insert(i, image);
        }
        self.access_order.retain(|&k| k != i);
        self.access_order.push(i);
    }

    fn evict_lru(&mut self, cap: usize) {
        while self.access_order.len() > cap {
            let evict = self.access_order.remove(0);
            self.images.remove(&evict);
        }
    }

    fn ready(&self, i: usize) -> Option<&Image> {
        self.images
            .get(&i)
            .filter(|img| img.is_ready() && img.error().is_none())
    }
}

struct State {
    manifest: Manifest,
    frames_dir: PathBuf,
    data_root: PathBuf,
    bundle_id: Option<String>,
    has_bundle: bool,
    world_mode: bool,
    worker_path: Option<PathBuf>,
    worker_job: Option<worker::WorkerJob>,
    worker_events: Vec<WorkerEvent>,
    worker_message: String,
    fetch_request: FetchRequest,
    fetch_west: TextEdit,
    fetch_south: TextEdit,
    fetch_east: TextEdit,
    fetch_north: TextEdit,
    fetch_start: TextEdit,
    fetch_end: TextEdit,
    copernicus_username: TextEdit,
    copernicus_password: TextEdit,

    cache: Option<ImageCache>,
    global_context: Option<Image>,
    bathymetry: Option<Image>,
    lake_outlines: Option<Image>,
    basemap: Option<Image>,
    rivers: Option<Image>,
    chl_cache: FrameCache,
    sst_cache: FrameCache,
    /// Maps an SST frame's date to its index: chlorophyll and SST are
    /// fetched independently (each anchored to "today" when its own
    /// fetch_*.py ran), so their date ranges can be offset by a day or two
    /// -- looking up by date keeps the two overlays showing the same date
    /// even when their indices don't line up.
    sst_by_date: HashMap<String, usize>,
    /// Same idea as `sst_by_date`, for `Manifest::precipitation`'s own
    /// independently-fetched date range.
    precip_by_date: HashMap<String, usize>,

    day_index: usize,
    playing: bool,
    days_per_sec: f32,
    speed_edit: TextEdit,
    play_accum_days: f64,
    color_intensity: f32,
    overlay_layer: OverlayLayer,
    show_rivers: bool,
    show_precipitation: bool,
    show_desalination: bool,
    show_wwtp: bool,

    zoom: f32,
    pan: (f32, f32),
    drag_pan_start: (f32, f32),
    viewport_size: (f32, f32),
    map_fit_pending: bool,
    taxonomy_width: f32,
    right_panel_width: f32,
    /// Whether the data/fetch side panel is expanded (false = collapsed rail).
    side_panel_open: bool,
    /// Previous progress sample (stage, bytes) with the app time it was seen,
    /// used to compute a transfer/download speed for the fetch panel.
    progress_sample: Option<(String, u64, Instant)>,
    progress_speed: Option<f64>,

    hovered_facility: Option<usize>,
    show_help: bool,
    show_taxonomy: bool,

    last_poll_t: f64,
}

fn empty_manifest() -> Manifest {
    Manifest {
        schema_version: 2,
        bundle_id: None,
        map_width: 1440.0,
        map_height: 720.0,
        map_bbox: [-180.0, -90.0, 180.0, 90.0],
        global_context: String::new(),
        global_bbox: [-180.0, -90.0, 180.0, 90.0],
        global_width: 1440.0,
        global_height: 720.0,
        bathymetry_layer: None,
        lake_outlines: None,
        basemap: String::new(),
        rivers_layer: None,
        chlorophyll_frames: Vec::new(),
        sst_frames: Vec::new(),
        precipitation: None,
        places: Vec::new(),
        facilities: Vec::new(),
        source_status: HashMap::new(),
        taxonomy_demo: false,
    }
}

fn init() -> State {
    let data_root = assets::data_root();
    let bundle = assets::load_active(&data_root);
    let (manifest, frames_dir, bundle_id, has_bundle) = match bundle {
        Some(bundle) => (bundle.manifest, bundle.frames_dir, Some(bundle.id), true),
        None => (empty_manifest(), data_root.clone(), None, false),
    };
    let day_index = manifest.chlorophyll_frames.len().saturating_sub(1);
    let sst_by_date = manifest
        .sst_frames
        .iter()
        .enumerate()
        .map(|(i, f)| (f.date.clone(), i))
        .collect();
    let precip_by_date = manifest
        .precipitation
        .as_ref()
        .map(|p| {
            p.dates
                .iter()
                .enumerate()
                .map(|(i, d)| (d.clone(), i))
                .collect()
        })
        .unwrap_or_default();
    let request = FetchRequest::default();
    let taxonomy_demo = manifest.taxonomy_demo;
    State {
        manifest,
        frames_dir,
        data_root,
        bundle_id,
        has_bundle,
        world_mode: !has_bundle,
        worker_path: worker::discover_worker(),
        worker_job: None,
        worker_events: Vec::new(),
        worker_message: if has_bundle {
            "Local bundle loaded".into()
        } else {
            "No local data bundle. Fetch an area to begin.".into()
        },
        fetch_west: TextEdit::new("29"),
        fetch_south: TextEdit::new("29.3"),
        fetch_east: TextEdit::new("36"),
        fetch_north: TextEdit::new("33.4"),
        fetch_start: TextEdit::new("2022-01-01"),
        fetch_end: TextEdit::new(""),
        copernicus_username: TextEdit::new(""),
        copernicus_password: TextEdit::new(""),
        fetch_request: request,
        cache: None,
        global_context: None,
        bathymetry: None,
        lake_outlines: None,
        basemap: None,
        rivers: None,
        chl_cache: FrameCache::default(),
        sst_cache: FrameCache::default(),
        sst_by_date,
        precip_by_date,
        day_index,
        playing: false,
        days_per_sec: 5.0,
        speed_edit: TextEdit::new("5"),
        play_accum_days: 0.0,
        color_intensity: 1.0,
        overlay_layer: OverlayLayer::Chlorophyll,
        show_rivers: true,
        show_precipitation: true,
        show_desalination: true,
        show_wwtp: true,
        zoom: 1.0,
        pan: (0.0, 0.0),
        drag_pan_start: (0.0, 0.0),
        viewport_size: (VIEWPORT_W, VIEWPORT_H),
        map_fit_pending: true,
        taxonomy_width: 300.0,
        right_panel_width: 240.0,
        side_panel_open: !has_bundle,
        hovered_facility: None,
        show_help: false,
        show_taxonomy: taxonomy_demo,
        last_poll_t: 0.0,
        progress_sample: None,
        progress_speed: None,
    }
}

fn activate_bundle(s: &mut State, bundle: Bundle) {
    s.manifest = bundle.manifest;
    s.frames_dir = bundle.frames_dir;
    s.bundle_id = Some(bundle.id);
    s.has_bundle = true;
    s.world_mode = false;
    s.day_index = s.manifest.chlorophyll_frames.len().saturating_sub(1);
    s.sst_by_date = s
        .manifest
        .sst_frames
        .iter()
        .enumerate()
        .map(|(i, f)| (f.date.clone(), i))
        .collect();
    s.precip_by_date = s
        .manifest
        .precipitation
        .as_ref()
        .map(|p| {
            p.dates
                .iter()
                .enumerate()
                .map(|(i, d)| (d.clone(), i))
                .collect()
        })
        .unwrap_or_default();
    s.chl_cache = FrameCache::default();
    s.sst_cache = FrameCache::default();
    s.global_context = None;
    s.bathymetry = None;
    s.lake_outlines = None;
    s.basemap = None;
    s.rivers = None;
    s.map_fit_pending = true;
    s.show_taxonomy = s.manifest.taxonomy_demo;
    if s.cache.is_some() {
        setup_images(s);
    }
}

/// Whole-world backdrop embedded in the binary, shown when no bundle provides
/// its own global context (i.e. before any data has been fetched).
const WORLD_CONTEXT_PNG: &[u8] = include_bytes!("../assets/world_context.png");

fn setup_images(s: &mut State) {
    let Some(cache) = s.cache.as_ref() else {
        return;
    };
    let w = s.manifest.map_width;
    if !s.manifest.global_context.is_empty() {
        s.global_context = Some(cache.file(
            s.frames_dir.join(&s.manifest.global_context),
            s.manifest.global_width,
            1.0,
        ));
    } else {
        s.global_context = Some(cache.load(WORLD_CONTEXT_PNG));
    }
    s.bathymetry = s
        .manifest
        .bathymetry_layer
        .as_ref()
        .map(|path| cache.file(s.frames_dir.join(path), w, 2.0));
    s.lake_outlines = s
        .manifest
        .lake_outlines
        .as_ref()
        .map(|path| cache.file(s.frames_dir.join(path), w, 2.0));
    if !s.manifest.basemap.is_empty() {
        s.basemap = Some(cache.file(s.frames_dir.join(&s.manifest.basemap), w, 2.0));
    }
    s.rivers = s
        .manifest
        .rivers_layer
        .as_ref()
        .map(|path| cache.file(s.frames_dir.join(path), w, 2.0));
    ensure_overlay_window(s, s.day_index);
}

fn start_fetch(s: &mut State) {
    if s.worker_job.is_some() {
        return;
    }
    let parse = |edit: &TextEdit| edit.text.trim().parse::<f32>().ok();
    let (Some(west), Some(south), Some(east), Some(north)) = (
        parse(&s.fetch_west),
        parse(&s.fetch_south),
        parse(&s.fetch_east),
        parse(&s.fetch_north),
    ) else {
        s.worker_message = "Enter numeric west, south, east, and north bounds.".into();
        return;
    };
    let mut request = s.fetch_request.clone();
    request.bbox = [west, south, east, north];
    request.start_date = s.fetch_start.text.trim().to_owned();
    request.end_date = if s.fetch_end.text.trim().is_empty() {
        None
    } else {
        Some(s.fetch_end.text.trim().to_owned())
    };
    let credentials =
        if !s.copernicus_username.text.is_empty() && !s.copernicus_password.text.is_empty() {
            Some(CredentialEnvelope {
                copernicus: Credentials {
                    username: s.copernicus_username.text.clone(),
                    password: s.copernicus_password.text.clone(),
                },
            })
        } else {
            None
        };
    let envelope = WorkerEnvelope {
        request,
        data_root: s.data_root.to_string_lossy().into_owned(),
        build: true,
        credentials,
    };
    let Some(path) = s.worker_path.clone() else {
        s.worker_message =
            "Pixi Python worker not found. Set FOSSPULSER_WORKER or run pixi install.".into();
        return;
    };
    match worker::start(envelope, &path) {
        Ok(job) => {
            s.worker_job = Some(job);
            s.worker_message = "Acquisition started".into();
            s.worker_events.clear();
            // The worker has received the envelope; do not retain secrets in
            // the GUI state after the handoff.
            s.copernicus_password.replace_text("");
        }
        Err(error) => s.worker_message = format!("Could not start worker: {error}"),
    }
}

/// Human-readable description for the worker's stage identifiers, so the
/// progress panel says what is actually happening rather than a raw token.
fn stage_description(stage: &str) -> &str {
    match stage {
        "chlorophyll" => "Chlorophyll observations",
        "sst" => "Sea-surface temperature",
        "precipitation" => "Rainfall (Open-Meteo)",
        "rivers" => "River network (HydroRIVERS)",
        "terrain" | "bathymetry" => "Terrain / bathymetry (GMRT)",
        "boundaries" => "Country boundaries (Natural Earth)",
        "facilities" => "Wastewater facilities (HydroWASTE)",
        "lakes" => "Lakes (Natural Earth)",
        "global-context" => "World context layer",
        "basemap" => "Basemap rendering",
        "bathymetry-layer" => "Depth contours",
        "rivers-layer" => "River layer rendering",
        "chlorophyll-layer" => "Chlorophyll layer rendering",
        "sst-layer" => "SST layer rendering",
        "precipitation-layer" => "Rain layer rendering",
        "labels" => "Labels and pins",
        "manifest" => "Assembling manifest",
        "build" => "Building bundle",
        "fetch" => "Fetching data",
        "complete" => "Finished",
        other => other,
    }
}

/// Updates a smoothed download/processing speed estimate from consecutive
/// progress events of the same stage. Bytes-based when available.
fn track_progress_speed(s: &mut State, event: &WorkerEvent) {
    let progress_bytes = match (event.bytes_completed, event.bytes_total) {
        (Some(done), Some(total)) if total > 0 => Some(done),
        _ => None,
    };
    // Item counts are too coarse for a meaningful rate; track bytes only.
    let Some(bytes) = progress_bytes else {
        s.progress_sample = None;
        s.progress_speed = None;
        return;
    };
    let now = Instant::now();
    if let Some((stage, prev_bytes, prev_time)) = &s.progress_sample {
        if *stage == event.stage {
            let dt = now.duration_since(*prev_time).as_secs_f64();
            if dt >= 0.25 {
                let rate = (bytes.saturating_sub(*prev_bytes)) as f64 / dt;
                s.progress_speed = Some(if let Some(prev) = s.progress_speed {
                    prev * 0.6 + rate * 0.4
                } else {
                    rate
                });
                s.progress_sample = Some((event.stage.clone(), bytes, now));
            }
            return;
        }
    }
    s.progress_sample = Some((event.stage.clone(), bytes, now));
}

/// Formats a byte count for the progress line.
fn format_bytes(bytes: u64) -> String {
    if bytes >= 1024 * 1024 * 1024 {
        format!("{:.1} GB", bytes as f64 / (1024.0 * 1024.0 * 1024.0))
    } else if bytes >= 1024 * 1024 {
        format!("{:.1} MB", bytes as f64 / (1024.0 * 1024.0))
    } else if bytes >= 1024 {
        format!("{:.0} KB", bytes as f64 / 1024.0)
    } else {
        format!("{bytes} B")
    }
}

fn poll_worker(s: &mut State) -> bool {
    let mut changed = false;
    let mut completed = None;
    let mut failed = None;
    let mut events = Vec::new();
    if let Some(job) = s.worker_job.as_mut() {
        while let Ok(event) = job.events.try_recv() {
            events.push(event);
        }
        if completed.is_none() && failed.is_none() {
            if let Ok(Some(status)) = job.try_finished() {
                if !status.success() {
                    failed = Some(format!("Worker exited with status {status}"));
                }
            }
        }
    }
    for event in &events {
        track_progress_speed(s, event);
        if let Some(message) = &event.message {
            s.worker_message = message.clone();
        }
        if (event.status == "complete" || event.status == "partial") && event.stage == "complete" {
            completed = event.manifest.clone();
        }
        if event.status == "failed" || event.status == "cancelled" {
            failed = event
                .message
                .clone()
                .or_else(|| Some("Worker stopped".into()));
        }
        changed = true;
    }
    s.worker_events.extend(events);
    if let Some(message) = failed {
        s.worker_job = None;
        s.worker_message = message;
        changed = true;
    }
    if let Some(path) = completed {
        s.worker_job = None;
        if let Some(bundle) = assets::load_manifest_path(&s.data_root, Path::new(&path)) {
            activate_bundle(s, bundle);
        }
        s.worker_message = "Data bundle activated".into();
        changed = true;
    }
    changed
}

/// The SST frame index showing the same date as the current chlorophyll day
/// (see `State::sst_by_date`); `None` if SST has no data for that date.
fn sst_index_for_day(s: &State, day_index: usize) -> Option<usize> {
    let date = &s.manifest.chlorophyll_frames.get(day_index)?.date;
    s.sst_by_date.get(date).copied()
}

fn setup(s: &mut State, waker: Waker) {
    s.cache = Some(ImageCache::new(waker));
    setup_images(s);
}

/// Keep only the frames near `center` decoded for whichever overlay is
/// active; load any missing ones in the window from disk (async: they draw
/// in once the cache's worker finishes).
fn ensure_overlay_window(s: &mut State, center: usize) {
    // Move the cache out so we can hold an immutable borrow of it alongside
    // mutable access to the rest of `s` (ImageCache doesn't implement Clone).
    let Some(cache) = s.cache.take() else { return };
    let map_width = s.manifest.map_width;

    match s.overlay_layer {
        OverlayLayer::Chlorophyll => {
            let lo = center.saturating_sub(FRAME_WINDOW);
            let hi =
                (center + FRAME_WINDOW).min(s.manifest.chlorophyll_frames.len().saturating_sub(1));
            for i in std::iter::once(center).chain(lo..=hi) {
                s.chl_cache.touch(
                    &cache,
                    &s.manifest.chlorophyll_frames,
                    &s.frames_dir,
                    map_width,
                    i,
                );
            }
            s.chl_cache.evict_lru(FRAME_CACHE_CAP);
        }
        OverlayLayer::Sst => {
            if let Some(sst_center) = sst_index_for_day(s, center) {
                let lo = sst_center.saturating_sub(FRAME_WINDOW);
                let hi =
                    (sst_center + FRAME_WINDOW).min(s.manifest.sst_frames.len().saturating_sub(1));
                for i in std::iter::once(sst_center).chain(lo..=hi) {
                    s.sst_cache
                        .touch(&cache, &s.manifest.sst_frames, &s.frames_dir, map_width, i);
                }
                s.sst_cache.evict_lru(FRAME_CACHE_CAP);
            }
        }
    }
    s.cache = Some(cache);
}

fn set_day(s: &mut State, index: usize) {
    let max = s.manifest.chlorophyll_frames.len().saturating_sub(1);
    s.day_index = index.min(max);
    ensure_overlay_window(s, s.day_index);
}

/// Ticks playback forward in real time; returns whether the day changed so
/// the view re-runs. Rune calls this once per frame with elapsed app time.
///
/// Steps by whole days only, accumulating fractional progress across calls
/// (rather than re-deriving it from a single frame's `dt`), so playback
/// speed is correct regardless of frame rate and the chlorophyll image
/// cache gets a real chance to decode each frame before it's evicted --
/// advancing a full day on nearly every 60 fps tick (as a per-tick-only
/// check would) starves the async image decode and the overlay never
/// visibly updates.
fn poll(s: &mut State, t: f64, tx: &mut Tx) -> bool {
    let worker_changed = poll_worker(s);
    if s.worker_job.is_some() {
        tx.wake_after(Duration::from_millis(100));
    }
    let dt = (t - s.last_poll_t).max(0.0);
    s.last_poll_t = t;
    // Keep ticking (and redrawing) whenever the precipitation overlay or
    // the rivers' pulse animation is on, even while paused.
    let animating_precip = s.show_precipitation && s.manifest.precipitation.is_some();
    let animating_rivers = s.show_rivers;
    let animating = animating_precip || animating_rivers;
    if !s.playing || s.days_per_sec <= 0.0 {
        if worker_changed {
            return true;
        }
        if animating {
            tx.wake_after(Duration::from_millis(33));
            return true;
        }
        return false;
    }
    tx.wake_after(Duration::from_millis(16));
    s.play_accum_days += s.days_per_sec as f64 * dt;
    let step = s.play_accum_days.floor();
    if step < 1.0 {
        return animating;
    }
    s.play_accum_days -= step;
    let max = s.manifest.chlorophyll_frames.len().saturating_sub(1);
    let next = s.day_index + step as usize;
    set_day(s, if next > max { 0 } else { next }); // loop back to the start
    true
}

// ---------------------------------------------------------------------------
// Colours / palette
// ---------------------------------------------------------------------------

fn c(hexstr: &str) -> Color {
    hex(hexstr).unwrap_or(Color {
        r: 1.0,
        g: 0.0,
        b: 1.0,
        a: 1.0,
    })
}

const BG: &str = "#0b0d13";
const PANEL_BG: &str = "#14182499";
const PANEL_BORDER: &str = "#ffffff14";
const TEXT_PRIMARY: &str = "#eef1f6";
const TEXT_MUTED: &str = "#8b93a7";
const ACCENT: &str = "#4cc9b0";
const SURFACE: &str = "#101320";
const BTN_BG: &str = "#1c2233";
const PANEL_BG_SOLID: &str = "#131726";
const SECTION_MUTED: &str = "#6f7890";

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------

const VIEWPORT_W: f32 = 760.0;
const VIEWPORT_H: f32 = 860.0;

fn view(s: &State, _cx: &mut Cx<State>) -> El {
    col()
        .w_full()
        .h_full()
        .fill(c(BG))
        .child(header(s))
        .child(
            row()
                .w_full()
                .grow(1.0)
                .min_h(0.0)
                .child(side_panel_toggle(s))
                // The synthetic taxonomy demonstration is disabled. Keep its
                // model/rendering code for a future data-backed implementation.
                .child(map_viewport(s))
                .child(panel_resize_handle(PanelSide::Right))
                .child(right_panel(s)),
        )
        .child(control_bar(s))
        .child(footer(s))
}

fn footer(_s: &State) -> El {
    let sources = [
        ("Copernicus Marine", "https://marine.copernicus.eu/"),
        (
            "NOAA CoastWatch",
            "https://coastwatch.noaa.gov/erddap/griddap/noaacwNPPVIIRSSQchlaDaily.html",
        ),
        (
            "HydroRIVERS",
            "https://www.hydrosheds.org/products/hydrorivers",
        ),
        ("Natural Earth", "https://www.naturalearthdata.com/"),
        ("Open-Meteo", "https://open-meteo.com/"),
        ("HydroWASTE", "https://doi.org/10.5194/essd-14-559-2022"),
    ];
    let mut attribution = String::from("Data: ");
    let mut spans = Vec::new();
    let mut links = Vec::new();
    for (i, (name, url)) in sources.iter().enumerate() {
        if i > 0 {
            attribution.push_str(" · ");
        }
        let start = attribution.len();
        attribution.push_str(name);
        spans.push(Span::new(start, attribution.len()).color(c("#8fc9ef")));
        links.push(
            Link::new(start..attribution.len(), *url).hover(
                LinkPaint::default()
                    .color(c("#c4e7ff"))
                    .underline(c("#c4e7ff")),
            ),
        );
    }
    let mut source_text = rich(attribution, spans)
        .font(type_::LABEL)
        .color(c(TEXT_MUTED))
        .selectable(true);
    for link in links {
        source_text = source_text.link(link);
    }

    col()
        .w_full()
        .padding(8.0)
        .gap(2.0)
        .fill(c("#0a0b12"))
        .child(
            source_text.on_link(|_: &mut State, url: &str, tx: &mut Tx| {
                let _ = tx.open_url(url);
            }),
        )
        .child(
            text("FossPulser — Eastern Mediterranean river & coastal monitoring")
                .font(type_::LABEL)
                .color(c("#5a5a6a"))
                .selectable(true),
        )
}

fn header(s: &State) -> El {
    col()
        .w_full()
        .padding(16.0)
        .gap(2.0)
        .child(
            text(if s.has_bundle {
                "LOCAL RESEARCH BUNDLE"
            } else {
                "FOSSPULSER DATA EXPLORER"
            })
            .font(type_::LABEL)
            .color(c(ACCENT)),
        )
        .child(
            text("FossPulser")
                .font(type_::TITLE)
                .color(c(TEXT_PRIMARY)),
        )
        .child(
            text("Rivers and coastal chlorophyll, day by day")
                .font(type_::BODY)
                .color(c(TEXT_MUTED)),
        )
}

fn map_viewport(s: &State) -> El {
    let (map_width, map_height) = if s.world_mode {
        (s.manifest.global_width, s.manifest.global_height)
    } else {
        (s.manifest.map_width, s.manifest.map_height)
    };
    let w = map_width * s.zoom;
    let h = map_height * s.zoom;
    let mut layers = el().absolute(s.pan.0, s.pan.1).w(w).h(h);
    if let Some(global) = &s.global_context {
        let (global_x, global_y, global_w, global_h) = global_geometry(s);
        layers = layers.child(
            el().absolute(global_x, global_y)
                .w(global_w)
                .h(global_h)
                .pixels_fit(global.pixels(), SurfaceFit::Fill),
        );
    }
    if !s.world_mode {
        if let Some(basemap) = &s.basemap {
            layers = layers.child(
                el().w(w)
                    .h(h)
                    .pixels_fit(basemap.pixels(), SurfaceFit::Fill),
            );
        }
        // Only the selected day's decoded image may be displayed. A missing or
        // still-decoding observation stays transparent rather than borrowing a
        // neighbouring date and contradicting the date label.
        let overlay_img = match s.overlay_layer {
            OverlayLayer::Chlorophyll => s.chl_cache.ready(s.day_index),
            OverlayLayer::Sst => {
                sst_index_for_day(s, s.day_index).and_then(|i| s.sst_cache.ready(i))
            }
        };
        if let Some(img) = overlay_img {
            layers = layers.child(
                el().absolute(0.0, 0.0)
                    .w(w)
                    .h(h)
                    .opacity(s.color_intensity)
                    .pixels_fit(img.pixels(), SurfaceFit::Fill),
            );
        }
        if let Some(bathymetry) = &s.bathymetry {
            layers = layers.child(
                el().absolute(0.0, 0.0)
                    .w(w)
                    .h(h)
                    .pixels_fit(bathymetry.pixels(), SurfaceFit::Fill),
            );
        }
        if let Some(lake_outlines) = &s.lake_outlines {
            layers = layers.child(
                el().absolute(0.0, 0.0)
                    .w(w)
                    .h(h)
                    .pixels_fit(lake_outlines.pixels(), SurfaceFit::Fill),
            );
        }
        if s.show_rivers {
            if let Some(rivers) = &s.rivers {
                layers = layers.child(
                    el().absolute(0.0, 0.0)
                        .w(w)
                        .h(h)
                        .opacity(rivers_pulse_opacity(s))
                        .pixels_fit(rivers.pixels(), SurfaceFit::Fill),
                );
            }
        }
        if s.show_precipitation {
            layers = layers.child(precipitation_overlay(s));
        }

        // City labels and facility pins ride the same pan/zoom transform so they
        // stay glued to the map underneath them.
        for place in &s.manifest.places {
            let (x, y) = (place.x * s.zoom, place.y * s.zoom);
            layers = layers.child(
                col()
                    .absolute(x, y)
                    .gap(2.0)
                    .child(el().size(3.0, 3.0).radius(1.5).fill(c("#ffffffcc")))
                    .child(text(&place.name).font(type_::LABEL).color(c("#e8e8f0cc"))),
            );
        }
        for (idx, facility) in s.manifest.facilities.iter().enumerate() {
            let shown = if facility.kind == "desalination" {
                s.show_desalination
            } else {
                s.show_wwtp
            };
            if !shown {
                continue;
            }
            let (x, y) = (facility.x * s.zoom, facility.y * s.zoom);
            let pin_color = if facility.kind == "desalination" {
                "#5fb8ff"
            } else {
                "#ffb15f"
            };
            layers = layers.child(
                el().key(&facility.name)
                    .absolute(x - 5.0, y - 5.0)
                    .size(10.0, 10.0)
                    .radius(5.0)
                    .fill(c(pin_color))
                    .border(1.0, c("#00000080"))
                    .label(&facility.name)
                    .focusable(true)
                    .on_hover_change::<State>(move |s, inside, _tx| {
                        s.hovered_facility = if inside { Some(idx) } else { None };
                    }),
            );
            if s.hovered_facility == Some(idx) {
                layers = layers.child(facility_tooltip(facility, x, y));
            }
        }
    }
    el().key("viewport")
        .grow(1.0)
        .min_w(0.0)
        .min_h(0.0)
        .radius(12.0)
        .clip_rounded(12.0)
        .fill(c("#05060a"))
        .border(1.0, c(PANEL_BORDER))
        .on_layout(|s: &mut State, rect| {
            let size = (rect[2].max(1.0), rect[3].max(1.0));
            let changed = (s.viewport_size.0 - size.0).abs() > 0.5
                || (s.viewport_size.1 - size.1).abs() > 0.5;
            s.viewport_size = size;
            if s.map_fit_pending || changed {
                reset_map(s);
            }
        })
        .on_drag::<State>(|s, drag, _tx| match drag.phase {
            Phase::Down => s.drag_pan_start = s.pan,
            Phase::Move => {
                s.pan = (s.drag_pan_start.0 + drag.dx, s.drag_pan_start.1 + drag.dy);
                clamp_pan(s);
            }
            Phase::Up => {}
        })
        .on_wheel::<State>(|s, event: WheelEvent, _tx| {
            zoom_at(s, event.dy * 0.0015, event.x, event.y);
            true
        })
        .child(layers)
        .child(help_overlay(s))
}

/// A gentle breathing opacity for the rivers glow layer, driven by wall-clock
/// time rather than the day
/// index -- it's a styling animation, not a second live data channel
/// (HydroRIVERS is a static long-term-average network).
fn rivers_pulse_opacity(s: &State) -> f32 {
    let phase = (s.last_poll_t * std::f64::consts::TAU / 3.0).sin() as f32;
    0.8 + 0.2 * phase
}

/// Rain markers (small falling streaks) at grid points with
/// measurable precipitation on the current day. `Manifest::precipitation`
/// comes from a coarse Open-Meteo point grid (see
/// pipeline/fetch_precipitation.py), not a full raster, so this draws
/// individual markers rather than a blended layer.
const PRECIP_THRESHOLD_MM: f32 = 0.5;

fn precipitation_overlay(s: &State) -> El {
    let mut group = el().absolute(0.0, 0.0);
    let Some(precip) = &s.manifest.precipitation else {
        return group;
    };
    let Some(frame) = s.manifest.chlorophyll_frames.get(s.day_index) else {
        return group;
    };
    let date = &frame.date;
    let Some(&day) = s.precip_by_date.get(date) else {
        return group;
    };
    let Some(row) = precip.by_day.get(day) else {
        return group;
    };

    // A falling animation driven by wall-clock time, looping every 0.5 s.
    let phase = (s.last_poll_t.rem_euclid(0.5) / 0.5) as f32;

    for (point, &mm) in precip.points.iter().zip(row.iter()) {
        if mm < PRECIP_THRESHOLD_MM {
            continue;
        }
        let intensity = (mm / 8.0).clamp(0.3, 1.0);
        let [px, py] = *point;
        let (x, y) = (px * s.zoom, py * s.zoom);

        // Localized rain cloud indicator (subtle background glow)
        group = group.child(
            el().absolute(x - 20.0, y - 20.0)
                .size(40.0, 40.0)
                .radius(20.0)
                .opacity(intensity * 0.15)
                .fill(c("#4a90d9")),
        );

        // Three staggered dashes falling along the same diagonal, so there's
        // always one visible as the other resets -- a cheap continuous
        // streak loop instead of a hard jump-cut every cycle.
        for offset in [0.0, 0.33, 0.66] {
            let t = (phase + offset).fract();
            let dy = t * 20.0;
            group = group.child(
                el().absolute(x + dy * 0.4, y + dy)
                    .size(3.0, 12.0)
                    .radius(1.5)
                    .rotate(0.45)
                    .opacity(intensity * (1.0 - t) * 0.95)
                    .fill(c("#9fd8ff")),
            );
        }

        // Daily total label near the rain area
        group = group.child(
            text(format!("{:.1}mm", mm))
                .font(type_::LABEL)
                .color(c("#9fd8ff"))
                .absolute(x + 15.0, y - 10.0),
        );
    }
    group
}

/// All map layers share the detailed raster's equirectangular pixel space.
/// The global image is scaled to 360 x 180 degrees in that same space, with
/// negative offsets placing the detailed region at its correct world position.
fn global_geometry(s: &State) -> (f32, f32, f32, f32) {
    let [west, _, _, north] = s.manifest.global_bbox;
    let [map_west, _, _, map_north] = if s.world_mode {
        s.manifest.global_bbox
    } else {
        s.manifest.map_bbox
    };
    let pixels_per_degree = if s.world_mode {
        s.manifest.global_width / (s.manifest.global_bbox[2] - map_west)
    } else {
        s.manifest.map_width / (s.manifest.map_bbox[2] - map_west)
    };
    let global_w = (s.manifest.global_bbox[2] - west) * pixels_per_degree * s.zoom;
    let global_h = (north - s.manifest.global_bbox[1]) * pixels_per_degree * s.zoom;
    let global_x = (west - map_west) * pixels_per_degree * s.zoom;
    let global_y = (map_north - north) * pixels_per_degree * s.zoom;
    (global_x, global_y, global_w, global_h)
}

fn fit_zoom(s: &State) -> f32 {
    let (width, height) = if s.world_mode {
        (s.manifest.global_width, s.manifest.global_height)
    } else {
        (s.manifest.map_width, s.manifest.map_height)
    };
    ((s.viewport_size.0 - 24.0) / width)
        .min((s.viewport_size.1 - 24.0) / height)
        .clamp(0.08, 6.0)
}

fn reset_map(s: &mut State) {
    s.zoom = fit_zoom(s);
    let (width, height) = if s.world_mode {
        (s.manifest.global_width, s.manifest.global_height)
    } else {
        (s.manifest.map_width, s.manifest.map_height)
    };
    s.pan = (
        (s.viewport_size.0 - width * s.zoom) / 2.0,
        (s.viewport_size.1 - height * s.zoom) / 2.0,
    );
    s.map_fit_pending = false;
}

fn zoom_at(s: &mut State, delta: f32, cursor_x: f32, cursor_y: f32) {
    let old_zoom = s.zoom;
    // World view keeps the globe fitted; the detailed view may zoom out well
    // past fit so the rough global context becomes visible around the region.
    let min_zoom = if s.world_mode {
        fit_zoom(s)
    } else {
        fit_zoom(s) * 0.12
    };
    let new_zoom = (old_zoom * (1.0 + delta)).clamp(min_zoom, 8.0);
    let world_x = (cursor_x - s.pan.0) / old_zoom;
    let world_y = (cursor_y - s.pan.1) / old_zoom;
    s.pan = (cursor_x - world_x * new_zoom, cursor_y - world_y * new_zoom);
    s.zoom = new_zoom;
    clamp_pan(s);
}

/// Keep the detailed Israel/Nile region reachable at all times. The coarse
/// world backdrop fills surrounding space but cannot become an accidental
/// full-globe destination that strands the user away from the data.
const PAN_MARGIN: f32 = 80.0;

fn clamp_pan(s: &mut State) {
    let (width, height) = if s.world_mode {
        (s.manifest.global_width, s.manifest.global_height)
    } else {
        (s.manifest.map_width, s.manifest.map_height)
    };
    let w = width * s.zoom;
    let h = height * s.zoom;
    let (viewport_w, viewport_h) = s.viewport_size;
    let min_x = PAN_MARGIN - w;
    let max_x = viewport_w - PAN_MARGIN;
    let min_y = PAN_MARGIN - h;
    let max_y = viewport_h - PAN_MARGIN;
    s.pan.0 = if w <= viewport_w {
        s.pan.0.clamp(0.0, viewport_w - w)
    } else {
        s.pan.0.clamp(min_x, max_x)
    };
    s.pan.1 = if h <= viewport_h {
        s.pan.1.clamp(0.0, viewport_h - h)
    } else {
        s.pan.1.clamp(min_y, max_y)
    };
}

fn color_legend(s: &State) -> El {
    match s.overlay_layer {
        OverlayLayer::Chlorophyll => {
            let frame = s.manifest.chlorophyll_frames.get(s.day_index);
            let date = frame.map(|f| f.date.clone()).unwrap_or_default();
            let mean = frame.and_then(|f| f.mean).unwrap_or(0.0);
            col()
                .padding(12.0)
                .gap(6.0)
                .w_full()
                .radius(10.0)
                .fill(c(PANEL_BG))
                .border(1.0, c(PANEL_BORDER))
                .child(text("CHLOROPHYLL").font(type_::LABEL).color(c(TEXT_MUTED)))
                .child(row().w_full().h(10.0).radius(5.0).linear_gradient(
                    std::f32::consts::FRAC_PI_2,
                    &[
                        (0.0, c("#040e16")),
                        (0.35, c("#0a3c46")),
                        (0.6, c("#198c78")),
                        (0.8, c("#5ac86e")),
                        (1.0, c("#c8eb46")),
                    ][..],
                ))
                .child(
                    row()
                        .w_full()
                        .child(text("0.03").font(type_::LABEL).color(c(TEXT_MUTED)))
                        .child(el().grow(1.0))
                        .child(text("3 mg/m³").font(type_::LABEL).color(c(TEXT_MUTED))),
                )
                .child(
                    text(format!("{date} · mean {mean:.2} mg/m³"))
                        .font(type_::BODY)
                        .color(c(TEXT_PRIMARY)),
                )
                .child(
                    text("Copernicus Mediterranean + NOAA VIIRS elsewhere")
                        .font(type_::LABEL)
                        .color(c(TEXT_MUTED)),
                )
                .child(
                    text(match frame.and_then(|f| f.noaa_date.as_deref()) {
                        Some(noaa_date) => {
                            format!("NOAA VIIRS exact daily observation: {noaa_date}")
                        }
                        None => "NOAA VIIRS: no supplementary observation for this day".to_string(),
                    })
                    .font(type_::LABEL)
                    .color(c(TEXT_MUTED)),
                )
                .child(
                    text("Exact dates only; missing same-day observations remain transparent")
                        .font(type_::LABEL)
                        .color(c(TEXT_MUTED)),
                )
                .child(
                    text("Rivers/lakes: coarse 4 km satellite estimates, not in-situ measurements")
                        .font(type_::LABEL)
                        .color(c(TEXT_MUTED)),
                )
        }
        OverlayLayer::Sst => {
            let frame =
                sst_index_for_day(s, s.day_index).and_then(|i| s.manifest.sst_frames.get(i));
            let date = frame.map(|f| f.date.clone()).unwrap_or_default();
            let mean = frame.and_then(|f| f.mean).unwrap_or(0.0);
            col()
                .padding(12.0)
                .gap(6.0)
                .w_full()
                .radius(10.0)
                .fill(c(PANEL_BG))
                .border(1.0, c(PANEL_BORDER))
                .child(
                    text("SEA SURFACE TEMPERATURE")
                        .font(type_::LABEL)
                        .color(c(TEXT_MUTED)),
                )
                .child(row().w_full().h(10.0).radius(5.0).linear_gradient(
                    std::f32::consts::FRAC_PI_2,
                    &[
                        (0.0, c("#14286e")),
                        (0.3, c("#146e96")),
                        (0.55, c("#3caa82")),
                        (0.8, c("#e6aa3c")),
                        (1.0, c("#dc3c28")),
                    ][..],
                ))
                .child(
                    row()
                        .w_full()
                        .child(text("14°C").font(type_::LABEL).color(c(TEXT_MUTED)))
                        .child(el().grow(1.0))
                        .child(text("30°C").font(type_::LABEL).color(c(TEXT_MUTED))),
                )
                .child(
                    text(format!("{date} · mean {mean:.1}°C"))
                        .font(type_::BODY)
                        .color(c(TEXT_PRIMARY)),
                )
        }
    }
}

fn river_legend(s: &State) -> El {
    if !s.show_rivers {
        return el();
    }
    col()
        .padding(12.0)
        .gap(6.0)
        .w_full()
        .radius(10.0)
        .fill(c(PANEL_BG))
        .border(1.0, c(PANEL_BORDER))
        .child(text("RIVER FLOW").font(type_::LABEL).color(c(TEXT_MUTED)))
        .child(
            text("Long-term average discharge · m³/s")
                .font(type_::LABEL)
                .color(c(TEXT_PRIMARY)),
        )
        .child(row().w_full().h(10.0).radius(5.0).linear_gradient(
            std::f32::consts::FRAC_PI_2,
            &[
                (0.0, c("#e63c32")),  // 0.1 m³/s
                (0.25, c("#ebb43c")), // 1 m³/s
                (0.5, c("#46c8dc")),  // 10 m³/s
                (1.0, c("#2864f0")),  // 1,000+ m³/s
            ][..],
        ))
        .child(
            row()
                .w_full()
                .child(text("0.1").font(type_::LABEL).color(c(TEXT_MUTED)))
                .child(el().grow(1.0))
                .child(text("1").font(type_::LABEL).color(c(TEXT_MUTED)))
                .child(el().grow(1.0))
                .child(text("10").font(type_::LABEL).color(c(TEXT_MUTED)))
                .child(el().grow(1.0))
                .child(text("100").font(type_::LABEL).color(c(TEXT_MUTED)))
                .child(el().grow(1.0))
                .child(text("1,000+").font(type_::LABEL).color(c(TEXT_MUTED))),
        )
        .child(
            text("Logarithmic scale · static HydroRIVERS data")
                .font(type_::LABEL)
                .color(c(TEXT_MUTED)),
        )
}

/// Switches the map's colour overlay between chlorophyll and sea-surface
/// temperature, re-priming the decode window for whichever just became
/// active.
fn overlay_layer_toggle(s: &State) -> El {
    let is_sst = s.overlay_layer == OverlayLayer::Sst;
    el().key("overlay-layer-toggle")
        .padding(8.0)
        .radius(8.0)
        .fill(c(if is_sst { "#e6aa3c" } else { "#198c78" }))
        .on_press(|s: &mut State| {
            s.overlay_layer = match s.overlay_layer {
                OverlayLayer::Chlorophyll => OverlayLayer::Sst,
                OverlayLayer::Sst => OverlayLayer::Chlorophyll,
            };
            ensure_overlay_window(s, s.day_index);
        })
        .child(
            text(if is_sst {
                "Sea Surface Temp"
            } else {
                "Chlorophyll"
            })
            .color(c(TEXT_PRIMARY)),
        )
}

fn format_count(value: f64) -> String {
    let digits = format!("{:.0}", value);
    let mut grouped = String::with_capacity(digits.len() + digits.len() / 3);
    for (i, ch) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            grouped.push(',');
        }
        grouped.push(ch);
    }
    grouped
}

/// A small on-canvas callout shown next to a facility pin while hovered.
fn facility_tooltip(facility: &FacilityPin, x: f32, y: f32) -> El {
    let kind_label = if facility.kind == "desalination" {
        "Desalination Plant"
    } else {
        "Wastewater Treatment"
    };
    let kind_color = if facility.kind == "desalination" {
        "#5fb8ff"
    } else {
        "#ffb15f"
    };
    let outfall_label = match facility.outfall.as_str() {
        "sea_pipe" => "Sea outfall / submarine pipe",
        "river" => "River discharge",
        "coastal" => "Coastal facility; outfall not reported",
        "reuse" => "Inland facility; outfall not reported",
        "intake" => "Seawater intake",
        _ => "Outfall not reported",
    };
    let capacity = facility
        .design_capacity_m3_day
        .map(|value| format!("Design capacity: {} m³/day", format_count(value)))
        .unwrap_or_else(|| "Design capacity: not reported".to_owned());
    let discharge = facility
        .discharge_m3_day
        .map(|value| format!("Effluent discharge: {} m³/day", format_count(value)))
        .unwrap_or_else(|| "Effluent discharge: not reported".to_owned());
    let population = facility
        .population_served
        .map(|value| format!("Population served: {}", format_count(value)))
        .unwrap_or_else(|| "Population served: not reported".to_owned());
    let treatment = format!(
        "Treatment level: {}",
        facility
            .treatment_level
            .as_deref()
            .unwrap_or("Not reported")
    );
    let mut tooltip = col()
        .absolute(x + 10.0, y - 10.0)
        .padding(10.0)
        .gap(4.0)
        .w(280.0)
        .radius(8.0)
        .fill(c(PANEL_BG))
        .border(1.0, c(PANEL_BORDER))
        .child(
            text(&facility.name)
                .font(type_::BODY)
                .color(c(TEXT_PRIMARY)),
        )
        .child(
            row()
                .gap(6.0)
                .child(el().size(8.0, 8.0).radius(4.0).fill(c(kind_color)))
                .child(text(kind_label).font(type_::LABEL).color(c(kind_color))),
        )
        .child(text(capacity).font(type_::LABEL).color(c("#f0cf7a")))
        .child(text(discharge).font(type_::LABEL).color(c("#8fc9ef")))
        .child(text(population).font(type_::LABEL).color(c(TEXT_MUTED)))
        .child(text(treatment).font(type_::LABEL).color(c(TEXT_MUTED)))
        .child(text(outfall_label).font(type_::LABEL).color(c("#aaaacc")))
        .child(text(&facility.note).font(type_::LABEL).color(c(TEXT_MUTED)));
    if let Some(country) = &facility.country {
        tooltip = tooltip.child(
            text(format!("Country: {country}"))
                .font(type_::LABEL)
                .color(c(TEXT_MUTED)),
        );
    }
    if let Some(source) = &facility.source {
        tooltip = tooltip.child(
            text(format!("Source: {source}"))
                .font(type_::LABEL)
                .color(c("#777788")),
        );
    }
    tooltip
}

fn right_panel(s: &State) -> El {
    col()
        .key("color-legend-panel")
        .w(s.right_panel_width)
        .h_full()
        .min_h(0.0)
        .padding(12.0)
        .gap(12.0)
        .fill(c(SURFACE))
        .border(1.0, c(PANEL_BORDER))
        .scroll(ScrollSpec {
            stick_bottom: false,
        })
        .child(text("COLOR SCALES").font(type_::LABEL).color(c(TEXT_MUTED)))
        .child(color_legend(s))
        .child(river_legend(s))
        .child(
            text("Facility pins remain on the map. Hover a pin for its metadata.")
                .font(type_::LABEL)
                .color(c(TEXT_MUTED)),
        )
}

// Retained for a future data-backed taxonomy view; the synthetic demo UI is disabled.
#[allow(dead_code)]
const TAXA: [(&str, &str); 5] = [
    ("Proteobacteria", "#e76f51"),
    ("Cyanobacteria", "#42a5f5"),
    ("Bacteroidetes", "#55c878"),
    ("Actinobacteria", "#f2b84b"),
    ("Other", "#ad7fd4"),
];

/// Four desalination stations plus the eight largest Israeli HydroWASTE
/// discharge records. The locations are real; only the microbial fractions
/// are deterministic demonstration values.
#[allow(dead_code)]
fn taxonomy_station_indices(s: &State) -> Vec<usize> {
    let mut desal: Vec<usize> = s
        .manifest
        .facilities
        .iter()
        .enumerate()
        .filter(|(_, facility)| {
            facility.kind == "desalination" && facility.country.as_deref() == Some("Israel")
        })
        .map(|(index, _)| index)
        .collect();
    desal.sort_by(|&a, &b| {
        s.manifest.facilities[a]
            .name
            .cmp(&s.manifest.facilities[b].name)
    });
    let mut wwtp: Vec<usize> = s
        .manifest
        .facilities
        .iter()
        .enumerate()
        .filter(|(_, facility)| {
            facility.kind == "wwtp" && facility.country.as_deref() == Some("Israel")
        })
        .map(|(index, _)| index)
        .collect();
    wwtp.sort_by(|&a, &b| {
        s.manifest.facilities[b]
            .discharge_m3_day
            .unwrap_or(0.0)
            .total_cmp(&s.manifest.facilities[a].discharge_m3_day.unwrap_or(0.0))
    });
    desal.extend(wwtp);
    desal.truncate(12);
    desal
}

#[allow(dead_code)]
fn taxonomy_fractions(station: usize) -> [f32; 5] {
    let phase = station as f32 * 0.73;
    let mut values = [
        0.34 + 0.08 * phase.sin(),
        0.23 + 0.07 * (phase + 1.1).cos(),
        0.19 + 0.05 * (phase + 2.2).sin(),
        0.13 + 0.035 * (phase + 0.4).cos(),
        0.08 + 0.02 * (phase + 1.7).sin(),
    ];
    let total: f32 = values.iter().sum();
    for value in &mut values {
        *value /= total;
    }
    values
}

#[allow(dead_code)]
fn pie_chart(station: usize) -> El {
    const SIZE: f32 = 72.0;
    let fractions = taxonomy_fractions(station);
    let center = [SIZE / 2.0, SIZE / 2.0];
    let radius = SIZE / 2.0 - 2.0;
    let mut shape = Shape::new().view_box(SIZE, SIZE);
    let mut angle = -std::f32::consts::FRAC_PI_2;
    for (fraction, (_, color)) in fractions.iter().zip(TAXA) {
        let sweep = fraction * std::f32::consts::TAU;
        let steps = ((sweep / 0.12).ceil() as usize).max(2);
        let mut points = vec![center];
        for step in 0..=steps {
            let theta = angle + sweep * step as f32 / steps as f32;
            points.push([
                center[0] + radius * theta.cos(),
                center[1] + radius * theta.sin(),
            ]);
        }
        shape = shape.fill(points, c(color));
        angle += sweep;
    }
    canvas(shape).size(SIZE, SIZE)
}

#[allow(dead_code)]
fn taxonomy_station_card(station: usize, facility: &FacilityPin) -> El {
    let fractions = taxonomy_fractions(station);
    let mut card = col()
        .key(format!("taxonomy-station-{station}"))
        .w_full()
        .padding(10.0)
        .gap(6.0)
        .radius(10.0)
        .fill(c("#ffffff08"))
        .border(1.0, c(PANEL_BORDER))
        .child(
            text(format!("{:02} · {}", station + 1, facility.name))
                .font(type_::BODY)
                .color(c(TEXT_PRIMARY)),
        )
        .child(
            text(if facility.kind == "desalination" {
                "Desalination monitoring station"
            } else {
                "Wastewater monitoring station"
            })
            .font(type_::LABEL)
            .color(c(TEXT_MUTED)),
        )
        .child(pie_chart(station));
    for (fraction, (name, color)) in fractions.iter().zip(TAXA) {
        card = card.child(
            row()
                .w_full()
                .gap(6.0)
                .child(el().size(8.0, 8.0).radius(4.0).fill(c(color)))
                .child(text(name).font(type_::LABEL).color(c(TEXT_MUTED)))
                .child(el().grow(1.0))
                .child(
                    text(format!("{:.0}%", fraction * 100.0))
                        .font(type_::LABEL)
                        .color(c(TEXT_PRIMARY)),
                ),
        );
    }
    card
}

/// Toggleable left panel with twelve station-specific demonstration pies.
#[allow(dead_code)]
#[derive(Clone, Copy)]
enum PanelSide {
    Left,
    Right,
}

fn panel_resize_handle(side: PanelSide) -> El {
    let (key, default_width) = match side {
        PanelSide::Left => ("taxonomy-resize-handle", 300.0),
        PanelSide::Right => ("legend-resize-handle", 240.0),
    };
    el().key(key)
        .w(7.0)
        .h_full()
        .fill(c("#252737"))
        .cursor(Cursor::ResizeColumn)
        .help("Drag to resize this panel")
        .on_drag::<State>(move |s, drag, tx| {
            tx.instant();
            let current = match side {
                PanelSide::Left => s.taxonomy_width,
                PanelSide::Right => s.right_panel_width,
            };
            match drag.phase {
                Phase::Down => drag.data = current,
                Phase::Move => {
                    let width = match side {
                        PanelSide::Left => (drag.data + drag.dx).clamp(220.0, 520.0),
                        PanelSide::Right => (drag.data - drag.dx).clamp(200.0, 420.0),
                    };
                    match side {
                        PanelSide::Left => s.taxonomy_width = width,
                        PanelSide::Right => s.right_panel_width = width,
                    }
                }
                Phase::Up if drag.cancelled() => match side {
                    PanelSide::Left => s.taxonomy_width = drag.data,
                    PanelSide::Right => s.right_panel_width = drag.data,
                },
                Phase::Up => {}
            }
        })
        .on_double_press_tx(move |s: &mut State, tx: &mut Tx| {
            tx.instant();
            match side {
                PanelSide::Left => s.taxonomy_width = default_width,
                PanelSide::Right => s.right_panel_width = default_width,
            }
        })
}

#[allow(dead_code)]
fn taxonomy_panel(s: &State) -> El {
    if !s.manifest.taxonomy_demo {
        return el();
    }
    if !s.show_taxonomy {
        return el();
    }
    let date = s
        .manifest
        .chlorophyll_frames
        .get(s.day_index)
        .map(|frame| frame.date.clone())
        .unwrap_or_default();
    let mut list = col()
        .w_full()
        .padding(12.0)
        .gap(10.0)
        .child(
            text("TAXONOMY · 12 STATIONS")
                .font(type_::LABEL)
                .color(c(TEXT_MUTED)),
        )
        .child(text(date).font(type_::BODY).color(c(TEXT_PRIMARY)))
        .child(
            text("SPOOFED DEMONSTRATION DATA — not observed microbial measurements. Station locations and facility metadata are real.")
                .font(type_::LABEL)
                .color(c("#f0cf7a")),
        );
    for (station, index) in taxonomy_station_indices(s).into_iter().enumerate() {
        list = list.child(taxonomy_station_card(
            station,
            &s.manifest.facilities[index],
        ));
    }
    col()
        .key("taxonomy-side-panel")
        .w(s.taxonomy_width)
        .h_full()
        .min_h(0.0)
        .fill(c(SURFACE))
        .border(1.0, c(PANEL_BORDER))
        .scroll(ScrollSpec {
            stick_bottom: false,
        })
        .child(list)
}

fn empty_world_manifest(manifest: &Manifest) -> Manifest {
    let mut world = empty_manifest();
    world.map_width = manifest.global_width;
    world.map_height = manifest.global_height;
    world.map_bbox = manifest.global_bbox;
    world
}

/// A labelled text field with a visible outline so inputs stay legible against
/// the dark panel background.
fn bordered_input(edit: &TextEdit, label: &str) -> El {
    text_input(edit, type_::BODY)
        .label(label.to_string())
        .single_line()
        .border(1.0, c("#ffffff1a"))
        .radius(8.0)
}

fn fetch_panel(s: &State) -> El {
    let section_label = |title: &str| {
        text(title)
            .font(type_::LABEL)
            .color(c(SECTION_MUTED))
    };
    let field_label = |title: &str| {
        text(title).font(type_::LABEL).color(c(TEXT_MUTED))
    };
    let mut panel = col()
        .w_full()
        .gap(10.0)
        .child(
            text("Fetch local data")
                .font(type_::SECTION)
                .color(c(TEXT_PRIMARY)),
        )
        .child(
            text("Downloads stay on this machine. Provider terms and coverage vary by source.")
                .font(type_::LABEL)
                .color(c(TEXT_MUTED))
                .wrap(),
        )
        .child(section_label("AREA OF INTEREST"))
        .child(
            row()
                .w_full()
                .gap(6.0)
                .child(
                    col()
                        .grow(1.0)
                        .gap(2.0)
                        .child(field_label("West"))
                        .child(
                            bordered_input(&s.fetch_west, "West longitude")
                                .key("fetch-west")
                                .w_full()
                                .on_edit(|s: &mut State| &mut s.fetch_west),
                        ),
                )
                .child(
                    col()
                        .grow(1.0)
                        .gap(2.0)
                        .child(field_label("East"))
                        .child(
                            bordered_input(&s.fetch_east, "East longitude")
                                .key("fetch-east")
                                .w_full()
                                .on_edit(|s: &mut State| &mut s.fetch_east),
                        ),
                ),
        )
        .child(
            row()
                .w_full()
                .gap(6.0)
                .child(
                    col()
                        .grow(1.0)
                        .gap(2.0)
                        .child(field_label("South"))
                        .child(
                            bordered_input(&s.fetch_south, "South latitude")
                                .key("fetch-south")
                                .w_full()
                                .on_edit(|s: &mut State| &mut s.fetch_south),
                        ),
                )
                .child(
                    col()
                        .grow(1.0)
                        .gap(2.0)
                        .child(field_label("North"))
                        .child(
                            bordered_input(&s.fetch_north, "North latitude")
                                .key("fetch-north")
                                .w_full()
                                .on_edit(|s: &mut State| &mut s.fetch_north),
                        ),
                ),
        )
        .child(section_label("PERIOD"))
        .child(
            row()
                .w_full()
                .gap(6.0)
                .child(
                    col()
                        .grow(1.0)
                        .gap(2.0)
                        .child(field_label("Start"))
                        .child(
                            bordered_input(&s.fetch_start, "Start date")
                                .key("fetch-start")
                                .w_full()
                                .on_edit(|s: &mut State| &mut s.fetch_start),
                        ),
                )
                .child(
                    col()
                        .grow(1.0)
                        .gap(2.0)
                        .child(field_label("End (blank = latest)"))
                        .child(
                            bordered_input(&s.fetch_end, "End date (blank = latest)")
                                .key("fetch-end")
                                .w_full()
                                .on_edit(|s: &mut State| &mut s.fetch_end),
                        ),
                ),
        )
        .child(section_label("CREDENTIALS (SESSION ONLY)"))
        .child(
            col()
                .gap(6.0)
                .child(
                    col()
                        .gap(2.0)
                        .child(field_label("Copernicus username"))
                        .child(
                            bordered_input(&s.copernicus_username, "Copernicus username")
                                .key("copernicus-username")
                                .w_full()
                                .on_edit(|s: &mut State| &mut s.copernicus_username),
                        ),
                )
                .child(
                    col()
                        .gap(2.0)
                        .child(field_label("Password"))
                        .child(
                            bordered_input(&s.copernicus_password, "Copernicus password")
                                .key("copernicus-password")
                                .secure()
                                .w_full()
                                .on_edit(|s: &mut State| &mut s.copernicus_password),
                        ),
                ),
        )
        .child(section_label("ACTIONS"))
        .child(
            row()
                .w_full()
                .gap(8.0)
                .child(
                    el().key("start-fetch")
                        .grow(1.0)
                        .padding(10.0)
                        .radius(10.0)
                        .fill(c(ACCENT))
                        .on_press(|s: &mut State| start_fetch(s))
                        .child(text("Fetch bounds").color(c("#08221d"))),
                )
                .child(
                    el().key("fetch-visible")
                        .padding(10.0)
                        .radius(10.0)
                        .fill(c(BTN_BG))
                        .grow(1.0)
                        .on_press(|s: &mut State| {
                            let world_manifest;
                            let active_manifest = if s.world_mode {
                                world_manifest = empty_world_manifest(&s.manifest);
                                &world_manifest
                            } else {
                                &s.manifest
                            };
                            let bbox = map::visible_bbox(
                                active_manifest,
                                map::ViewTransform {
                                    zoom: s.zoom,
                                    pan: s.pan,
                                    viewport: s.viewport_size,
                                },
                            );
                            s.fetch_west.replace_text(format!("{:.5}", bbox[0]));
                            s.fetch_south.replace_text(format!("{:.5}", bbox[1]));
                            s.fetch_east.replace_text(format!("{:.5}", bbox[2]));
                            s.fetch_north.replace_text(format!("{:.5}", bbox[3]));
                            start_fetch(s);
                        })
                        .child(text("Use view").color(c(TEXT_PRIMARY))),
                ),
        )
        .child(
            row()
                .w_full()
                .gap(8.0)
                .child(
                    el().key("cancel-fetch")
                        .grow(1.0)
                        .padding(10.0)
                        .radius(10.0)
                        .fill(c(BTN_BG))
                        .on_press(|s: &mut State| {
                            if let Some(job) = s.worker_job.as_mut() {
                                job.cancel();
                            }
                            s.worker_job = None;
                            s.worker_message = "Cancellation requested".into();
                        })
                        .child(text("Cancel").color(c(TEXT_PRIMARY))),
                )
                .child(
                    el().key("load-folder")
                        .grow(1.0)
                        .padding(10.0)
                        .radius(10.0)
                        .fill(c(BTN_BG))
                        .on_press_tx(|s: &mut State, tx: &mut Tx| {
                            let start = std::env::current_exe()
                                .ok()
                                .and_then(|exe| exe.parent().map(|p| p.to_path_buf()))
                                .unwrap_or_else(|| s.data_root.clone());
                            tx.open_folder(
                                FileDialogOptions {
                                    title: "Choose a FossPulser data bundle folder".into(),
                                    directory: Some(start),
                                    ..Default::default()
                                },
                                |s: &mut State, result: FileDialogResult, _tx: &mut Tx| {
                                    match result {
                                        Ok(Some(path)) => match assets::load_picked_folder(&path)
                                        {
                                            Some(bundle) => {
                                                activate_bundle(s, bundle);
                                                s.worker_message =
                                                    format!("Loaded bundle from {}", path.display());
                                            }
                                            None => {
                                                s.worker_message = format!(
                                                    "No data bundle found in {} (expected a folder containing frames/manifest.json)",
                                                    path.display()
                                                );
                                            }
                                        },
                                        Ok(None) => {}
                                        Err(e) => {
                                            s.worker_message = format!("Folder dialog failed: {e}");
                                        }
                                    }
                                },
                            );
                        })
                        .child(text("Load folder…").color(c(TEXT_PRIMARY))),
                ),
        )
        .child(
            text(&s.worker_message)
                .font(type_::BODY)
                .color(c(TEXT_PRIMARY))
                .wrap(),
        );
    if !s.manifest.source_status.is_empty() {
        let availability = s
            .manifest
            .source_status
            .iter()
            .map(|(name, value)| format!("{name}: {}", value.status))
            .collect::<Vec<_>>()
            .join(" · ");
        panel = panel.child(
            text(availability)
                .font(type_::LABEL)
                .color(c(TEXT_MUTED))
                .wrap(),
        );
    }
    if let Some(event) = s.worker_events.last() {
        let mut line = stage_description(&event.stage).to_string();
        if let Some(message) = &event.message {
            if !message.is_empty() {
                line.push_str(&format!(" — {message}"));
            }
        }
        if let (Some(done), Some(total)) = (event.bytes_completed, event.bytes_total) {
            if total > 0 {
                line.push_str(&format!(
                    " · {} / {}",
                    format_bytes(done),
                    format_bytes(total)
                ));
                if done <= total {
                    line.push_str(&format!(" ({:.0}%)", 100.0 * done as f64 / total as f64));
                }
                if let Some(rate) = s.progress_speed {
                    if rate > 1024.0 {
                        line.push_str(&format!(" · {}/s", format_bytes(rate as u64)));
                    }
                }
            }
        } else if let (Some(done), Some(total)) = (event.completed, event.total) {
            if total > 0 {
                line.push_str(&format!(" · {done} / {total}"));
            }
        }
        panel = panel.child(text(line).font(type_::LABEL).color(c(TEXT_MUTED)).wrap());
    }
    panel
}

fn control_bar(s: &State) -> El {
    if !s.has_bundle {
        return col().w_full().padding(12.0).fill(c(SURFACE)).child(
            text("No time series loaded. Fetch a local bundle to enable playback.")
                .color(c(TEXT_MUTED)),
        );
    }
    let max_index = s.manifest.chlorophyll_frames.len().saturating_sub(1).max(1);
    let progress = s.day_index as f32 / max_index as f32;

    col()
        .w_full()
        .padding(16.0)
        .gap(10.0)
        .fill(c(SURFACE))
        .border(1.0, c(PANEL_BORDER))
        .child(
            row()
                .w_full()
                .gap(12.0)
                .child(
                    el().key("play")
                        .size(36.0, 36.0)
                        .radius(18.0)
                        .fill(c(if s.playing { ACCENT } else { "#2a2c3a" }))
                        .center()
                        .on_press(|s: &mut State| s.playing = !s.playing)
                        .child(text(if s.playing { "❚❚" } else { "▶" }).color(c(TEXT_PRIMARY))),
                )
                .child(text(if s.playing { "Pause" } else { "Play" }).color(c(TEXT_PRIMARY)))
                .child(el().grow(1.0))
                .child(text("Speed (days/s)").color(c(TEXT_MUTED)))
                .child(speed_slider(s))
                .child(speed_editor(s)),
        )
        .child(date_carousel(s))
        .child(scrubber(s, progress))
        .child(
            row()
                .w_full()
                .gap(16.0)
                .child(text("Color intensity").color(c(TEXT_MUTED)))
                .child(intensity_slider(s))
                .child(el().grow(1.0))
                .child(
                    el().key("rivers-toggle")
                        .padding(8.0)
                        .radius(8.0)
                        .fill(c(if s.show_rivers { ACCENT } else { "#2a2c3a" }))
                        .on_press(|s: &mut State| s.show_rivers = !s.show_rivers)
                        .child(text("Rivers").color(c(TEXT_PRIMARY))),
                )
                .child(
                    el().key("rain-toggle")
                        .padding(8.0)
                        .radius(8.0)
                        .fill(c(if s.show_precipitation {
                            "#9fd8ff"
                        } else {
                            "#2a2c3a"
                        }))
                        .on_press(|s: &mut State| s.show_precipitation = !s.show_precipitation)
                        .child(text("Rain").color(c(TEXT_PRIMARY))),
                )
                .child(overlay_layer_toggle(s))
                .child(
                    el().key("desal-toggle")
                        .padding(8.0)
                        .radius(8.0)
                        .fill(c(if s.show_desalination {
                            "#5fb8ff"
                        } else {
                            "#2a2c3a"
                        }))
                        .on_press(|s: &mut State| s.show_desalination = !s.show_desalination)
                        .child(text("Desalination").color(c(TEXT_PRIMARY))),
                )
                .child(
                    el().key("wwtp-toggle")
                        .padding(8.0)
                        .radius(8.0)
                        .fill(c(if s.show_wwtp { "#ffb15f" } else { "#2a2c3a" }))
                        .on_press(|s: &mut State| s.show_wwtp = !s.show_wwtp)
                        .child(text("Wastewater").color(c(TEXT_PRIMARY))),
                )
                .child(zoom_button(-0.25, "-"))
                .child(zoom_button(0.25, "+"))
                .child(
                    el().key("reset-view")
                        .padding(8.0)
                        .radius(8.0)
                        .fill(c(BTN_BG))
                        .on_press(|s: &mut State| reset_map(s))
                        .child(text("Reset view").color(c(TEXT_PRIMARY))),
                )
                .child(
                    el().key("world-view")
                        .padding(8.0)
                        .radius(8.0)
                        .fill(c(if s.world_mode { ACCENT } else { "#2a2c3a" }))
                        .on_press(|s: &mut State| {
                            s.world_mode = true;
                            reset_map(s);
                        })
                        .child(text("World").color(c(TEXT_PRIMARY))),
                )
                .child(
                    el().key("home-view")
                        .padding(8.0)
                        .radius(8.0)
                        .fill(c(if !s.world_mode { ACCENT } else { "#2a2c3a" }))
                        .on_press(|s: &mut State| {
                            if s.has_bundle {
                                s.world_mode = false;
                                reset_map(s);
                            }
                        })
                        .child(text("Home").color(c(TEXT_PRIMARY))),
                )
                .child(
                    el().key("fetch-toggle")
                        .padding(8.0)
                        .radius(8.0)
                        .fill(c(if s.side_panel_open { ACCENT } else { BTN_BG }))
                        .on_press(|s: &mut State| s.side_panel_open = !s.side_panel_open)
                        .child(text("Fetch data").color(c(TEXT_PRIMARY))),
                )
                .child(
                    el().key("help-toggle")
                        .padding(8.0)
                        .radius(8.0)
                        .fill(c(if s.show_help { ACCENT } else { "#2a2c3a" }))
                        .on_press(|s: &mut State| s.show_help = !s.show_help)
                        .child(text("?").color(c(TEXT_PRIMARY))),
                ),
        )
}

fn help_overlay(s: &State) -> El {
    if !s.show_help {
        return el();
    }
    col()
        .absolute(16.0, 16.0)
        .padding(16.0)
        .gap(8.0)
        .w(320.0)
        .radius(12.0)
        .fill(c(PANEL_BG))
        .border(1.0, c(PANEL_BORDER))
        .child(text("Help").font(type_::TITLE).color(c(TEXT_PRIMARY)))
        .child(
            text("• Scroll to zoom in/out")
                .font(type_::BODY)
                .color(c(TEXT_MUTED)),
        )
        .child(
            text("• Drag to pan the map")
                .font(type_::BODY)
                .color(c(TEXT_MUTED)),
        )
        .child(
            text("• Click dates or use ‹ › to navigate")
                .font(type_::BODY)
                .color(c(TEXT_MUTED)),
        )
        .child(
            text("• Toggle layers with the buttons below")
                .font(type_::BODY)
                .color(c(TEXT_MUTED)),
        )
        .child(
            text("• Hover over facility pins for details")
                .font(type_::BODY)
                .color(c(TEXT_MUTED)),
        )
        .child(
            text("• Adjust color intensity with the slider")
                .font(type_::BODY)
                .color(c(TEXT_MUTED)),
        )
}

fn set_speed(s: &mut State, value: f32) {
    s.days_per_sec = value.clamp(0.0, 30.0);
    let label = if s.days_per_sec.fract().abs() < 0.001 {
        format!("{:.0}", s.days_per_sec)
    } else {
        format!("{:.2}", s.days_per_sec)
    };
    s.speed_edit.replace_text(label);
}

fn speed_slider(s: &State) -> El {
    const WIDTH: f32 = 180.0;
    row().key("speed-slider").w(WIDTH).h(18.0).child(
        el().w_full()
            .h(4.0)
            .radius(2.0)
            .fill(c(BTN_BG))
            .child(
                el().w(WIDTH * s.days_per_sec / 30.0)
                    .h(4.0)
                    .radius(2.0)
                    .fill(c(ACCENT)),
            )
            .on_drag::<State>(|s, drag, _tx| {
                set_speed(s, (drag.x / drag.w.max(1.0)).clamp(0.0, 1.0) * 30.0);
            }),
    )
}

fn speed_editor(s: &State) -> El {
    text_input(&s.speed_edit, type_::BODY)
        .key("speed-editor")
        .label("Playback speed in days per second")
        .single_line()
        .w(58.0)
        .h(30.0)
        .padding(6.0)
        .radius(6.0)
        .fill(c(BTN_BG))
        .on_edit(|s: &mut State| &mut s.speed_edit)
        .on_input_change(|s: &mut State, _tx: &mut Tx| {
            if let Ok(value) = s.speed_edit.text.trim().parse::<f32>() {
                if value.is_finite() && (0.0..=30.0).contains(&value) {
                    s.days_per_sec = value;
                }
            }
        })
}

fn side_panel_toggle(s: &State) -> El {
    let width = if s.side_panel_open { 320.0 } else { 0.0 };
    let expanded = col()
        .w_full()
        .gap(10.0)
        .child(
            row()
                .w_full()
                .child(
                    text("DATA SOURCES")
                        .font(type_::LABEL)
                        .color(c(SECTION_MUTED)),
                )
                .child(el().grow(1.0))
                .child(
                    el().key("side-collapse")
                        .padding(4.0)
                        .radius(6.0)
                        .fill(c(BTN_BG))
                        .on_press(|s: &mut State| s.side_panel_open = false)
                        .child(text("‹").color(c(TEXT_MUTED))),
                ),
        )
        .child(fetch_panel(s));
    let header_rail = el()
        .key("side-expand")
        .w(36.0)
        .h_full()
        .min_h(0.0)
        .fill(c(PANEL_BG_SOLID))
        .center()
        .on_press(|s: &mut State| s.side_panel_open = true)
        .child(text("›").color(c(TEXT_MUTED)));
    if s.side_panel_open {
        col()
            .key("data-side-panel")
            .w(width)
            .h_full()
            .min_h(0.0)
            .fill(c(PANEL_BG_SOLID))
            .border(1.0, c(PANEL_BORDER))
            .padding(14.0)
            .gap(12.0)
            .scroll(ScrollSpec {
                stick_bottom: false,
            })
            .child(expanded)
            .child(header_rail)
    } else {
        header_rail
    }
}

fn zoom_button(delta: f32, label: &str) -> El {
    el().key(if delta > 0.0 { "zoom-in" } else { "zoom-out" })
        .size(32.0, 32.0)
        .radius(8.0)
        .fill(c(BTN_BG))
        .on_press(move |s: &mut State| {
            let cx = s.viewport_size.0 / 2.0;
            let cy = s.viewport_size.1 / 2.0;
            zoom_at(s, delta, cx, cy);
        })
        .child(text(label).color(c(TEXT_PRIMARY)))
}

/// A "carousel"-style date readout above the scrubber: a bold centered date
/// flanked by its fading, clickable neighbours, plus ‹ › step buttons that
/// move by one day (our minimal interval) on each side of the centre date.
fn date_carousel(s: &State) -> El {
    const SIDE: i64 = 2; // neighbours shown on each side of the current day

    row()
        .key("date-carousel")
        .w_full()
        .gap(10.0)
        .items_center()
        .justify_center()
        .child(carousel_step_button(s, -1, "‹"))
        .child({
            let mut strip = row().gap(14.0).items_center();
            for offset in -SIDE..=SIDE {
                if let Some(el) = carousel_date(s, offset) {
                    strip = strip.child(el);
                }
            }
            strip
        })
        .child(carousel_step_button(s, 1, "›"))
}

/// One date in the carousel at `offset` days from the current day; `None`
/// if that day is out of range. The current day (`offset == 0`) is bold and
/// full-opacity; neighbours shrink and fade with distance and jump straight
/// to that day when clicked.
fn carousel_date(s: &State, offset: i64) -> Option<El> {
    let target = s.day_index as i64 + offset;
    if target < 0 || target as usize >= s.manifest.chlorophyll_frames.len() {
        return None;
    }
    let target = target as usize;
    let date = s.manifest.chlorophyll_frames.get(target)?.date.clone();
    let dist = offset.unsigned_abs() as f32;
    let font = if offset == 0 {
        type_::TITLE
    } else {
        type_::BODY
    };
    let alpha = if offset == 0 {
        255
    } else {
        (210.0 - dist * 70.0).max(60.0) as u32
    };
    let color = format!("#f2f2f5{:02x}", alpha.min(255));
    Some(
        el().key(format!("carousel-{offset}"))
            .padding(4.0)
            .on_press(move |s: &mut State| set_day(s, target))
            .child(text(date).font(font).color(c(&color))),
    )
}

fn carousel_step_button(s: &State, delta: i64, label: &str) -> El {
    let max = s.manifest.chlorophyll_frames.len().saturating_sub(1);
    let target = (s.day_index as i64 + delta).clamp(0, max as i64) as usize;
    el().key(if delta < 0 {
        "carousel-prev"
    } else {
        "carousel-next"
    })
    .size(28.0, 28.0)
    .radius(14.0)
    .fill(c(BTN_BG))
    .center()
    .on_press(move |s: &mut State| set_day(s, target))
    .child(text(label).color(c(TEXT_PRIMARY)))
}

fn scrubber(_s: &State, progress: f32) -> El {
    let track_w = VIEWPORT_W;
    row().key("scrubber").w_full().h(18.0).child(
        el().w_full()
            .h(4.0)
            .radius(2.0)
            .fill(c(BTN_BG))
            .child(
                el().w(track_w * progress)
                    .h(4.0)
                    .radius(2.0)
                    .fill(c(ACCENT)),
            )
            .on_drag::<State>(move |s, drag, _tx| {
                let max = s.manifest.chlorophyll_frames.len().saturating_sub(1);
                let frac = (drag.x / drag.w.max(1.0)).clamp(0.0, 1.0);
                set_day(s, (frac * max as f32).round() as usize);
            }),
    )
}

fn intensity_slider(s: &State) -> El {
    row().key("intensity").w(140.0).h(14.0).child(
        el().w_full()
            .h(4.0)
            .radius(2.0)
            .fill(c(BTN_BG))
            .child(
                el().w(140.0 * s.color_intensity)
                    .h(4.0)
                    .radius(2.0)
                    .fill(c("#c8eb46")),
            )
            .on_drag::<State>(|s, drag, _tx| {
                s.color_intensity = (drag.x / drag.w.max(1.0)).clamp(0.0, 1.0);
            }),
    )
}

fn main() {
    rune::run(AppSpec {
        setup: Some(setup),
        poll: Some(poll),
        ..AppSpec::new("FossPulser", (1440.0, 980.0), init, view)
    });
}
