# Windows Cross-Compilation

## Verified status

Cross-compilation from Linux to 64-bit Windows with MinGW-w64 succeeds. On 2026-11-16 the release build completed without errors and produced:

```text
app/target/x86_64-pc-windows-gnu/release/fosspulser.exe
```

The output was identified as a PE32+ x86-64 Windows executable and was approximately 46 MB. Compilation is verified; execution and rendering on a Windows machine have not yet been tested.

## Reproduce the build

The repository's Pixi environment supplies the MinGW-w64 compiler and Windows Rust standard library. The linker and archiver are configured in `app/.cargo/config.toml`.

```bash
rustup target add x86_64-pc-windows-gnu
pixi install
PATH="$PWD/.pixi/envs/default/bin:$PATH" \
  cargo build --manifest-path app/Cargo.toml \
  --target x86_64-pc-windows-gnu --release
```

The explicit `PATH` makes the Pixi-provided `x86_64-w64-mingw32-gcc` and `x86_64-w64-mingw32-ar` available to Cargo. Running through an activated Pixi shell should provide the same tools.

## Why this target

- `x86_64-pc-windows-gnu` works with the Linux-hosted MinGW-w64 toolchain and does not require the Microsoft SDK.
- Rune's rendering/windowing dependency stack supports Windows through wgpu/winit.
- The repository already carries the required patched dependencies, including `accesskit_windows`.
- The MSVC target is an alternative for native Windows CI, but is not needed for the verified Linux-hosted build.

## Data and packaging

The executable resolves local data at runtime. Set `FOSSPULSER_DATA_DIR`, or place a user-created `data/` directory beside the executable. A fresh package can start without a manifest and presents the fetch/onboarding panel. Do not include provider archives, generated rasters, credentials, or local environment files in a release.

The Python fetch/build pipeline remains Linux-configured (`platforms = ["linux-64"]`). A Windows package therefore needs a documented Python/Pixi worker setup or a separately packaged worker; the native executable alone does not eliminate that prerequisite.

## Recommended CI

A native Windows runner avoids MinGW setup and provides a useful second build path:

```yaml
name: Windows Build
on: [push, pull_request]
jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - run: cargo build --release --manifest-path app/Cargo.toml
      - uses: actions/upload-artifact@v4
        with:
          name: fosspulser-windows
          path: app/target/release/fosspulser.exe
```

Keep generated data assets out of the artifact. A Windows smoke test is still required to validate window creation, GPU backend selection, text rendering, links, worker discovery, and local asset loading.
