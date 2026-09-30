# Optional post-call speaker identification

Community-1 runs after a completed STT job. Its first setup requires accepting
the model's Hugging Face conditions and authenticating locally. Recording and
transcription remain available if speaker setup is incomplete.

Use a **separate Python environment**, never the Apex/Trelis environment. Install
`pyannote.audio` 4.x and its supported PyTorch/torchcodec dependencies there,
record the exact resolved package versions, and validate audio decoding before
enabling the runtime. Windows torchcodec may require matching FFmpeg libraries.
No package or model installation is performed by the worker.

1. Accept the conditions at https://huggingface.co/pyannote/speaker-diarization-community-1.
2. Run `python -m huggingface_hub.cli.hf auth login` yourself using the chosen
   environment's Python (or `tools/login_speaker_model.ps1 -PythonExe <absolute-python.exe>`).
   This avoids Windows executable wrappers that still point at an old environment.
   Do not paste a token
   into chat, configuration files or command arguments.
3. Run `python tools/prepare_speaker_model.py --check-access`. It prints access
   status and the resolved revision, never credentials.
4. Run the helper with `--revision <that-40-character-revision> --output <private-model-directory>`.
   It downloads only model files and writes a hash manifest. It never reads audio.
5. Save `speaker-runtime.json` in the native app's local STTApp data root:

```json
{
  "version": 1,
  "enabled": true,
  "python_executable": "<isolated-environment>/Scripts/python.exe",
  "worker_script": "<private-runtime>/speaker_worker_launcher.py",
  "model_path": "<private-model-directory>",
  "model_revision": "<40-character-revision>",
  "device": "cpu",
  "threads": 4
}
```

The launcher adds its installed package root to `sys.path`, then runs
`sttbench.speaker_worker` as `__main__`. Copy the module and
`speaker_reconciliation.py` into that private versioned runtime. The worker CLI
is `--config <speaker-runtime.json> --request <speaker-jobs/id/request.json>`.

The request contains `job_id`, `meeting_id`, `session_dir`,
`transcription_job_id`, `transcription_status_path`, and optional
`previous_speakers_path`. The previous sidecar is the prior completed `result`
with current manual display names merged in. Native supervision serializes STT
and speaker inference on this laptop.

`status.json` progresses through validating, loading_model, diarizing,
reconciling, then complete or failed. Its immutable source hashes bind the audio,
transcript status, request and model manifest. Completed results retain regular
speaker turns including overlap; they do not use exclusive diarization to erase
simultaneous speech.

ASR currently supplies 20-second windows, not word alignment. Each window gets
candidate speaker IDs and intersecting turn IDs, while `speaker_id` remains
null. A long majority-speaker turn does not establish who spoke every word.
Names are manual labels attached to stable application IDs. Reruns reuse an ID
only for strong, unambiguous mutual temporal overlap. Ambiguous merges/splits get
new IDs; previous names remain available on inactive IDs for review.

Pure synthetic verification: `python -m unittest discover -s tests -p test_speaker_reconciliation.py`.
This verifies reconciliation behavior, not diarization accuracy or Windows model
inference. Real setup, held-out speaker checks, latency and audio decoding must
pass before claiming speaker identification works on this laptop. Live diart is
deferred until recording plus STT has measured spare resources.

Sources: [Community-1 local/offline API and CC-BY-4.0 attribution](https://huggingface.co/pyannote/speaker-diarization-community-1),
[Hugging Face access check](https://huggingface.co/docs/huggingface_hub/package_reference/hf_api#huggingface_hub.HfApi.auth_check).
