# Contributing to xx

Start with [setup](docs/SETUP.md) and [how it works](docs/HOW_IT_WORKS.md). Current development targets Windows x64. Inherited macOS/Linux files do not establish support for those platforms in this fork.

Keep changes focused and explain behavior, validation, and limitations in a pull request. Preserve the upstream MIT license and copyright. Changes to model revisions, decoder prefixes, quantization, chunk boundaries, or speaker reconciliation require explicit provenance and suitable checks.

## Local checks

From the repository root, using Python 3.12 with the worker dependencies installed:

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'stt_lab'
python -B -m unittest discover -s stt_lab/tests
```

From `frontend`:

```powershell
pnpm install --frozen-lockfile
pnpm exec tsc --noEmit
pnpm build
node --test tests/lib/local-transcription-workflow.test.cjs tests/lib/transcript-workspace.test.cjs
```

Browser QA scripts under `frontend/tests/ui` use a mocked local bridge. They check interface behavior without uploading audio or opening a real Claude session. Inspect each script's prerequisites before running it; a browser check does not qualify native capture or model quality.

The isolated Rust suite tests production capture/workspace code without opening devices:

```powershell
cargo test --manifest-path frontend/src-tauri/capture-core-tests/Cargo.toml --locked
```

See its [toolchain notes](frontend/src-tauri/capture-core-tests/README.md) and the [Windows build guide](docs/SETUP.md#building-the-windows-application). A component test is not a full native build. Use the MSVC target for the packaged app.

## Data and model changes

Use synthetic fixtures. Do not commit private audio, transcripts, corrections, meeting names, exported reviews with embedded audio, screenshots of real meetings, logs, model weights, runtime pointers, or access tokens. Keep them outside the checkout. `.gitignore` does not protect files already tracked by Git.

`node scripts/audit-publication.cjs` scans candidate files, exact staged Git index contents, and reachable history for selected credential/path patterns. Redirect its path-only report to a private location. Review findings and binary provenance before publication; the heuristic scan is not a complete privacy or secret audit.

Compare recognition and display script separately. Preserve Trelis's native mixed script. Do not rank Roman and Devanagari transcripts by an incompatible word-error calculation, equate longer output with truth, or describe batch runtime as live latency. Private benchmark references remain private when the implementation is shared.

## Releases

The installer currently references a separately prepared local runtime. A release for another computer needs a tested setup path, runtime/model license notices, checksums, and clean-machine validation. Publishing source does not imply a universally qualified Windows package. Keep inherited automated release workflows disabled until adapted and reviewed for this fork.
