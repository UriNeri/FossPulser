use crate::model::Manifest;
use serde::Deserialize;
use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub struct Bundle {
    pub id: String,
    #[allow(dead_code)]
    pub root: PathBuf,
    pub frames_dir: PathBuf,
    pub manifest: Manifest,
}

#[derive(Deserialize)]
struct Active {
    bundle_id: Option<String>,
    manifest: Option<String>,
}

pub fn data_root() -> PathBuf {
    if let Some(value) = std::env::var_os("FOSSPULSER_DATA_DIR") {
        return PathBuf::from(value);
    }
    if let Ok(executable) = std::env::current_exe() {
        if let Some(directory) = executable.parent() {
            let portable = directory.join("data");
            if portable.exists() {
                return portable;
            }
        }
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../data")
}

fn contained_path(root: &Path, value: &str) -> Option<PathBuf> {
    let path = Path::new(value);
    if path.is_absolute() {
        return None;
    }
    let mut normalized = root.to_path_buf();
    for component in value.split(['/', '\\']) {
        match component {
            "" | "." => {}
            ".." => {
                normalized.pop();
            }
            _ => normalized.push(component),
        }
    }
    // The manifest lives in frames/, so ../curated is valid within a bundle;
    // escaping the bundle's parent is not. Canonicalization also handles
    // symlinks when the target exists.
    let bundle_root = root.parent()?.parent()?;
    let canonical_root = bundle_root.canonicalize().ok()?;
    let canonical = normalized.canonicalize().ok()?;
    canonical.starts_with(&canonical_root).then_some(canonical)
}

fn read_bundle(root: &Path, id: &str, manifest_path: &Path) -> Option<Bundle> {
    let bytes = std::fs::read(manifest_path).ok()?;
    let manifest: Manifest = serde_json::from_slice(&bytes).ok()?;
    if manifest.map_width <= 0.0 || manifest.map_height <= 0.0 {
        return None;
    }
    let frames_dir = manifest_path.parent()?.to_path_buf();
    for path in [&manifest.global_context, &manifest.basemap] {
        if path.is_empty() {
            return None;
        }
        contained_path(&frames_dir, path)?.is_file().then_some(())?;
    }
    for frame in &manifest.chlorophyll_frames {
        contained_path(&frames_dir, &frame.path)?
            .is_file()
            .then_some(())?;
    }
    Some(Bundle {
        id: id.to_owned(),
        root: root.to_path_buf(),
        frames_dir,
        manifest,
    })
}

pub fn discover_bundles(root: &Path) -> Vec<Bundle> {
    let mut bundles = Vec::new();
    let active = std::fs::read(root.join("active.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice::<Active>(&bytes).ok());
    if let Some(active) = active {
        if let Some(path) = active.manifest {
            if let Some(path) = contained_path(root, &path) {
                let id = active.bundle_id.unwrap_or_else(|| "active".into());
                if let Some(bundle) = read_bundle(root, &id, &path) {
                    bundles.push(bundle);
                }
            }
        }
    }
    if let Ok(entries) = std::fs::read_dir(root.join("bundles")) {
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let id = entry.file_name().to_string_lossy().into_owned();
            if bundles.iter().any(|bundle| bundle.id == id) {
                continue;
            }
            if let Some(bundle) = read_bundle(root, &id, &path.join("frames/manifest.json")) {
                bundles.push(bundle);
            }
        }
    }
    bundles
}

pub fn load_active(root: &Path) -> Option<Bundle> {
    discover_bundles(root).into_iter().next()
}

pub fn load_manifest_path(root: &Path, path: &Path) -> Option<Bundle> {
    let id = path
        .parent()?
        .parent()?
        .file_name()?
        .to_string_lossy()
        .into_owned();
    read_bundle(root, &id, path)
}

/// A bundle the user picked themselves in a folder dialog. The chosen folder
/// is trusted (the user browsed to it), so this reads `manifest.json` directly
/// from it instead of going through the data-root layout. The folder may be
/// either a bundle directory (`frames/manifest.json`) or a `frames` directory
/// itself (`manifest.json`).
pub fn load_picked_folder(folder: &Path) -> Option<Bundle> {
    let frames = if folder.join("manifest.json").is_file() {
        folder.to_path_buf()
    } else if folder.join("frames/manifest.json").is_file() {
        folder.join("frames")
    } else {
        return None;
    };
    let id = frames
        .parent()
        .and_then(|p| p.file_name())
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "picked".into());
    // The bundle root for path containment is the folder holding frames/.
    let root = frames.parent().unwrap_or(folder);
    read_bundle(root, &id, &frames.join("manifest.json"))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn traversal_is_rejected() {
        assert!(contained_path(Path::new("/tmp/root"), "/absolute").is_none());
    }
}
