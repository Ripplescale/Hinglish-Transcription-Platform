# Optional local speaker identification

The app's post-call speaker stage uses a separate Python environment. It does not
change Apex or Trelis dependencies, transcript text, or the original recording.

## One-time setup

1. Sign into your Hugging Face account and open the
   [Community-1 model page](https://huggingface.co/pyannote/speaker-diarization-community-1).
   Complete its access form and accept its conditions using that same account.
2. Open [Access Tokens](https://huggingface.co/settings/tokens), choose **Create
   new token**, name it **STT App**, and select **Read**. This download needs no
   write permission. A fine-grained token with read access to the accepted model
   also works.
3. Copy the generated token. In PowerShell, from this project directory, run:

   ```powershell
   .\stt_lab\tools\login_speaker_model.ps1
   ```

   Paste the token only into the terminal's token prompt. Characters may stay
   hidden while pasting. If asked to add it as a Git credential, choose **N**;
   this app downloads through the Hugging Face Python library.
4. Once login succeeds, run:

```powershell
.\stt_lab\tools\setup_speakers.ps1
```

The script downloads and checks the isolated speaker runtime, then enables it in
the app. After setup succeeds, speaker identification can run locally after each
transcript finishes. You do not repeat the access form or login after each call.

**Do not paste the token into this chat, a screenshot, a script, or a command
argument.** The login helper uses Hugging Face's normal local credential store;
setup reports access success or failure without printing the token. If access
fails, confirm the token belongs to the same account that accepted the model's
conditions. Creating a token alone does not grant gated-model access.

Token roles and local login follow the official
[Hugging Face token documentation](https://huggingface.co/docs/hub/security-tokens)
and [CLI login instructions](https://huggingface.co/docs/huggingface_hub/guides/cli#hf-auth-login).

## What setup does

The setup command discovers the same data root and Python executable used by the
app. `-PrepareOnly` installs dependencies and checks audio decoding without model
access. `-DataRoot`, `-AssetRoot`, and `-PythonExe` can override discovery for a
separate installation. The environment and model files must stay outside OneDrive.

Setup never accepts model conditions or asks for a token in command arguments.
Authentication is read by the existing Hugging Face helper from its normal local
cache. No recordings, transcripts, or synthetic test audio are uploaded. Package
downloads and, after access is available, the model download require network access;
processing validation and the installed worker disable network access.

On Windows, login and setup combine certifi with the Windows certificates trusted
for HTTPS in a temporary bundle. Certificate and hostname verification remain
enabled. The bundle exists only for the setup process, and previous CA environment
settings are restored afterward. This handles issuers trusted by Windows but
missing from Python's default bundle. A token-free preflight is available:

```powershell
.\stt_lab\tools\login_speaker_model.ps1 -CheckHttps
```

HTTP 401 is the expected successful HTTPS result for this unauthenticated check.

## Validation and publication

1. Install the pinned CPU packages in `lab/venvs/community1-cpu-py312`.
2. Install a hash-verified FFmpeg shared-library build, retaining its license files.
3. Check `pip check`, package versions, and exact synthetic PCM decoding through
   both TorchCodec and pyannote's file reader.
4. Check local model access and resolve an exact Hugging Face commit.
5. Download that revision and hash all model files.
6. Validate the hashes, load the pipeline offline, and run generated audio through
   it. Activation requires at least 4 GiB available RAM; otherwise the download is
   preserved for a later retry. This checks deployment, not speaker accuracy.
7. Copy a versioned worker and publish `speaker-runtime.json` only after success.

Missing access or failed validation never publishes a new configuration and preserves
any previous working configuration. Readiness, logs, full dependency freeze, public
package download reports, and dependency attribution are stored under
`setup/community1-cpu-py312` in the private asset root. The worker and reconciliation
source hashes are retained in its versioned `source-manifest.json`.

The private environment's `sitecustomize.py` registers FFmpeg's DLL directory and
disables telemetry. It is loaded when the native app directly launches this Python
executable, so the user does not need to edit system PATH.
It also removes relative/empty PATH entries and disables Windows current-directory
executable lookup within this environment. This ensures TorchCodec finds the pinned
shared FFmpeg instead of a relative executable beside the app.

## Pinned runtime

| Component | Version/source |
| --- | --- |
| Python | Windows x64 3.12 |
| pyannote.audio | 4.0.7 |
| torch / torchaudio | 2.10.0+cpu, official PyTorch CPU wheel index |
| TorchCodec | 0.10.0, Windows cp312 wheel |
| FFmpeg | BtbN 8.1.3, LGPL shared build, `autobuild-2026-09-29-13-10` |
| Speaker model | Community-1, exact accessed revision recorded at activation |

Compatibility was checked against the [pyannote dependency declaration](https://raw.githubusercontent.com/pyannote/pyannote-audio/4.0.7/pyproject.toml),
[PyTorch CPU installation instructions](https://pytorch.org/get-started/previous-versions/#v2-10-0),
and [TorchCodec compatibility table](https://github.com/meta-pytorch/torchcodec#compatibility-with-torch-versions).
The [FFmpeg download page](https://ffmpeg.org/download.html) links to BtbN Windows
builds. Community-1 attribution and model conditions are in its
[model card](https://huggingface.co/pyannote/speaker-diarization-community-1).
