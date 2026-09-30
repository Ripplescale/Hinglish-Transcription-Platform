"""Convert verified local Oriserve weights with pinned upstream whisper.cpp tools.

Preserves original weights, conversion logs, and a sidecar compatible with
sttbench.runtime.assets.verify_conversion_provenance. This does not validate
recognition parity or timestamps; those require actual benchmark inference.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sttbench.runtime.assets import sha256_file, verify_model_assets, verify_conversion_provenance


def worker(converter: Path, model: Path, whisper: Path, output: Path) -> None:
    # The unmodified official converter resolves only verified local assets.
    # Deny socket use as an additional guard against accidental HF fallback.
    def denied(*args, **kwargs):
        raise RuntimeError("Network access forbidden during local conversion")
    socket.socket.connect = denied
    socket.create_connection = denied
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1",
                      TOKENIZERS_PARALLELISM="false", OMP_NUM_THREADS="2")
    sys.argv = [str(converter), str(model), str(whisper), str(output)]
    runpy.run_path(str(converter), run_name="__main__")


def convert(model: Path, runtime: Path, output: Path, quantization: str) -> dict:
    model, runtime, output = model.resolve(), runtime.resolve(), output.resolve()
    manifest = json.loads((model / "artifact-manifest.json").read_text(encoding="utf-8"))
    if not str(manifest["repo_id"]).startswith("Oriserve/"):
        raise ValueError("This benchmark conversion helper only supports pinned Oriserve models")
    spec = {key: manifest[key] for key in ("model_id", "repo_id", "source_revision")}
    spec["artifact_path"] = str(model)
    verified = verify_model_assets(spec)
    if not verified["ok"] or verified["integrity"] != "manifest_files_verified":
        raise ValueError(f"Source verification failed: {verified}")
    runtime_manifest = json.loads((runtime / "runtime-manifest.json").read_text(encoding="utf-8"))
    for entry in runtime_manifest["artifacts"] + runtime_manifest["binaries"]:
        path = (runtime / entry["path"]).resolve()
        if not path.is_relative_to(runtime) or sha256_file(path) != entry["sha256"]:
            raise ValueError(f"Runtime asset verification failed: {entry['path']}")
    output.mkdir(parents=True, exist_ok=True)
    f16 = output / "ggml-model.bin"
    quantized = output / f"ggml-model-{quantization}.bin"
    for path in (f16, quantized):
        if path.exists():
            raise ValueError(f"Refusing to overwrite existing conversion: {path}")
    converter = runtime / "source/models/convert-h5-to-ggml.py"
    conversion_command = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).resolve()),
                          "--worker", "--model", str(model), "--runtime", str(runtime), "--output", str(output)]
    with (output / "conversion.log").open("w", encoding="utf-8") as log:
        subprocess.run(conversion_command, check=True, stdout=log, stderr=subprocess.STDOUT,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    base = {"source_repo_id": manifest["repo_id"], "source_revision": manifest["source_revision"],
            "source_sha256": verified["verified_hashes"], "source_hashes_verified_at_conversion": True,
            "source_manifest_sha256": sha256_file(model / "artifact-manifest.json"),
            "converter": {"name": "whisper.cpp/models/convert-h5-to-ggml.py",
                          "revision": runtime_manifest["source_revision"], "sha256": sha256_file(converter),
                          "command": conversion_command, "unmodified_upstream_converter": True},
            "mel_filters": {"source_revision": runtime_manifest["openai_whisper_revision"],
                            "sha256": sha256_file(runtime / "openai-whisper/whisper/assets/mel_filters.npz")},
            "runtime_manifest_sha256": sha256_file(runtime / "runtime-manifest.json"),
            "decoding_required": {"language": "en", "task": "transcribe"},
            "recognition_parity_validated": False, "timestamps_validated": False,
            "license": manifest["license"]}
    f16_metadata = dict(base, output_sha256=sha256_file(f16), quantization="f16")
    f16.with_suffix(".bin.provenance.json").write_text(json.dumps(f16_metadata, indent=2) + "\n", encoding="utf-8")
    quantizer = runtime / "cpu/Release/whisper-quantize.exe"
    quantize_command = [str(quantizer), str(f16), str(quantized), quantization]
    with (output / "quantization.log").open("w", encoding="utf-8") as log:
        subprocess.run(quantize_command, check=True, stdout=log, stderr=subprocess.STDOUT,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    quant_metadata = dict(base, output_sha256=sha256_file(quantized), quantization=quantization,
                         quantizer={"name": "whisper-quantize", "revision": runtime_manifest["source_revision"],
                                    "sha256": sha256_file(quantizer), "command": quantize_command,
                                    "input_sha256": f16_metadata["output_sha256"]})
    quantized.with_suffix(".bin.provenance.json").write_text(json.dumps(quant_metadata, indent=2) + "\n", encoding="utf-8")
    results = []
    for path in (f16, quantized):
        validation = verify_conversion_provenance(dict(spec, artifact_path=str(path)))
        if not validation["ok"]:
            raise ValueError(f"Conversion sidecar validation failed: {validation}")
        results.append({"path": str(path), "size": path.stat().st_size,
                        "sha256": validation["output_sha256"], "provenance_verified": True})
    return {"model_id": spec["model_id"], "outputs": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--quantization", choices=("q5_0", "q8_0"), default="q5_0")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.runtime / "source/models/convert-h5-to-ggml.py", args.model,
               args.runtime / "openai-whisper", args.output)
    else:
        print(json.dumps(convert(args.model, args.runtime, args.output, args.quantization)))
