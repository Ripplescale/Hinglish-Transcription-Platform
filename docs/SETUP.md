# Windows setup for xx

xx is currently a Windows x64 application with a separately prepared local model runtime. **The existing installer is for the already configured laptop.** It does not yet provide a complete, tested first-run installation on an arbitrary PC. This guide separates that installation from the developer setup needed elsewhere.

## Using an existing installation

Run the locally supplied xx setup program, then open **xx** from Start. Close the previous app normally before opening xx: the renamed installer can coexist with it, and both use the same workspace. Opening xx does not start recording.

The install includes the desktop application, FFmpeg, and required Microsoft C++ runtime files. It reuses the existing local models and Python environments. Its adjacent `sttapp-local-install.json` points to the data root; do not commit this machine-specific file.

1. Check the local runtime status, microphone, and system-audio device.
2. Choose the call language. New recordings use **Apex · 20 seconds** for a Roman Hinglish **Live Draft**, followed automatically by **Trelis · 20 seconds** for the original-script **Final** after stopping.
3. Record a short test. Stop, let Apex finish its current window and exit, then let Trelis process the saved audio. Check playback and the transcript before relying on a longer call.
4. Review the Final and edit text or speakers where needed. “Final” is an unreviewed machine transcript until you check it. Copy/export into Claude only when you want to review and send it yourself.

The Live Draft is for following the conversation and may be incomplete. Its saved output remains available alongside the Final. Corrections and notes stay with the version you edited; they are not silently copied between models. Additional manual versions remain available through Tools.

One heavy worker runs at a time. Back-to-back calls keep recording while earlier work finishes; a delayed Live Draft can be skipped once that call stops, and its Final can still use the saved audio. Pausing a worker does not stop capture, and a paused or failed Final needs an explicit retry. Existing legacy versions remain unchanged.

This unsigned local installer does not use Meetily's upstream updater. Trelis on the current CPU setup runs after the call. Optional speaker setup is described below.

## Developer prerequisites

Use PowerShell 7, Git, Node.js 22, pnpm, and Windows x64 CPython 3.12. The private Python-copy helper is pinned to the tested **3.12.14** base. Build the native app with Rust's **MSVC** target, Microsoft C++ Build Tools and a Windows SDK; the bundled ONNX build step does not support the GNU target.

Use a local data directory outside OneDrive. The tested machine has 32 GB RAM and an Intel Vulkan-capable GPU; that is a description of the tested configuration, not an established minimum requirement. Leave headroom for the calling app and run only one heavy model job at a time. Dependency/model setup needs the network and substantial disk space; installed transcription can run offline.

```powershell
git clone https://github.com/Ripplescale/Hinglish-Transcription-Platform.git
Set-Location Hinglish-Transcription-Platform
$sttRoot = Join-Path $env:LOCALAPPDATA 'STTApp'
$sttLab = Join-Path $sttRoot 'lab'
```

The repository is private; cloning requires an authorized GitHub account. No private recordings or reference transcripts are required to install the worker.

## Download the two ASR models

Set `$basePython` to your existing CPython 3.12 executable. The following helper creates an isolated environment; it does not install a system Python:

```powershell
$basePython = 'C:\Python312\python.exe' # replace with your actual installation
.\stt_lab\tools\bootstrap_whisper.ps1 -Python $basePython
$sttPython = Join-Path $sttLab 'venvs\whisper\Scripts\python.exe'
& $sttPython -B stt_lab/tools/model_assets.py fetch apex
& $sttPython -B stt_lab/tools/model_assets.py fetch trelis
& $sttPython -B stt_lab/tools/model_assets.py verify apex
& $sttPython -B stt_lab/tools/model_assets.py verify trelis
```

The bootstrap includes the pinned SciPy resampling dependency used by the capture worker. The model helper uses exact revisions from [`models.json`](../stt_lab/models.json), retains license/source metadata, and writes file hashes. No Qwen model is downloaded. The default download location is `%LOCALAPPDATA%\STTApp\lab`; changing `STTAPP_DATA_DIR` alone does not redirect these download helpers.

## Prepare Apex's local engine

Follow [the pinned whisper.cpp setup](../stt_lab/tools/WHISPER_CPP_SETUP.md) to download the CPU converter/runtime, convert Apex to Q5_0, and build the Vulkan CLI. Retain the generated `.bin.provenance.json` sidecars. The Vulkan build uses portable, pinned tools; it does not install a graphics driver.

The expected paths below follow that guide. Use your actual verified executable if you chose another build directory. A successful build alone does not prove that inference uses the GPU; inspect its runtime log.

## Configure and install the ASR worker

Create a private configuration directory. The installer's historical `--study` argument means a directory containing `configs/apex.json` and `configs/trelis.json`; it does not require a benchmark or private call data.

```powershell
$sttConfig = Join-Path $sttRoot 'setup\asr'
$sttWork = Join-Path $sttLab 'work'
New-Item -ItemType Directory -Force (Join-Path $sttConfig 'configs'), $sttWork | Out-Null
$apex = @{
  backend = 'whisper_cpp'
  artifact_path = (Join-Path $sttLab 'converted\whisper-cpp-b5130\apex\ggml-model-q5_0.bin')
  executable = (Join-Path $sttLab 'vk\build\bin\whisper-cli.exe')
  work_dir = $sttWork
  device = 'vulkan'; threads = 4; num_beams = 5; timestamps = $false
}
$trelis = @{
  backend = 'transformers'
  artifact_path = (Join-Path $sttLab 'models\trelis\eab1188fd2d0e91f2584229b32b3bfe1901c896c')
  device = 'cpu'; dtype = 'float32'; threads = 4
  num_beams = 1; max_new_tokens = 440; timestamps = $false
}
$utf8 = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText((Join-Path $sttConfig 'configs\apex.json'), ($apex | ConvertTo-Json), $utf8)
[IO.File]::WriteAllText((Join-Path $sttConfig 'configs\trelis.json'), ($trelis | ConvertTo-Json), $utf8)
& $sttPython -B stt_lab/tools/install_local_worker.py `
  --data-root $sttRoot --study $sttConfig --python $sttPython
```

This writes a content-addressed copy of the worker and `runtime.json`. It does not copy recordings or download weights. Paths and asset provenance are validated during inference. Keep the original checkpoint and conversion records outside Git. Test a short local recording before claiming deployment success.

## Optional speaker identification

Follow [Community-1 setup](../stt_lab/SPEAKER_SETUP.md). Accept access conditions on the model publisher's page using your own Hugging Face account, then authenticate through the local token prompt:

```powershell
.\stt_lab\tools\login_speaker_model.ps1
.\stt_lab\tools\setup_speakers.ps1 -DataRoot $sttRoot
```

Do not put the token in a command argument, repository file, chat, or screenshot. A read token does not grant access until the account has accepted the model conditions. The helper uses an isolated environment, verifies downloaded artifacts, and performs an offline deployment check before publishing `speaker-runtime.json`. Speaker accuracy still needs listening-based validation. Omitting this optional setup does not prevent ASR.

## Building the Windows application

From `frontend`, install the locked dependencies and build the interface:

```powershell
pnpm install --frozen-lockfile
pnpm exec tsc --noEmit
pnpm build
```

The current packaging helper, [`build-local-windows.ps1`](../frontend/src-tauri/scripts/build-local-windows.ps1), expects the prepared `runtime.json`, an isolated MSVC Rust toolchain under `lab/rust`, a libclang distribution under `lab/windows-build`, and Microsoft Build Tools. Its prerequisites are not bootstrapped by the script; inspect its checks before using it on a new PC. From the repository root, after preparing them:

```powershell
.\frontend\src-tauri\scripts\build-local-windows.ps1 `
  -Mode Package -DataRoot $sttRoot -LabRoot $sttLab `
  -BuildToolsPath 'C:\BuildTools-STT' -TargetRoot 'C:\STTBuild\target'
```

Those short build paths are examples; choose writable locations for your installation. The helper creates a machine-specific runtime pointer and bundles verified runtime DLLs. Review licenses before distributing a binary. Inherited upstream CI/release recipes are not a supported xx release pipeline.

An optional [`Install-PrivatePythonBase.ps1`](../frontend/src-tauri/scripts/Install-PrivatePythonBase.ps1) copies and verifies an existing CPython **3.12.14** base and can rebind the prepared environments. Supply `-SourceBase` explicitly. It does not download Python; stop worker processes before using its activation option.

The bundled [`Start-xx.ps1`](../frontend/src-tauri/scripts/Start-xx.ps1) resolves the local pointer and launches the GUI. When running the source copy, pass `-AppPath` pointing to the built `xx.exe`; its default expects the script beside the executable.

## Storage and backups

Data-root discovery uses `STTAPP_DATA_DIR`, then `sttapp-local-install.json` beside the executable, then `%LOCALAPPDATA%\STTApp`. The database, recordings, worker/model assets, corrections, and notes must remain outside the source checkout.

Back up the full root while recording and workers are stopped. Earlier local installations can point into a Windows app package's cache. Resetting or uninstalling the host app can remove such data; inspect the pointer and back up before doing either. Normal inference uses the verified private Python base, not the host app's separate managed interpreter.

Moving data requires updating absolute recording/model/worker/environment paths and verifying history before retiring the old copy. Setting a new environment variable is not a migration. This installer does not move or delete existing recordings.

## Troubleshooting boundaries

- **Runtime unavailable:** verify the data pointer, `runtime.json`, Python executable, and model/configuration paths.
- **Speaker access denied:** use the same account for the access form and local login; preserve a working configuration while retrying setup.
- **Trelis queues slowly:** keep it after-call and avoid concurrent memory-heavy jobs. It is not currently a live CPU profile.
- **Missing speech:** check the saved track and input levels, then inspect flags/retry alternatives. Re-running cannot restore missing captured audio.
- **Build failure on GNU Rust:** use the MSVC toolchain required by the pinned native ONNX packaging step.

See [privacy](../PRIVACY_POLICY.md), [architecture](HOW_IT_WORKS.md), and [contribution checks](../CONTRIBUTING.md) for the limits of the current implementation.
