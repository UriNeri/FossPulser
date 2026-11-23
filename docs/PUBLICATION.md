# Publication and releases

FossPulser publishes source code and user-built executables, not downloaded research data, local environments, credentials, or generated QA captures.

## Before publishing

1. Review the Git index and working tree for local-only files, credentials, and generated artifacts.
2. Run the Python and Rust tests.
3. Confirm that source and provider links in the documentation resolve to their intended projects.
4. Build each executable from the same commit and record its target architecture.

Review configuration, documentation, and release notes manually before publishing.

## Release packages

Each OS/architecture package contains the executable, a `source/` directory with the clean source snapshot, the license, and platform launch notes. Standalone source archives and SHA-256 checksums accompany the binary packages.

The Linux and Windows packages contain native x86-64 executables. Data acquisition additionally requires the Python/Pixi environment described in the README. macOS and other architectures require builds on their respective platforms or a suitable CI runner; do not label an untested cross-built binary as verified.
