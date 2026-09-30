"""Fetch pinned portable Windows build tools for a scoped whisper.cpp Vulkan build.

The Vulkan SDK installer is downloaded but NOT run here. It must be invoked
separately with LunarG's documented copy_only=1 option and a private --root.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

ASSETS = [
    ("llvm.zip", "https://github.com/mstorsjo/llvm-mingw/releases/download/20260908/llvm-mingw-20260908-ucrt-x86_64.zip",
     "1bcf74d06b724aeecaa6412ca85f5b26fb1da770e7cdcefa9263c9c5c3ad34b6", "compiler"),
    ("cmake.zip", "https://github.com/Kitware/CMake/releases/download/v3.31.8/cmake-3.31.8-windows-x86_64.zip",
     "81aa9964dbabd71fe02e7ec50472fd3ad56138c49944515ece9001efbff8d719", "cmake"),
    ("ninja.zip", "https://github.com/ninja-build/ninja/releases/download/v1.13.1/ninja-win.zip",
     "26a40fa8595694dec2fad4911e62d29e10525d2133c9a4230b66397774ae25bf", "ninja"),
    ("vulkan-sdk-1.4.357.0.exe", "https://sdk.lunarg.com/sdk/download/1.4.357.0/windows/vulkansdk-windows-X64-1.4.357.0.exe?Human=true",
     "81f474711e9042f4cd22b31b2f7a8870db2e428b21586fb43dd80150be97310d", None),
    ("whisper-source.zip", "https://codeload.github.com/ggml-org/whisper.cpp/zip/927cfce34f31707e17f2bff35c349632fb9e2c3a",
     None, "source"),
]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fetch(root: Path, asset: tuple) -> dict:
    name, url, expected, extract = asset
    path = root / name
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if not path.is_file():
        partial = path.with_name(path.name + ".part")
        if name.startswith("vulkan-sdk-") and os.name == "nt":
            # The official public SDK endpoint accepts PowerShell's client;
            # urllib received HTTP 403 even with its documented Human parameter.
            # Paths/URLs are environment values, never interpolated shell code.
            command = ("$ErrorActionPreference='Stop'; $env:HTTP_PROXY=''; "
                       "$env:HTTPS_PROXY=''; $env:ALL_PROXY=''; "
                       "Invoke-WebRequest -Uri $env:STT_VK_DOWNLOAD_URL "
                       "-OutFile $env:STT_VK_DOWNLOAD_PATH -TimeoutSec 180")
            subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                           check=True, env={**os.environ, "STT_VK_DOWNLOAD_URL": url,
                                            "STT_VK_DOWNLOAD_PATH": str(partial)},
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            with opener.open(url, timeout=120) as response, partial.open("wb") as f:
                while block := response.read(1024 * 1024):
                    f.write(block)
        if expected and digest(partial) != expected:
            raise ValueError(f"Upstream digest mismatch: {name}")
        partial.replace(path)
    sha = digest(path)
    if expected and sha != expected:
        raise ValueError(f"Existing digest mismatch: {name}")
    if extract and not (root / extract / ".extraction-complete").is_file():
        target_root = (root / extract).resolve()
        with zipfile.ZipFile(path) as zipped:
            for member in zipped.infolist():
                if not (target_root / member.filename).resolve().is_relative_to(target_root):
                    raise ValueError(f"Archive path escapes target: {member.filename}")
            extraction_path = "\\\\?\\" + str(target_root) if os.name == "nt" else str(target_root)
            zipped.extractall(extraction_path)
        (target_root / ".extraction-complete").write_text(sha, encoding="ascii")
    record = {"path": name, "url": url, "sha256": sha,
              "size": path.stat().st_size, "upstream_hash_verified": bool(expected),
              "extracted_to": extract}
    print(json.dumps(record), flush=True)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch, root, asset): asset[0] for asset in ASSETS}
        records, errors = [], []
        for future in concurrent.futures.as_completed(futures):
            try:
                records.append(future.result())
            except Exception as exc:
                errors.append({"asset": futures[future], "error": str(exc)})
                print(json.dumps(errors[-1]), flush=True)
        if errors:
            raise RuntimeError(json.dumps(errors))
    (root / "toolchain-manifest.json").write_text(json.dumps({
        "source_revision": "927cfce34f31707e17f2bff35c349632fb9e2c3a", "artifacts": records,
        "sdk_copy_only_required": True, "system_path_changes_permitted": False,
        "sdk_documentation": "https://vulkan.lunarg.com/doc/view/latest/windows/getting_started.html",
    }, indent=2) + "\n", encoding="utf-8")
