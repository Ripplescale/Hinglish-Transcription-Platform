# Local runtime adapters

```python
from pathlib import Path
from sttbench.runtime import transcribe

result = transcribe(model_spec, Path("clip.wav"), {
    "backend": "transformers",
    "artifact_path": r"C:\local-models\apex\pinned-revision",
    "device": "cpu",
    "dtype": "float32",
    "threads": 4,
})
```

`model_spec` comes from the model registry: `model_id`, `repo_id`, `family`,
`source_revision`, `decoding`, and optional `artifact_path`. Only existing local
paths are loaded. No adapter downloads weights or sends audio to an API.
Inference dependencies are optional and imported lazily.

Input is a nonempty mono, 16 kHz, 16-bit PCM WAV of at most 30 seconds. Prepare
clips before calling the runtime; it rejects other formats or truncated frames.
This is a **batch qualification adapter**, not a live microphone service.

The result always includes `status` (`ok`, `failed`, or `unavailable`), `text`,
`segments`, `timing`, `provenance`, and `warnings`; failures include
`error: {code, message}` and empty text/segments. Segments have `start`, `end`
(seconds), and `text`. An empty segments list means no verified timestamps were
produced. No first-partial latency or speaker labels are inferred.

## Backend contracts

| Backend | Behavior |
|---|---|
| `transformers` | Safetensors-only Whisper loading. Oriserve uses `transcribe/en`, five beams and 256 new tokens, matching the documented pipeline with Transformers 4.56. Trelis preserves the complete saved-tokenizer encoding of `<|mixedcode|>` in its explicit decoder prefix and defaults to 440 new tokens. Other Whisper models use registry decoding, with automatic language detection when unspecified. |
| `whisper_cpp` | Calls an existing local CLI with an argument vector. CPU adds `--no-gpu`; Vulkan requests GPU use, which still needs independent verification from the actual build. Trelis's custom prefix is unsupported by this adapter. |

Python adapters accept `cpu`, `cuda[:0]`, or `xpu[:0]` only when the installed
Torch build exposes that device. This is capability handling, not evidence that
Intel XPU acceleration works on the target laptop. CPU float16 is rejected.

`reuse_model` defaults to true. Cache keys include model path/revision and
device/dtype . `reset_runtime_cache()` drops model
references. Generation settings are recorded in provenance. Calls serialize
inside one process because models and offline environment settings are shared.
`decoding.num_beams` and `decoding.max_new_tokens` select the registry profile;
explicit runtime values override them. Effective values are recorded under
`provenance.effective_generation`. The token limit is capped by the model's
actual decoder capacity after its complete prefix. Saved suppression-token
settings are preserved.

`load_seconds` and `inference_seconds` are separate for Python backends.
`verification_seconds` includes artifact checks. `total_seconds` includes all
work. A CLI invocation includes model loading, so its interval is recorded as
`process_seconds`; pure inference/load times remain null. Batch timings are not
live latency results, even when a model runs faster than the clip duration.

## Asset verification

`verify_model_assets(spec, backend)` checks required local files, nonempty
safetensors/shards, and optional `asset_sha256` mappings. When present,
`artifact-manifest.json` must match the registry's model ID, repository and
revision, list every loaded weight file, and declare `files` entries with
`path`, `size`, and `sha256`. All declared file sizes and hashes are checked.
The revision remains a matched declaration; a file hash does not independently
prove publisher identity. Without expected hashes the result explicitly says
`not_hash_verified`.

Hash results may be reused only within one process while path, size, and
nanosecond modification time remain unchanged. Restart to force rehashing;
this cache is not a tamper-proof store.

For `whisper_cpp`, configure `executable`, an existing workspace-local
`work_dir`, and a converted binary through `model_path` or `artifact_path`.
The default sidecar is `<binary>.provenance.json`; override with `sidecar_path`.
It must contain:

```json
{
  "source_repo_id": "Oriserve/Whisper-Hindi2Hinglish-Apex",
  "source_revision": "the exact registry revision",
  "source_sha256": {"model.safetensors": "64 hexadecimal characters"},
  "converter": {"name": "the converter used", "revision": "its pinned commit"},
  "quantization": "the actual quantization setting",
  "output_sha256": "64 hexadecimal characters"
}
```

The binary hash and source identity/revision are checked before execution.
Source hash declarations are retained but not independently revalidated against
source weights by this conversion check. No conversion or download is hidden
inside the inference call.

## Evidence boundaries

`inspect_capabilities()` checks installed package metadata without importing
Torch or loading models. Dependency presence does not prove successful model
inference. Unit tests use fake inference to verify prefix, offline-loading,
cache, CLI argument, timestamp and provenance contracts; they do not measure
accuracy or speed. Real clips and real installed backends are required for
qualification.
