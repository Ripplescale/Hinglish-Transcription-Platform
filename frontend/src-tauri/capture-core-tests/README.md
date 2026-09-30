# Isolated source-component tests

This small crate compiles the production `audio/durable_capture.rs` directly.
It tests queue draining, independent source tracks, silence/gaps, audio-only
recovery, torn journal tails, corruption retention, and visible queue overflow.

The build script also parses `local_workspace.rs` and compiles its exact
non-command items and tests, omitting only Tauri commands and the two Tauri/app
state imports. Those tests check revision conflicts/history and evidence
requirements for quantities. A separate test parses the modified app source
files with `syn`; it checks syntax only.

Run with `cargo test --manifest-path frontend/src-tauri/capture-core-tests/Cargo.toml --locked`.
The target is intentionally a separate workspace so testing these components
does not initialize devices, download models, or build the app's native engine
dependencies. No microphone or system recording is started.

On the installed Windows GNU Rust toolchain, the available LLVM-Mingw compiler
uses UCRT, while Rust bundles its own GCC/MSVCRT startup and runtime libraries.
The successful isolated-test configuration uses the direct toolchain
`cargo.exe`, `rustc.exe` and `rustdoc.exe`, the LLVM-Mingw `gcc.exe` linker, and:

```text
RUSTFLAGS=-Clink-self-contained=yes -Lnative=<rust-toolchain>/lib/rustlib/x86_64-pc-windows-gnu/lib/self-contained
CARGO_BUILD_JOBS=1
```

This is **not** a full Tauri typecheck, Windows installer build, device test,
long-call timing test, or speaker integration test. The pinned app's ONNX
bundling script currently requires `x86_64-pc-windows-msvc`.
