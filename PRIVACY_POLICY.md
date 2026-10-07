# Privacy in oats

oats records and transcribes calls locally on Windows. This document describes the local workflow in this fork, not upstream Meetily releases or external services.

## Local storage

The private data root holds microphone/system recordings, the meeting database, transcription jobs, original model output, correction history, notes, speaker assignments, optional Vault entries and sources, and operational logs. Derived audio windows and checkpoints can remain for recovery and reproducibility.

The recommended location is `%LOCALAPPDATA%\STTApp`, outside the checkout and OneDrive. Existing installations can use another local path recorded in `sttapp-local-install.json` beside the executable. `STTAPP_DATA_DIR` overrides discovery. See [storage and migration](docs/SETUP.md#storage-and-backups) before changing a populated installation's path.

The app does not claim to encrypt its database or recordings itself. Protection depends on Windows permissions and any device encryption or backup services you configure. A local path does not prevent a separately configured backup service from copying it.

## Network use

Installed ASR and speaker workers load local files and use offline settings. The local interface disables the inherited analytics/cloud-summary workflow and upstream automatic updating. Recordings are not automatically sent to a transcription API, summary API, or model publisher.

Setup uses the network for dependencies, models, and build tools. Download servers receive normal request metadata. Optional Community-1 setup requires accepting the model's conditions and signing in to Hugging Face through its local CLI; the token stays in that tool's credential store. Setup validates audio processing locally and does not upload recordings.

Some upstream provider code remains in the repository for provenance and compatibility. Its presence does not mean arbitrary upstream modes share this local workflow's behavior.

## Manual Claude handoff and exports

Copying puts the transcript on the Windows clipboard. Copy-and-open opens Claude Desktop; file handoff creates a local TXT attachment. **You review and send it yourself.** No Claude API key is required and oats does not automatically submit the transcript. Content you send in Claude is handled by Claude and the relevant account's service settings.

Clipboard history, exports, and shared files are additional copies outside the app's local store.

## Retention and recovery

Audio is saved before inference so a model failure does not discard the call. Both Apex's Live Draft and Trelis's Final are retained. Corrections and notes belong to the version you edited, and previous revisions remain available. A visible edit therefore does not erase all earlier text. Small workflow pointers also use the app's local webview storage; transcript text remains in the durable worker/database stores.

There is no automatic retention period. Uninstalling the app does not guarantee removal of separately stored data, exports, model caches, credentials, or backups. Inspect and back up the data root when migrating or retiring an installation. Do not delete it during recording or active work.

Use synthetic examples for bug reports where possible. Remove private content from any logs or excerpts you share. Contribution and publication checks deliberately exclude recordings, private reviews, and runtime configuration.
