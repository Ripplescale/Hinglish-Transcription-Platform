"""Run local checkpoints on the tiny publisher example; never score accuracy.

Requires the isolated Whisper environment and explicit model downloads first.
Python socket connections are blocked for the duration of inference. This is a
smoke check, not evidence of sustained live performance or call accuracy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.runtime import reset_runtime_cache, transcribe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", choices=("apex", "trelis"), default=["apex", "trelis"])
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads must be positive")
    import numpy as np
    import soundfile as sf

    private = Path(os.environ["LOCALAPPDATA"]) / "STTApp" / "lab"
    registry = json.loads((LAB / "models.json").read_text(encoding="utf-8"))
    models = {m["id"]: m for m in registry["models"]}
    apex = private / "models" / "apex" / models["apex"]["source_revision"]
    source = apex / "audios" / "f5e0178c-354c-40c9-b3a7-687c86240a77_1_1152496_1175488.wav"
    samples, rate = sf.read(source, dtype="float32")
    if rate != 16000 or samples.ndim != 1 or not np.all(np.isfinite(samples)):
        raise ValueError("Expected the mono 16 kHz publisher example; no implicit resampling is performed.")
    if np.max(np.abs(samples)) > 1:
        raise ValueError("Publisher audio requires clipping; review the input before testing.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = private / "smoke" / stamp
    output.mkdir(parents=True, exist_ok=False)
    audio = output / "publisher-example-pcm16.wav"
    sf.write(audio, samples, rate, subtype="PCM_16")
    report = {
        "version": 1, "created_at": stamp, "purpose": "local_inference_smoke_only",
        "human_verified_reference": False, "qualifies_model": False,
        "live_performance_measured": False, "audio_seconds": len(samples) / rate,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "pcm16_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        "preprocessing": "soundfile: mono 16000Hz float32 WAV to PCM16; no resampling",
        "python_network_attempts": [], "runs": [],
    }
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex
    original_create = socket.create_connection

    def denied(*_args, **_kwargs):
        report["python_network_attempts"].append("blocked socket connection")
        raise OSError("Network disabled during local inference smoke test")

    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    try:
        for name in args.models:
            spec = dict(models[name])
            spec["artifact_path"] = str(private / "models" / name / spec["source_revision"])
            reset_runtime_cache()
            for phase in ("cold", "warm"):
                result = transcribe(spec, audio, {"backend": "transformers", "device": "cpu",
                    "dtype": "float32", "threads": args.threads, "timestamps": False})
                row = {"model_id": name, "phase": phase, **result}
                report["runs"].append(row)
                print(json.dumps({key: row.get(key) for key in ("model_id", "phase", "status", "timing", "error")}), flush=True)
                if result["status"] != "ok":
                    break
    finally:
        socket.socket.connect, socket.socket.connect_ex = original_connect, original_connect_ex
        socket.create_connection = original_create
        (output / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Private smoke report: {output / 'results.json'}", flush=True)
    return 0 if report["runs"] and all(r["status"] == "ok" for r in report["runs"]) and not report["python_network_attempts"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
