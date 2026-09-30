"""Local, sequential, resumable benchmark execution."""
from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path

from .manifest import digest, read_json, read_jsonl, write_json
from .scoring import corpus_digest


def select_model(registry: dict, model_id: str) -> dict:
    candidates = registry.get("models", registry)
    if isinstance(candidates, list):
        matches = [item for item in candidates if item.get("id", item.get("model_id")) == model_id]
    elif isinstance(candidates, dict):
        matches = [dict(candidates[model_id], id=model_id)] if model_id in candidates else []
    else:
        matches = []
    if len(matches) != 1:
        raise ValueError(f"Registry must contain exactly one model {model_id!r}")
    spec = matches[0]
    revision = spec.get("source_revision")
    if not isinstance(revision, str) or not revision.strip() or revision.lower() in ("main", "master", "latest", "head"):
        raise ValueError("Model source_revision must identify a pinned revision, not a mutable branch or empty value")
    if not spec.get("license"):
        raise ValueError("Model registry must declare a license")
    return spec


def _write_hypotheses(path: Path, rows: list[dict]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def run(manifest: dict, model_spec: dict, runtime_config: dict, output: Path,
        split: str = "dev", model_path: Path | None = None, transcriber=None) -> dict:
    config = dict(runtime_config)
    if model_path:
        config["artifact_path"] = str(model_path.resolve())
    elif not config.get("artifact_path"):
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise ValueError("LOCALAPPDATA is unavailable; supply --model-path or runtime_config.artifact_path")
        config["artifact_path"] = str(Path(local) / "STTApp" / "lab" / "models" /
                                      model_spec.get("id", model_spec.get("model_id")) / model_spec["source_revision"])
    identity = {"model_spec": model_spec, "runtime_config": config}
    config_sha = digest(identity)
    metadata = {"version": 1, "kind": "sttbench_run", "manifest_sha256": manifest["manifest_sha256"],
                "input_manifest_sha256": manifest["input_manifest_sha256"],
                "corpus_sha256": corpus_digest(manifest), "config_sha256": config_sha,
                "split": split, **identity, "created_unix_seconds": time.time()}
    selected = [s for s in manifest["samples"] if split == "all" or s["split"] == split]
    if not selected:
        raise ValueError(f"No samples for split {split}")
    if not metadata["corpus_sha256"]:
        raise ValueError("Execution requires verified local audio")
    output.mkdir(parents=True, exist_ok=True)
    metadata_path, hypotheses_path = output / "run.json", output / "hypotheses.jsonl"
    if metadata_path.exists():
        previous = read_json(metadata_path)
        if any(previous.get(key) != metadata[key] for key in ("manifest_sha256", "corpus_sha256", "config_sha256", "split")):
            raise ValueError("Output directory belongs to another corpus, configuration, or split; choose a new directory")
        metadata = previous
    elif hypotheses_path.exists():
        raise ValueError("Existing hypotheses have no run.json; refuse to overwrite unowned output")
    else:
        write_json(metadata_path, metadata)
    records = read_jsonl(hypotheses_path) if hypotheses_path.exists() else []
    completed = {}
    allowed = {s["id"] for s in selected}
    for row in records:
        sample_id = row.get("sample_id")
        if sample_id not in allowed or sample_id in completed:
            raise ValueError("Existing output contains unknown or duplicate sample IDs")
        if row.get("config_sha256") != config_sha or row.get("manifest_sha256") != manifest["manifest_sha256"]:
            raise ValueError("Existing hypothesis provenance differs from this run")
        completed[sample_id] = row
    if transcriber is None:
        transcriber = importlib.import_module("sttbench.runtime").transcribe
    processed = 0
    for index, sample in enumerate(selected, 1):
        previous = completed.get(sample["id"])
        if previous and previous.get("status") == "ok":
            if previous.get("audio_sha256") != sample["_audio_sha256"]:
                raise ValueError("Audio bytes changed since previous successful transcription")
            print(f"[{index}/{len(selected)}] {sample['id']}: resumed", flush=True)
            continue
        started = time.perf_counter()
        try:
            result = transcriber(model_spec, sample["_audio_path"], config)
            if not isinstance(result, dict):
                raise ValueError("Runtime transcribe must return a dictionary")
            if result.get("status", "ok") == "ok" and not isinstance(result.get("text"), str):
                raise ValueError("Successful runtime result must contain text")
        except Exception as exc:
            result = {"status": "failed", "error": {"code": type(exc).__name__, "message": str(exc)}}
        elapsed = time.perf_counter()-started
        if result.get("status", "ok") == "ok" and model_spec.get("output_script") == "roman":
            result["text_view"] = "roman"
            result["roman_text"] = result["text"]
        else:
            result.setdefault("text_view", "original")
        row = {**result, "status": result.get("status", "ok"), "sample_id": sample["id"],
               "manifest_sha256": manifest["manifest_sha256"], "corpus_sha256": metadata["corpus_sha256"],
               "input_manifest_sha256": manifest["input_manifest_sha256"],
               "config_sha256": config_sha, "audio_sha256": sample["_audio_sha256"],
               "processing_seconds": elapsed, "duration_seconds": sample["_duration_seconds"]}
        # JSON validity is checked before replacing the previous durable output.
        json.dumps(row, allow_nan=False)
        completed[sample["id"]] = row
        _write_hypotheses(hypotheses_path, [completed[s["id"]] for s in selected if s["id"] in completed])
        processed += 1
        print(f"[{index}/{len(selected)}] {sample['id']}: {row['status']} ({elapsed:.2f}s batch wall time)", flush=True)
    return {"output": str(output.resolve()), "processed": processed, "samples": len(selected),
            "successful": sum(row.get("status") == "ok" for row in completed.values()),
            "config_sha256": config_sha, "manifest_sha256": metadata["manifest_sha256"],
            "corpus_sha256": metadata["corpus_sha256"]}
