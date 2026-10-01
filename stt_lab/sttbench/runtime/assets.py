"""Inspect local assets without fetching weights or executing model code."""

from __future__ import annotations

import hashlib
from functools import lru_cache
import json
import re
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@lru_cache(maxsize=256)
def _hash_unchanged(path: str, size: int, mtime_ns: int) -> str:
    # This is a performance cache, not a tamper-proof store. The output explicitly
    # records the stat-based cache policy; a new process always rehashes files.
    return sha256_file(Path(path))


def _asset_hash(path: Path) -> str:
    stat = path.stat()
    return _hash_unchanged(str(path), stat.st_size, stat.st_mtime_ns)


def local_path(value: Any) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("A local artifact_path is required; repository IDs are not paths.")
    text = str(value)
    if "://" in text or text.startswith(("\\\\", "//")):
        raise ValueError("Remote URLs and network-share paths are not supported.")
    return Path(value).expanduser().resolve()


def _child(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Asset path escapes the model directory: {name}")
    return path


def verify_model_assets(model_spec: dict, backend: str = "transformers") -> dict:
    """Check presence and optional expected SHA-256 values, never infer a revision.

    ``asset_sha256`` is an optional {relative_filename: sha256} mapping. Presence
    alone is reported separately from hash verification and source provenance.
    Safetensors is required for Python adapters: pickle weights are not loaded.
    """
    issues: list[str] = []
    verified_hashes: dict[str, str] = {}
    result = {"ok": False, "issues": issues, "verified_hashes": verified_hashes,
              "integrity": "not_hash_verified", "source_revision": model_spec.get("source_revision")}
    try:
        path = local_path(model_spec.get("artifact_path"))
        result["artifact_path"] = str(path)
        if backend == "whisper_cpp":
            if not path.is_file() or path.stat().st_size == 0:
                issues.append("A nonempty local whisper.cpp model binary is required.")
            result["ok"] = not issues
            return result
        if not path.is_dir():
            issues.append("Local model directory is missing.")
            return result
        for name in ("config.json", "preprocessor_config.json"):
            # Qwen's processor may be configured using processor_config.json.
            if name == "preprocessor_config.json" and backend == "qwen_asr":
                if not any((path / item).is_file() for item in (name, "processor_config.json")):
                    issues.append("Missing preprocessor_config.json or processor_config.json.")
                continue
            file = path / name
            if not file.is_file():
                issues.append(f"Missing {name}.")
            else:
                json.loads(file.read_text(encoding="utf-8"))
        if not (path / "tokenizer.json").is_file() and not (
            (path / "vocab.json").is_file() and (path / "merges.txt").is_file()
        ):
            issues.append("Missing local tokenizer assets.")
        weights = list(path.glob("*.safetensors"))
        index = path / "model.safetensors.index.json"
        if index.is_file():
            mapping = json.loads(index.read_text(encoding="utf-8")).get("weight_map")
            if not isinstance(mapping, dict) or not mapping:
                issues.append("Safetensors index has no weight_map.")
            else:
                weights = [_child(path, name) for name in set(mapping.values())]
        if not weights:
            issues.append("No safetensors weights found; this adapter does not load pickle weights.")
        for file in weights:
            if not file.is_file() or file.stat().st_size == 0:
                issues.append(f"Missing or empty weights: {file.name}")
        expected = model_spec.get("asset_sha256", {})
        if not isinstance(expected, dict):
            raise ValueError("asset_sha256 must map relative filenames to SHA-256 hashes.")
        expected = dict(expected)
        manifest_path = path / "artifact-manifest.json"
        manifest_used = manifest_path.is_file()
        if manifest_used:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("repo_id") != model_spec.get("repo_id"):
                raise ValueError("Artifact manifest repo_id does not match the specification.")
            if not model_spec.get("source_revision") or manifest.get("source_revision") != model_spec["source_revision"]:
                raise ValueError("Artifact manifest requires a matching, explicit source_revision.")
            if manifest.get("model_id") != model_spec.get("model_id"):
                raise ValueError("Artifact manifest model_id does not match the specification.")
            files = manifest.get("files")
            if not isinstance(files, list) or not files:
                raise ValueError("Artifact manifest must contain a nonempty files array.")
            seen = set()
            for entry in files:
                name, digest = entry["path"], entry["sha256"]
                if name in seen:
                    raise ValueError(f"Artifact manifest repeats a path: {name}")
                seen.add(name)
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                    raise ValueError(f"Invalid manifest SHA-256: {name}")
                if name in expected and str(expected[name]).lower() != digest.lower():
                    raise ValueError(f"Conflicting expected hashes: {name}")
                file = _child(path, name)
                size = entry.get("size")
                if type(size) is not int or size < 0:
                    raise ValueError(f"Invalid manifest file size: {name}")
                if file.is_file() and file.stat().st_size != size:
                    issues.append(f"Manifest size mismatch: {name}")
                expected[name] = digest
            # Weights used for inference must be represented in the manifest.
            for file in weights:
                if file.relative_to(path).as_posix() not in seen:
                    issues.append(f"Manifest omits loaded weights: {file.name}")
            result.update(manifest_path=str(manifest_path), manifest_sha256=sha256_file(manifest_path),
                          source_revision_declaration_matches=True, license_declaration=manifest.get("license"))
        for name, expected_hash in expected.items():
            file = _child(path, name)
            if not file.is_file():
                issues.append(f"Missing hashed asset: {name}")
                continue
            actual = _asset_hash(file)
            if actual.lower() != str(expected_hash).lower():
                issues.append(f"SHA-256 mismatch: {name}")
            else:
                verified_hashes[name] = actual
        if expected and not issues:
            result["integrity"] = "manifest_files_verified" if manifest_used else "provided_hashes_verified"
            result["hash_cache_policy"] = "same-process path/size/mtime_ns; restart to force rehash"
        result["ok"] = not issues
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        issues.append(str(exc))
    return result


def verify_conversion_provenance(model_spec: dict, sidecar_path: str | Path | None = None) -> dict:
    """Verify an existing conversion sidecar and output hash, without converting.

    Sidecar: source_repo_id, source_revision, source_sha256 (nonempty mapping),
    converter {name, revision}, output_sha256. Optional quantization is recorded.
    Source hashes are provenance declarations, NOT independently checked here.
    """
    result: dict = {"ok": False, "issues": []}
    try:
        model = local_path(model_spec.get("artifact_path"))
        sidecar = local_path(sidecar_path) if sidecar_path else model.with_suffix(model.suffix + ".provenance.json")
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if metadata.get("source_repo_id") != model_spec.get("repo_id"):
            raise ValueError("Conversion source_repo_id does not match the model specification.")
        revision = model_spec.get("source_revision")
        if not revision or metadata.get("source_revision") != revision:
            raise ValueError("Conversion requires a matching, explicit source_revision.")
        sources = metadata.get("source_sha256")
        if not isinstance(sources, dict) or not sources:
            raise ValueError("Conversion sidecar must record source_sha256 values.")
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value) for value in sources.values()):
            raise ValueError("Conversion source_sha256 values must be 64-character hex hashes.")
        converter = metadata.get("converter", {})
        if not converter.get("name") or not converter.get("revision"):
            raise ValueError("Conversion sidecar must identify converter name and revision.")
        actual = _asset_hash(model)
        if actual != metadata.get("output_sha256"):
            raise ValueError("Converted model SHA-256 does not match its provenance sidecar.")
        result.update(ok=True, metadata=metadata, output_sha256=actual,
                      source_hashes_independently_verified=False, sidecar_path=str(sidecar),
                      hash_cache_policy="same-process path/size/mtime_ns; restart to force rehash")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        result["issues"].append(str(exc))
    return result


def verify_openvino_assets(model_spec: dict, export_path: str | Path,
                           source_verification: dict) -> dict:
    """Verify an existing IR export separately from its original HF assets.

    The conversion receipt binds two independently checked manifests. It records
    conversion provenance, not proof of the publisher's identity. No export,
    quantization, or dependency import happens here.
    """
    result = {"ok": False, "issues": [], "verified_hashes": {}}
    try:
        source = local_path(model_spec.get("artifact_path"))
        target = local_path(export_path)
        if not target.is_dir() or target == source:
            raise ValueError("OpenVINO export_path must be a separate existing local directory.")
        if not source_verification.get("ok") or source_verification.get("integrity") != "manifest_files_verified":
            raise ValueError("OpenVINO requires independently verified source artifact-manifest.json assets.")
        loaded_source_files = {"config.json", "generation_config.json", "preprocessor_config.json",
                               "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt",
                               "normalizer.json", "special_tokens_map.json", "added_tokens.json",
                               "processor_config.json"}
        for name in loaded_source_files:
            if (source / name).is_file() and name not in source_verification["verified_hashes"]:
                raise ValueError(f"Source manifest omits loaded processor/config asset: {name}")
        sidecar = target / "conversion-provenance.json"
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if metadata.get("schema_version") != 1:
            raise ValueError("Unsupported OpenVINO conversion provenance schema.")
        for field, expected in (("model_id", model_spec.get("model_id")),
                                ("source_repo_id", model_spec.get("repo_id")),
                                ("source_revision", model_spec.get("source_revision"))):
            if not expected or metadata.get(field) != expected:
                raise ValueError(f"OpenVINO conversion {field} does not match the model specification.")
        if metadata.get("source_manifest_sha256") != source_verification.get("manifest_sha256"):
            raise ValueError("OpenVINO conversion source manifest SHA-256 mismatch.")
        manifest_path = target / "export-complete.json"
        manifest_hash = sha256_file(manifest_path)
        if metadata.get("export_manifest_sha256") != manifest_hash:
            raise ValueError("OpenVINO export manifest SHA-256 mismatch.")
        converter = metadata.get("converter", {})
        required_options = {
            "name": "optimum.exporters.openvino.main_export", "version": "2.2.0",
            "task": "automatic-speech-recognition-with-past", "dtype": "fp16",
            "stateful": True, "convert_tokenizer": False, "load_in_8bit": False,
        }
        for name, expected in required_options.items():
            if converter.get(name) != expected or type(converter.get(name)) is not type(expected):
                raise ValueError(f"Unsupported OpenVINO conversion option: {name}.")
        packages = converter.get("packages", {})
        for name in ("openvino", "optimum-intel", "transformers", "torch", "numpy"):
            if not isinstance(packages.get(name), str) or not packages[name].strip():
                raise ValueError(f"OpenVINO conversion is missing pinned package version: {name}.")
        if packages["optimum-intel"] != converter["version"]:
            raise ValueError("OpenVINO converter version disagrees with its package pin.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest.get("files")
        if not isinstance(files, dict) or not files:
            raise ValueError("OpenVINO export manifest must have a nonempty files mapping.")
        required = {"config.json", "generation_config.json", "preprocessor_config.json",
                    "openvino_encoder_model.xml", "openvino_encoder_model.bin",
                    "openvino_decoder_model.xml", "openvino_decoder_model.bin"}
        if not required.issubset(files):
            raise ValueError("OpenVINO export manifest omits required graph or configuration files.")
        if manifest.get("load_in_8bit") is not False:
            raise ValueError("OpenVINO export must explicitly declare load_in_8bit=false.")
        # Optimum may load additional decoder graphs or configuration from this
        # directory. Every candidate graph/config must be covered by the receipt.
        for file in target.iterdir():
            if file.is_file() and file.name not in ("conversion-provenance.json", "export-complete.json"):
                if file.suffix in (".xml", ".bin", ".json") and file.name not in files:
                    raise ValueError(f"OpenVINO export manifest omits loadable file: {file.name}")
        for name, entry in files.items():
            file = _child(target, name)
            digest, size = entry.get("sha256"), entry.get("size")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise ValueError(f"Invalid OpenVINO asset SHA-256: {name}")
            if type(size) is not int or size <= 0 or not file.is_file() or file.stat().st_size != size:
                raise ValueError(f"Missing, empty, or size-mismatched OpenVINO asset: {name}")
            actual = _asset_hash(file)
            if actual != digest.lower():
                raise ValueError(f"OpenVINO asset SHA-256 mismatch: {name}")
            result["verified_hashes"][name] = actual
        source_config = json.loads((source / "config.json").read_text(encoding="utf-8"))
        export_config = json.loads((target / "config.json").read_text(encoding="utf-8"))
        for key in ("model_type", "vocab_size", "decoder_start_token_id", "max_target_positions"):
            if key not in source_config or export_config.get(key) != source_config[key]:
                raise ValueError(f"OpenVINO export changes source model configuration: {key}")
        if export_config["model_type"] != "whisper" or manifest.get("vocab_size") != source_config["vocab_size"]:
            raise ValueError("OpenVINO export is not the matching Whisper vocabulary.")
        # Generation settings include suppression IDs and must survive export.
        source_generation = json.loads((source / "generation_config.json").read_text(encoding="utf-8"))
        export_generation = json.loads((target / "generation_config.json").read_text(encoding="utf-8"))
        # save_pretrained records the exporting library version; this is not a
        # decoding setting. All actual generation settings must remain equal.
        source_generation.pop("transformers_version", None)
        export_generation.pop("transformers_version", None)
        if source_generation != export_generation:
            raise ValueError("OpenVINO export changes the source generation configuration.")
        result.update(ok=True, integrity="source_and_export_manifests_verified", metadata=metadata,
                      export_path=str(target), sidecar_path=str(sidecar), sidecar_sha256=sha256_file(sidecar),
                      export_manifest_sha256=manifest_hash, source_hashes_independently_verified=True,
                      hash_cache_policy="same-process path/size/mtime_ns; restart to force rehash")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result["issues"].append(str(exc))
    return result
