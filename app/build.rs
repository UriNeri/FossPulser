//! The system only ships `libxkbcommon.so.0` (the runtime .so, no dev
//! symlink), and we deliberately don't want to require a `sudo apt install
//! libxkbcommon-dev` just to build this app. Point rustc at the pixi
//! environment's lib dir instead, where conda-forge's `libxkbcommon` /
//! `wayland` / `libxcb` packages (declared in the repo's pixi.toml) provide
//! the unversioned `.so` symlinks the linker wants.
fn main() {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").expect("set by cargo");
    let lib_dir = std::path::Path::new(&manifest_dir).join("../.pixi/envs/default/lib");
    if lib_dir.exists() {
        println!("cargo:rustc-link-search=native={}", lib_dir.display());
    } else {
        println!(
            "cargo:warning=pixi env lib dir not found at {}; run `pixi install` from the repo root first",
            lib_dir.display()
        );
    }
}
