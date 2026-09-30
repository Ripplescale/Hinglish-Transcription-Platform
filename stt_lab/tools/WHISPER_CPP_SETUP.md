# Local whisper.cpp benchmark setup

These tools prepare the pinned Apex conversion used by xx. Preparation alone
does not qualify a model for release, prove parity, or measure live latency.

All binaries, source weights, converted weights, logs and runtime manifests belong
outside OneDrive under `%LOCALAPPDATA%\STTApp\lab`. In the Codex MSIX environment,
that path may resolve to the package's `LocalCache\Local\STTApp\lab` directory.
The shorter non-package path is useful for native CMake/Ninja builds.

## Pinned inputs

- whisper.cpp **b5130 / v1.9.4**, commit
  `927cfce34f31707e17f2bff35c349632fb9e2c3a` (MIT).
- Official Windows x64 CPU release archive, SHA-256
  `f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c`.
- OpenAI Whisper mel filters, commit
  `31243bad24cc746f07d4c8bfdd2d974872cb1803` (MIT).
- Portable LLVM-MinGW **20260908 / Clang 23.1.1**, CMake **3.31.8**,
  Ninja **1.13.1**, LunarG Vulkan SDK **1.4.357.0**.
  `prepare_whisper_vulkan.py` pins official URLs and published archive hashes.

Upstream references: [whisper.cpp release](https://github.com/ggml-org/whisper.cpp/releases/tag/b5130),
[converter](https://github.com/ggml-org/whisper.cpp/blob/927cfce34f31707e17f2bff35c349632fb9e2c3a/models/convert-h5-to-ggml.py),
[LLVM-MinGW release](https://github.com/mstorsjo/llvm-mingw/releases/tag/20260908),
[LunarG installation guide](https://vulkan.lunarg.com/doc/view/latest/windows/getting_started.html),
[SDK download/hash API](https://vulkan.lunarg.com/content/view/latest-sdk-version-api).

## CPU runtime and model conversion

From the repository, with the existing isolated Whisper Python environment:

```powershell
$sttLab = Join-Path $env:LOCALAPPDATA 'STTApp\lab'
$sttPython = Join-Path $sttLab 'venvs\whisper\Scripts\python.exe'
$sttRuntime = Join-Path $sttLab 'runtimes\whisper-cpp-b5130'
& $sttPython -B stt_lab/tools/setup_whisper_cpp.py --root $sttRuntime

& $sttPython -B stt_lab/tools/convert_whisper_cpp.py `
  --model (Join-Path $sttLab 'models\apex\f3214eed20b4e4d4144e739982d911f87b9cb223') `
  --runtime $sttRuntime `
  --output (Join-Path $sttLab 'converted\whisper-cpp-b5130\apex') --quantization q5_0
```

Only Apex is needed for the current app. Conversion refuses to overwrite outputs. It verifies every
source manifest file and every pinned runtime artifact, executes the unmodified
official converter with network sockets denied, and retains source hashes,
converter hash/revision, mel-filter provenance, output hashes and quantizer
commands in `.bin.provenance.json` sidecars.

`audit_whisper_conversion.py --model SOURCE --binary OUTPUT.bin --output AUDIT.json`
independently checks model dimensions, all 50,257 serialized base vocabulary
entries, required language/task IDs, and all 1,501 timestamp IDs. Apex has 51,866
total tokens. Stock whisper.cpp reconstructs its standard
added-token IDs. This check does not establish timestamp accuracy.

CPU executable: `runtimes\whisper-cpp-b5130\cpu\Release\whisper-cli.exe`.
Use the same directory's `whisper-quantize.exe` for quantization.

## Optional scoped Vulkan build

The official b5130 release has no Windows Vulkan binary. These commands build the
pinned source using portable tools. They require no compiler/SDK system install
and make no permanent PATH change:

```powershell
$sttVk = Join-Path $sttLab 'vk'
& $sttPython -B stt_lab/tools/prepare_whisper_vulkan.py --root $sttVk
& stt_lab/tools/build_whisper_vulkan.ps1 -Root $sttVk -Step sdk
& stt_lab/tools/build_whisper_vulkan.ps1 -Root $sttVk -Step configure
& stt_lab/tools/build_whisper_vulkan.ps1 -Root $sttVk -Step build -Jobs 2
```

LunarG's documented `copy_only=1` mode copies SDK files without registry,
shortcut, layer registration or system PATH operations. The helper verifies
user/machine PATH, VULKAN_SDK and VK_SDK_PATH remain unchanged afterward. It does
not install drivers. The SDK download uses PowerShell because the official
endpoint returned HTTP 403 to Python urllib; all clients use the same public
pinned artifact, documented `Human=true` download parameter, and expected hash.

The build uses `GGML_VULKAN=ON`, `GGML_NATIVE=ON`, `GGML_OPENMP=OFF`, static project
libraries and initially two Ninja jobs (the benchmark setup resumed with four
after checking free memory). The upstream shader generator can launch its own
workers; do not run sustained timing measurements concurrently. CMake may report
`1.9.4-dev` / an unknown Git revision for the archive checkout; the setup manifest
and archive hash carry the exact source revision instead.

The external `whisper_cpp_libcxx.cmake` hook supplies `<algorithm>` for the
upstream `common` utility target. The pinned `examples/common.cpp` uses
`std::partial_sort` without that direct include, which fails with libc++ 23.
The hook changes a compile option only; no upstream C++, model code, or shader
source is patched. Retain the hook hash with build provenance.

This experimental build additionally compiles only `ggml-vulkan.cpp` (host GPU
dispatch) with `-O1`: Clang 23's `-O3` optimization of that translation unit
exceeded nine CPU minutes. The file's source bytes, GPU shader SPIR-V, embedded
shader C++ objects, CPU kernels and model code retain their original settings.
Any measured speed applies to this explicitly recorded build profile.

## Comparison limits

The accepted Python Oriserve profile uses `language=en`, `task=transcribe`,
five beams and 256 maximum new tokens. CLI beam/fallback/non-speech settings must
be explicit. Stock CLI timestamps and absence of a directly equivalent maximum
token option mean a backend comparison is not a pure precision-only experiment.
Despite its display-only help wording, `--no-timestamps` also sets the decoder's
`wparams.no_timestamps` in pinned `examples/cli/cli.cpp:1252`; use it for the
explicit text-only profile that corresponds to HF `return_timestamps=False`.
Record backend stderr, actual selected device, cold-load cost, and final text.
Do not equate command-process time with warm inference or live partial latency.

On the development laptop, Vulkan detection reported Intel Arc 140V, driver
`32.0.101.8724`, device Vulkan API `1.4.344`. GPU presence does not prove that a
particular inference run used it; retain the actual inference backend log.
