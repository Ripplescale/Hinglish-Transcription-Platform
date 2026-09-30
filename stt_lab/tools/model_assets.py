"""Fetch verified, pinned research models. Inference never uses this network tool.

Only the admitted registry can select repositories. All data defaults outside the
source checkout and OneDrive. Run `python model_assets.py --help` for commands.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request

REGISTRY = Path(__file__).resolve().parents[1] / "models.json"
SUPPORT_FILES = {
    "README.md", "LICENSE", "LICENSE.md", "config.json", "generation_config.json",
    "preprocessor_config.json", "processor_config.json", "tokenizer.json",
    "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json",
    "vocab.json", "merges.txt", "normalizer.json", "chat_template.json",
    "chat_template.jinja", "model.safetensors.index.json", "convert_hf2openai.json",
}


def runtime_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise ValueError("LOCALAPPDATA is required; refusing to put recordings or models in the project.")
    return Path(base).resolve() / "STTApp" / "lab"


def load_spec(model_id: str) -> dict:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for spec in registry["models"]:
        if spec["id"] == model_id:
            if spec["license"] not in registry["policy"]["stt_licenses"]:
                raise ValueError("Model license is not admitted")
            if not re.fullmatch(r"[0-9a-f]{40}", spec["source_revision"]):
                raise ValueError("Model must be pinned to an immutable commit")
            return spec
    raise ValueError(f"Unknown or excluded model: {model_id}")


def file_digest(path: Path, algorithm="sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_remote_file(path: Path, entry: dict) -> None:
    expected_size = entry.get("size")
    if expected_size is not None and path.stat().st_size != expected_size:
        raise ValueError(f"Size mismatch: {path.name}")
    lfs = entry.get("lfs") or {}
    expected_sha = lfs.get("sha256")
    if expected_sha:
        if file_digest(path) != expected_sha:
            raise ValueError(f"SHA-256 mismatch: {path.name}")
    elif entry.get("blobId"):
        digest = hashlib.sha1()
        digest.update(f"blob {path.stat().st_size}\0".encode())
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != entry["blobId"]:
            raise ValueError(f"Git blob mismatch: {path.name}")
    else:
        raise ValueError(f"No upstream digest available: {path.name}")


def download_file(opener, url: str, partial: Path, entry: dict, attempts: int = 8) -> None:
    """Resume interrupted large downloads, then require the full upstream hash."""
    expected = entry.get("size")
    for attempt in range(attempts):
        offset = partial.stat().st_size if partial.exists() else 0
        if expected is not None and offset >= expected:
            try:
                verify_remote_file(partial, entry)
                return
            except ValueError:
                offset = 0
        request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"}) if offset else url
        try:
            with opener.open(request, timeout=120) as response:
                status = getattr(response, "status", 200)
                if status == 206:
                    content_range = getattr(response, "headers", {}).get("Content-Range", "")
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
                    if not match or not (0 <= int(match[1]) <= int(match[2]) < int(match[3])) or int(match[1]) != offset or (expected is not None and int(match[3]) != expected):
                        raise ValueError("Download returned an invalid Content-Range")
                elif status == 200:
                    offset = 0  # Server ignored Range; restart rather than append duplicate bytes.
                else:
                    raise OSError(f"Unexpected download HTTP status {status}")
                with partial.open("ab" if offset else "wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            actual = partial.stat().st_size
            if expected is not None and actual < expected:
                raise OSError(f"Interrupted download at {actual} of {expected} bytes")
            verify_remote_file(partial, entry)
            return
        except (OSError, http.client.IncompleteRead) as exc:
            if attempt + 1 == attempts:
                raise OSError(f"Download incomplete after {attempts} attempts: {exc}") from exc
            print(f"Retrying {partial.name}: {exc}", flush=True)
            time.sleep(min(attempt + 1, 5))


def fetch(model_id: str, direct=False, include_smoke=False) -> dict:
    spec = load_spec(model_id)
    revision = spec["source_revision"]
    root = runtime_root() / "models" / spec["id"] / revision
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
    api_url = f"https://huggingface.co/api/models/{spec['repo_id']}/revision/{revision}?blobs=true"
    with opener.open(api_url, timeout=60) as response:
        metadata = json.load(response)
    if metadata.get("sha") != revision:
        raise ValueError("Upstream revision does not match registry")
    if metadata.get("cardData", {}).get("license") != spec["license"]:
        raise ValueError("Upstream license does not match reviewed registry")
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for entry in metadata["siblings"]:
        name = entry["rfilename"]
        chosen = name in SUPPORT_FILES or re.fullmatch(r"model(?:-\d+-of-\d+)?\.safetensors", name)
        if include_smoke and name == "audios/f5e0178c-354c-40c9-b3a7-687c86240a77_1_1152496_1175488.wav":
            chosen = True
        if not chosen:
            continue
        target = (root / name).resolve()
        if not target.is_relative_to(root.resolve()):
            raise ValueError("Unsafe upstream filename")
        target.parent.mkdir(parents=True, exist_ok=True)
        valid_existing = False
        if target.exists():
            try:
                verify_remote_file(target, entry)
                valid_existing = True
            except ValueError:
                pass
        if not valid_existing:
            url = f"https://huggingface.co/{spec['repo_id']}/resolve/{revision}/{urllib.parse.quote(name, safe='/')}"
            partial = target.with_name(target.name + ".part")
            print(f"Fetching {model_id}/{name}", flush=True)
            download_file(opener, url, partial, entry)
            partial.replace(target)
        files.append({"path": name, "size": target.stat().st_size, "sha256": file_digest(target)})
    if not any(item["path"].endswith(".safetensors") for item in files):
        raise ValueError("No model weights found")
    manifest = {
        "version": 1, "model_id": model_id, "repo_id": spec["repo_id"],
        "source_revision": revision, "license": spec["license"], "files": files,
        "smoke_audio_is_human_verified_reference": False,
    }
    pending = root / "artifact-manifest.json.tmp"
    pending.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    pending.replace(root / "artifact-manifest.json")
    return {"status": "downloaded_and_verified", "artifact_path": str(root), "files": len(files)}


def verify(model_id: str) -> dict:
    spec = load_spec(model_id)
    root = runtime_root() / "models" / model_id / spec["source_revision"]
    manifest = json.loads((root / "artifact-manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("source_revision") != spec["source_revision"]
            or manifest.get("repo_id") != spec["repo_id"]
            or manifest.get("model_id") != model_id
            or manifest.get("license") != spec["license"]):
        raise ValueError("Artifact provenance differs from registry")
    files = manifest.get("files")
    if not isinstance(files, list) or not files or not any(item.get("path", "").endswith(".safetensors") for item in files):
        raise ValueError("Artifact manifest must contain verified model weights")
    seen = set()
    for item in files:
        if item["path"] in seen:
            raise ValueError("Duplicate path in artifact manifest")
        seen.add(item["path"])
        target = (root / item["path"]).resolve()
        if not target.is_relative_to(root.resolve()):
            raise ValueError("Manifest path escapes artifact root")
        if target.stat().st_size != item["size"] or file_digest(target) != item["sha256"]:
            raise ValueError(f"Artifact corrupted: {item['path']}")
    return {"status": "verified", "artifact_path": str(root), "files": len(manifest["files"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["fetch", "verify"])
    parser.add_argument("model_id")
    parser.add_argument("--direct", action="store_true", help="Use direct HTTPS for this setup command only")
    parser.add_argument("--include-smoke", action="store_true", help="Download one publisher example for smoke testing; not human reference")
    args = parser.parse_args()
    try:
        result = fetch(args.model_id, args.direct, args.include_smoke) if args.command == "fetch" else verify(args.model_id)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
