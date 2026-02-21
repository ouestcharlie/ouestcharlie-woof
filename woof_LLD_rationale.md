# Woof LLD Rationale

This document captures the reasoning behind design decisions in [woof_LLD.md](woof_LLD.md).

## UI Framework

Woof needs a multi-platform UI capable of displaying AVIF image grids, spawning child processes (MCP stdio transport), and running as a long-lived controller process.

### Alternatives considered

| Framework | Language | Binary size | AVIF support | Process spawning | Maturity |
|---|---|---|---|---|---|
| **Tauri** | Rust + Web (JS/TS) | ~5-10 MB | System webview (Safari 16+, Chrome 85+) | Rust `std::process` | Growing fast, v2 stable |
| **Electron** | JS/TS | ~150-200 MB | Chromium built-in | Node.js `child_process` | Very mature |
| **Flutter** | Dart | ~20-30 MB | Needs plugin (`flutter_avif`) | `dart:io Process` | Desktop maturing |
| **Qt 6** | C++ or Python (PySide6) | ~30-50 MB | Qt 6.5+ via `libavif` plugin | Native `QProcess` | Very mature |
| **Web app (localhost)** | Any backend + HTML/JS | N/A | Browser-native | Backend spawns processes | Simplest |

### Analysis

**Tauri** is the strongest fit:
- Lightweight — uses the system WebKit on macOS (no bundled browser engine)
- Rust backend naturally handles MCP stdio transport (spawn + stdin/stdout piping)
- AVIF works out of the box via macOS WebKit (Safari 16+ = macOS Ventura+, which matches the V1 target)
- Web frontend (React, Svelte, Vue) gives fast UI iteration for photo grids
- Multi-platform (macOS, Windows, Linux) from a single codebase
- Rust aligns well with performance-sensitive operations (SHA-256 hashing, AVIF encoding via `libavif` bindings)

**Electron** works but is heavy — 150 MB+ for a photo browser feels wrong when Tauri achieves the same with 5-10 MB. The main advantage is ecosystem maturity and npm library availability.

**Web app (localhost)** is the lowest-friction V1 option — Woof runs as a server, UI is just a browser tab. No packaging, no framework, instant AVIF support. Downside: not a "real" app, no system tray, no native file associations. But for V1 validation, it removes all UI framework risk and lets you focus on the agent pipeline.

**Flutter** and **Qt** are viable but less natural — Flutter's desktop is still maturing, and Qt brings C++/Python into a stack that otherwise could be pure Rust+TS.

### Incremental path

The web app and Tauri approaches are not mutually exclusive — V1 can start as a localhost web app, then wrap it in Tauri later since Tauri's frontend IS a web app. Zero throwaway work.

**References:**
- [Tauri v2](https://v2.tauri.app/)
- [Electron](https://www.electronjs.org/)
- [Flutter Desktop](https://docs.flutter.dev/platform-integration/desktop)
- [Qt 6](https://www.qt.io/product/qt6)
