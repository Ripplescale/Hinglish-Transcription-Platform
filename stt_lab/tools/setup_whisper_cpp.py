"""Install pinned official Windows CPU binaries and converter assets locally.

No system install, PATH mutation, driver installation, or private audio access.
The source converter is kept byte-for-byte from the declared upstream commit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

WHISPER_CPP_REVISION = "927cfce34f31707e17f2bff35c349632fb9e2c3a"
WHISPER_REVISION = "31243bad24cc746f07d4c8bfdd2d974872cb1803"
RELEASE_TAG = "b5130"
ARCHIVE_SHA256 = "f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def install(root: Path, *, direct: bool = False) -> dict:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
    artifacts = []

    def fetch(relative: str, url: str, expected: str | None = None) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            partial = path.with_name(path.name + ".part")
            request = urllib.request.Request(url, headers={"User-Agent": "STTApp-local-benchmark"})
            with opener.open(request, timeout=120) as response, partial.open("wb") as destination:
                while block := response.read(1024 * 1024):
                    destination.write(block)
            if expected and sha256(partial) != expected:
                raise ValueError(f"Downloaded hash does not match official release: {relative}")
            partial.replace(path)
        digest = sha256(path)
        if expected and digest != expected:
            raise ValueError(f"Existing asset hash does not match official release: {relative}")
        artifacts.append({"path": relative, "url": url, "size": path.stat().st_size,
                          "sha256": digest, "upstream_digest_verified": bool(expected)})
        return path

    archive = fetch("whisper-bin-x64.zip",
                    f"https://github.com/ggml-org/whisper.cpp/releases/download/{RELEASE_TAG}/whisper-bin-x64.zip",
                    ARCHIVE_SHA256)
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            target = (root / "cpu" / member.filename).resolve()
            if not target.is_relative_to(root / "cpu"):
                raise ValueError("Release archive contains an escaping path")
        zipped.extractall(root / "cpu")
    for relative in ("models/convert-h5-to-ggml.py", "LICENSE", "include/whisper.h"):
        fetch(f"source/{relative}", f"https://raw.githubusercontent.com/ggml-org/whisper.cpp/{WHISPER_CPP_REVISION}/{relative}")
    for relative in ("whisper/assets/mel_filters.npz", "LICENSE"):
        fetch(f"openai-whisper/{relative}", f"https://raw.githubusercontent.com/openai/whisper/{WHISPER_REVISION}/{relative}")
    binaries = [{"path": str(p.relative_to(root)), "size": p.stat().st_size, "sha256": sha256(p)}
                for p in sorted((root / "cpu").rglob("*")) if p.is_file()]
    manifest = {"whisper_cpp_version": "1.9.4", "release_tag": RELEASE_TAG,
                "source_revision": WHISPER_CPP_REVISION, "openai_whisper_revision": WHISPER_REVISION,
                "backend": "official_cpu_release", "vulkan_available": False,
                "upstream_release_api": f"https://api.github.com/repos/ggml-org/whisper.cpp/releases/tags/{RELEASE_TAG}",
                "license": "MIT", "artifacts": artifacts, "binaries": binaries}
    (root / "runtime-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--direct", action="store_true", help="Use direct public downloads in this process only.")
    args = parser.parse_args()
    result = install(args.root, direct=args.direct)
    print(json.dumps({"root": str(args.root.resolve()), "version": result["whisper_cpp_version"],
                      "revision": result["source_revision"], "files": len(result["binaries"])}))
