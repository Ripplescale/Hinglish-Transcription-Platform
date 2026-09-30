# Attribution and licenses

The repository's [MIT license](../LICENSE.md) preserves **Copyright (c) 2024 Zackriya Solutions**. xx is an independent modification of [Meetily Community](https://github.com/Zackriya-Solutions/meetily), based on `v0.4.1`, commit `a2cb62e827da7ef59f65064c97233efb2313878e`. Existing upstream copyright notices are retained. Upstream documents and assets may still show the Meetily name; they do not describe every behavior of xx.

## Speech models

Model licenses are separate from the application license. Weights are downloaded during local setup and are not committed here.

| Model | Source and exact revision | License |
| --- | --- | --- |
| Oriserve Whisper Hindi2Hinglish Apex | [Model card](https://huggingface.co/Oriserve/Whisper-Hindi2Hinglish-Apex), `f3214eed20b4e4d4144e739982d911f87b9cb223` | Apache-2.0 |
| Trelis Whisper Hinglish Preview | [Model card](https://huggingface.co/Trelis/whisper-hinglish-preview), `eab1188fd2d0e91f2584229b32b3bfe1901c896c` | Apache-2.0 |
| pyannote Community-1, optional speakers | [Model card and access conditions](https://huggingface.co/pyannote/speaker-diarization-community-1); the accessed commit is resolved and saved during activation | CC-BY-4.0; gated download requires accepting the publisher's conditions |

Installed artifact manifests retain source identity, revision, hashes, and available license files. Conversion records preserve Apex's source identity and converter provenance. These checks establish which files were used; they do not grant rights beyond the publisher's license or establish accuracy.

## Runtime and build dependencies

The implementation uses [Tauri](https://github.com/tauri-apps/tauri), [Next.js](https://github.com/vercel/next.js), [React](https://github.com/facebook/react), SQLite, [Transformers](https://github.com/huggingface/transformers), [PyTorch](https://github.com/pytorch/pytorch), [pyannote.audio](https://github.com/pyannote/pyannote-audio), and their transitive dependencies. See the committed package/Cargo lockfiles and local Python setup reports for exact installed versions. Each component retains its own license.

Apex uses [whisper.cpp](https://github.com/ggml-org/whisper.cpp) under MIT, pinned to `927cfce34f31707e17f2bff35c349632fb9e2c3a` (`b5130`). Conversion also uses mel filters from [OpenAI Whisper](https://github.com/openai/whisper), MIT, revision `31243bad24cc746f07d4c8bfdd2d974872cb1803`. See [conversion/build notes](../stt_lab/tools/WHISPER_CPP_SETUP.md).

FFmpeg, ONNX Runtime, Vulkan/compiler tools, CPython, and Microsoft runtime components have their own distribution terms. The speaker setup uses a pinned LGPL shared FFmpeg build and retains its notices. Other packaged FFmpeg builds can have different terms; inspect the actual artifacts before redistribution. The app license does not relicense bundled executables or third-party models. A source publication is not a completed binary redistribution-license review.

Claude is an optional external application selected by the user for manual handoff. It is not bundled, operated, or reimplemented by this project.

## Bundled interface fonts

Caveat and DynaPuff are bundled locally so rendering does not require a font CDN.
Their SIL Open Font License notices are retained beside the fonts:
[Caveat](../frontend/public/fonts/Caveat-OFL.txt) and
[DynaPuff](../frontend/public/fonts/DynaPuff-OFL.txt). Those notices cover the font
files separately from the application's MIT license.

## Project interface assets

The xx icon and wordmark are project interface assets. The [workspace screenshot](assets/xx-workspace.png) was captured from the app with fictional demonstration data; it contains no user recording or private transcript. Historical Meetily assets retain their upstream provenance.
