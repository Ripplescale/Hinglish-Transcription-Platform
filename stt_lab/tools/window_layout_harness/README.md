# Windows maximize geometry check

This small Windows-only executable imports the application's exact
`frontend/src-tauri/src/window_layout.rs`. It creates a disposable, non-activating
tool window and tests its native maximize/restore behavior. It does not open xx,
record audio, load models, or read user meeting data.

Use an MSVC Developer PowerShell with Rust available on `PATH`:

```powershell
cargo test --offline --manifest-path stt_lab/tools/window_layout_harness/Cargo.toml
cargo run --offline --manifest-path stt_lab/tools/window_layout_harness/Cargo.toml -- geometry-report.json
```

Dependencies must already be cached for `--offline`. Set `CARGO_TARGET_DIR` to an
isolated build directory to avoid contending with an application build. If the
Windows C runtime is not installed globally, add the matching Microsoft VC143 CRT
redistribution directory to this process's `PATH`.

The source module defaults to the relative application path. To test another
checkout, set `XX_WINDOW_LAYOUT_SOURCE` to its exact `window_layout.rs` path. The
build script embeds a Common Controls v6 activation manifest so named subclass
APIs resolve under the same activation context used by Tauri.

The JSON report records monitor/work-area bounds, DWM visible frame bounds,
outer window bounds, and native minimized/maximized state. Checks cover repeated
maximize/restore, width changes, normal and maximized minimize/restore,
unchanged resize constraints, duplicate installation and destruction.
Maximized outer width and horizontal position are retained; DWM frame edges allow
one physical pixel for the native border change between normal and maximized
windows. The visible vertical edges must match the monitor work area, excluding
the taskbar.

Only connected monitors are exercised. Pure module tests additionally cover
negative monitor coordinates, taskbar offsets and DPI conversion; those checks do
not establish real multi-monitor or mixed-DPI behavior on hardware not present.
