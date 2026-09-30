"""Prepare and transcribe sparse local samples, without claiming benchmark accuracy."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import wave

LAB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB))
from sttbench.manifest import file_digest, write_json
from sttbench.runtime import transcribe


def process(command):
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def prepare(sources, output):
    import imageio_ffmpeg
    import numpy as np
    if output.exists():
        raise ValueError("Choose a new output directory; previous screening is never overwritten")
    sources = [source.resolve(strict=True) for source in sources]
    if any(not source.is_file() for source in sources):
        raise ValueError("Every source must be an existing local file")
    hashes = [file_digest(source) for source in sources]
    if len(set(hashes)) != len(hashes):
        raise ValueError("Duplicate recordings supplied")
    output.mkdir(parents=True)
    executable = imageio_ffmpeg.get_ffmpeg_exe()
    version = process([executable, "-version"]).stdout.splitlines()[0]
    document = {"version": 1, "purpose": "exploratory_language_screening_not_accuracy_evaluation",
        "created_at": datetime.now(timezone.utc).isoformat(), "decoder": version,
        "human_reviewed": False, "calls": [], "clips": []}
    for index, (source, source_hash) in enumerate(zip(sources, hashes), 1):
        call_id = f"call-{index:02d}"
        folder = output / call_id
        folder.mkdir()
        original = folder / ("original" + source.suffix.lower())
        shutil.copyfile(source, original)
        if file_digest(original) != source_hash:
            raise ValueError("Copy hash does not match original")
        probe = process([executable, "-hide_banner", "-i", str(original)])
        found = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr)
        if not found:
            raise ValueError(f"Could not read recording duration: {source.name}")
        duration = int(found[1]) * 3600 + int(found[2]) * 60 + float(found[3])
        streams = [line.strip() for line in probe.stderr.splitlines() if "Audio:" in line]
        if len(streams) != 1:
            raise ValueError("Review separate audio streams before choosing which stream to screen")
        call = {"id": call_id, "title": source.parent.name, "source_path": str(source),
                "original": original.relative_to(output).as_posix(), "source_sha256": source_hash,
                "audio_seconds": duration, "stream_info": streams}
        document["calls"].append(call)
        # Full short recordings; spaced, deterministic 25s windows on longer calls.
        if duration <= 90:
            starts = list(range(0, int(duration), 30))
            windows = [(float(start), min(30., duration - start)) for start in starts if duration - start >= .1]
        else:
            windows = [(round(max(0., duration * fraction - 12.5), 3), 25.) for fraction in (.15, .50, .85)]
        for clip_index, (start, length) in enumerate(windows, 1):
            clip_id = f"{call_id}-{clip_index:02d}"
            clip = folder / f"sample-{clip_index:02d}.wav"
            result = process([executable, "-nostdin", "-v", "error", "-ss", str(start), "-i", str(original),
                "-t", str(length), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(clip)])
            if result.returncode:
                raise ValueError(f"Could not extract {clip_id}: {result.stderr[-1000:]}")
            with wave.open(str(clip), "rb") as audio:
                frames = audio.getnframes()
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                    raise ValueError("Unexpected decoder output format")
                raw = audio.readframes(frames)
                if not frames or len(raw) != frames * 2:
                    raise ValueError("Truncated or empty decoded clip")
            data = np.frombuffer(raw, dtype="<i2").astype("float64") / 32768
            document["clips"].append({"id": clip_id, "call_id": call_id,
                "audio": clip.relative_to(output).as_posix(), "start_seconds": start,
                "duration_seconds": frames / 16000, "audio_sha256": file_digest(clip),
                "rms": float(np.sqrt(np.mean(data * data))), "peak": float(np.max(np.abs(data)))})
        print(json.dumps({"call_id": call_id, "duration_seconds": duration, "samples": len(windows)}), flush=True)
    write_json(output / "screening.json", document)
    print(f"Screening manifest: {output / 'screening.json'}", flush=True)


def infer(path, model_id, clips, english_clips=None):
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("purpose") != "exploratory_language_screening_not_accuracy_evaluation":
        raise ValueError("Expected an exploratory screening manifest")
    registry = json.loads((LAB / "models.json").read_text(encoding="utf-8"))
    spec = dict(next(m for m in registry["models"] if m["id"] == model_id))
    spec["artifact_path"] = str(Path(os.environ["LOCALAPPDATA"]) / "STTApp/lab/models" / model_id / spec["source_revision"])
    config = {"backend": "qwen_asr" if spec["family"] == "qwen_asr" else "transformers", "device": "cpu", "dtype": "float32", "threads": 4, "timestamps": False}
    english_clips = sorted(set(english_clips or []))
    if english_clips and model_id != "trelis":
        raise ValueError("Explicit English clip prefixes are supported here only for Trelis")
    output = path.parent / "results" / f"{model_id}.json"
    report = {"version": 1, "model_id": model_id, "model_spec": spec, "runtime_config": config,
              "screening_sha256": file_digest(path), "clips": {}, "network_attempts": []}
    versions = {}
    for package in ("torch", "transformers", "accelerate", "numpy", "qwen-asr"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    report["execution_environment"] = {"python": sys.version, "packages": versions,
        "code_sha256": {name: file_digest(LAB / name) for name in (
            "tools/screen_recordings.py", "sttbench/runtime/api.py", "sttbench/runtime/assets.py")}}
    if english_clips:
        report["clip_decoding"] = {sample_id: {"task": "transcribe", "language": "en", "mixed_code": False}
                                   for sample_id in english_clips}
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if any(previous.get(key) != report[key] for key in ("screening_sha256", "model_spec", "runtime_config")):
            raise ValueError("Previous results have different inputs/settings; refusing silent reuse")
        if previous.get("clip_decoding", {}) != report.get("clip_decoding", {}):
            raise ValueError("Previous results have different per-clip decoding settings")
        report = previous
    selected = [clip for clip in document["clips"] if not clips or clip["id"] in clips]
    if clips and set(clips) != {clip["id"] for clip in selected}:
        raise ValueError("An unknown clip id was requested")
    if not set(english_clips).issubset(clip["id"] for clip in selected):
        raise ValueError("English-prefix clips must be in the selected screening clips")
    connect, connect_ex, create = socket.socket.connect, socket.socket.connect_ex, socket.create_connection

    def denied(*_args, **_kwargs):
        report["network_attempts"].append("blocked Python socket connection")
        raise OSError("Network disabled during private recording transcription")

    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = denied
    try:
        for index, clip in enumerate(selected, 1):
            audio = (path.parent / clip["audio"]).resolve()
            if not audio.is_relative_to(path.parent.resolve()) or file_digest(audio) != clip["audio_sha256"]:
                raise ValueError("Clip location or audio hash changed")
            if report["clips"].get(clip["id"], {}).get("status") == "ok":
                continue
            print(f"[{index}/{len(selected)}] {model_id}: {clip['id']} starting", flush=True)
            sample_spec = {**spec, "decoding": {**spec.get("decoding", {}), **report.get("clip_decoding", {}).get(clip["id"], {})}}
            result = transcribe(sample_spec, audio, config)
            result["audio_sha256"] = clip["audio_sha256"]
            report["clips"][clip["id"]] = result
            write_json(output, report)
            print(json.dumps({"model": model_id, "clip": clip["id"], "status": result["status"],
                              "seconds": result["timing"]["total_seconds"], "error": result.get("error")}), flush=True)
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = connect, connect_ex, create
        write_json(output, report)
    return 0 if all(report["clips"][clip["id"]]["status"] == "ok" for clip in selected) and not report["network_attempts"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    intake = commands.add_parser("prepare")
    intake.add_argument("sources", nargs="+", type=Path)
    intake.add_argument("--output", type=Path, required=True)
    inference = commands.add_parser("transcribe")
    inference.add_argument("--screening", type=Path, required=True)
    inference.add_argument("--model", choices=("apex", "trelis"), required=True)
    inference.add_argument("--clips", nargs="*")
    inference.add_argument("--english-clips", nargs="*", help="Trelis clips identified as English before inference; use its documented en prefix without mixedcode")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.sources, args.output.resolve())
        return 0
    return infer(args.screening.resolve(), args.model, args.clips, args.english_clips)


if __name__ == "__main__":
    raise SystemExit(main())
