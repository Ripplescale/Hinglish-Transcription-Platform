"""Prepare or activate the isolated Windows Community-1 CPU runtime.

Preparation installs dependencies and decodes synthetic audio without model access.
After the user has accepted the model conditions and logged into HF locally, the
same command with --activate downloads a pinned revision, validates it offline,
and only then publishes speaker-runtime.json. Tokens are never accepted or logged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import venv
import zipfile


PINS = {"torch": "2.10.0+cpu", "torchaudio": "2.10.0+cpu",
        "torchcodec": "0.10.0", "pyannote.audio": "4.0.7"}
CPU_INDEX = "https://download.pytorch.org/whl/cpu"
PYPI_INDEX = "https://pypi.org/simple"
FFMPEG_NAME = "ffmpeg-n8.1.3-6-gff48edd8b2-win64-lgpl-shared-8.1"
FFMPEG_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
              "autobuild-2026-09-29-13-10/" + FFMPEG_NAME + ".zip")
FFMPEG_SHA256 = "1c9af2356443fec537fe1a64a5b33cb4c54fa212ad6590464423b3437e1aaa44"
SOURCES = [
    "https://pypi.org/project/pyannote.audio/4.0.7/",
    "https://raw.githubusercontent.com/pyannote/pyannote-audio/4.0.7/pyproject.toml",
    "https://pytorch.org/get-started/previous-versions/#v2-10-0",
    "https://github.com/meta-pytorch/torchcodec#compatibility-with-torch-versions",
    "https://ffmpeg.org/download.html",
    "https://github.com/BtbN/FFmpeg-Builds/releases/tag/autobuild-2026-09-29-13-10",
    "https://huggingface.co/pyannote/speaker-diarization-community-1",
]


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def local_root(path):
    result = Path(path).expanduser().resolve()
    if "onedrive" in str(result).lower():
        raise ValueError("The speaker runtime and recordings must stay outside OneDrive")
    if result == Path(result.anchor):
        raise ValueError("Choose an application directory, not a drive root")
    return result


def available_memory_bytes():
    if os.name != "nt":
        return None
    import ctypes
    class MemoryStatus(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong), ("available_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended", ctypes.c_ulonglong)]
    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError("Could not check available memory before the model smoke test")
    return status.available_physical


def install_decoder_bootstrap(environment_root, ffmpeg):
    # shutil.which on Windows otherwise searches the current directory before
    # PATH. TorchCodec converts a relative ffmpeg.exe match into DLL directory
    # '.', which AddDllDirectory rejects. Keep this change private to the venv.
    bootstrap = ("import os\nfrom pathlib import Path\n"
                 "os.environ['PYANNOTE_METRICS_ENABLED'] = '0'\n"
                 "os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'\n"
                 "os.environ['NoDefaultCurrentDirectoryInExePath'] = '1'\n"
                 f"_sttapp_ffmpeg = {str(ffmpeg)!r}\n"
                 "_sttapp_path_entries = [entry.strip().strip(chr(34)) for entry in os.environ.get('PATH', '').split(os.pathsep)]\n"
                 "_sttapp_path_entries = [entry for entry in _sttapp_path_entries if entry and Path(entry).is_absolute()]\n"
                 "os.environ['PATH'] = os.pathsep.join([_sttapp_ffmpeg, *_sttapp_path_entries])\n"
                 "_sttapp_ffmpeg_dll_handle = os.add_dll_directory(_sttapp_ffmpeg) if os.name == 'nt' else None\n")
    target = environment_root / "Lib" / "site-packages" / "sitecustomize.py"
    target.write_text(bootstrap, encoding="utf-8")
    return target


def run(command, log, *, environment=None, capture=False):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with Path(log).open("a", encoding="utf-8") as stream:
        # Commands contain public package arguments and local paths, never tokens.
        stream.write("\n" + json.dumps([str(part) for part in command]) + "\n")
        stream.flush()
        result = subprocess.run([str(part) for part in command], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE if capture else stream,
                                stderr=stream, text=True, encoding="utf-8", errors="replace",
                                env=environment, creationflags=flags)
    if result.returncode:
        raise RuntimeError(f"Setup step failed with exit code {result.returncode}; see {log}")
    return result.stdout if capture else None


def install_ffmpeg(asset_root):
    archive = asset_root / "downloads" / (FFMPEG_NAME + ".zip")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file() or sha(archive) != FFMPEG_SHA256:
        temporary = archive.with_suffix(".download")
        request = urllib.request.Request(FFMPEG_URL, headers={"User-Agent": "STTApp-local-setup/1"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as target:
            shutil.copyfileobj(response, target, length=1024 * 1024)
        if sha(temporary) != FFMPEG_SHA256:
            raise ValueError("FFmpeg archive integrity check failed")
        os.replace(temporary, archive)
    destination = asset_root / "ffmpeg" / FFMPEG_SHA256[:16]
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for item in package.infolist():
            resolved = (destination / item.filename).resolve()
            if not resolved.is_relative_to(destination) or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe path in FFmpeg archive")
        package.extractall(destination)
    binary = destination / FFMPEG_NAME / "bin"
    if not (binary / "ffmpeg.exe").is_file() or not list(binary.glob("avcodec-*.dll")):
        raise ValueError("FFmpeg shared decoder libraries are missing")
    return binary


def probe(model_path=None, revision=None):
    """No private inputs: exercise the same decoder used by pyannote on a tone."""
    import array
    import importlib.metadata
    import math
    import socket
    import tempfile
    import wave
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", PYANNOTE_METRICS_ENABLED="0")
    def blocked(*_args, **_kwargs):
        raise RuntimeError("Network is disabled during speaker runtime validation")
    socket.create_connection = blocked
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    import torch
    import torchaudio
    from torchcodec.decoders import AudioDecoder
    from pyannote.audio import Audio, Pipeline
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    if torch.version.cuda is not None:
        raise ValueError("Expected an isolated CPU-only PyTorch build")
    versions = {name: importlib.metadata.version(name) for name in PINS}
    if versions != PINS:
        raise ValueError(f"Speaker package pins differ: {versions}")
    with tempfile.TemporaryDirectory(prefix="sttapp-speaker-probe-") as temporary:
        audio = Path(temporary) / "synthetic-tone.wav"
        samples = array.array("h", [int(8000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(16000)])
        with wave.open(str(audio), "wb") as target:
            target.setnchannels(1); target.setsampwidth(2); target.setframerate(16000)
            target.writeframes(samples.tobytes())
        decoded = AudioDecoder(str(audio)).get_all_samples()
        if decoded.sample_rate != 16000 or list(decoded.data.shape) != [1, 16000]:
            raise ValueError("TorchCodec changed the synthetic audio shape")
        expected = torch.tensor(samples, dtype=torch.float32).reshape(1, -1) / 32768
        if not torch.allclose(decoded.data, expected, atol=1e-6):
            raise ValueError("TorchCodec changed the synthetic PCM samples")
        waveform, rate = Audio(sample_rate=16000, mono="downmix")(str(audio))
        if rate != 16000 or not torch.allclose(waveform, expected, atol=1e-6):
            raise ValueError("The pyannote file decoder changed the synthetic PCM samples")
        result = {"dependencies_validated": True, "versions": versions, "cuda": torch.version.cuda,
                  "torchcodec_pcm_exact": True, "pyannote_pcm_exact": True, "sample_rate": rate,
                  "frames": waveform.shape[-1], "network_disabled": True, "model_validated": False}
        if model_path:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from sttbench.speaker_worker import inspect_model
            root, manifest = inspect_model({"model_path": str(model_path), "model_revision": revision})
            pipeline = Pipeline.from_pretrained(str(root))
            if pipeline is None:
                raise ValueError("The offline speaker pipeline did not load")
            output = pipeline(str(audio))
            if not hasattr(output, "speaker_diarization"):
                raise ValueError("The local speaker pipeline returned an unexpected result")
            result.update(model_validated=True, model_revision=manifest["revision"],
                          model_manifest_sha256=sha(root / "sttapp-model-manifest.json"),
                          smoke_turns=len(list(output.speaker_diarization)),
                          smoke_input="one-second generated tone; not a speaker accuracy test")
    print(json.dumps(result))


def attribution(python, setup_root, log):
    code = """import importlib.metadata as m,json
rows=[]
for d in m.distributions():
 rows.append({'name':d.metadata['Name'],'version':d.version,'license':d.metadata.get('License-Expression') or d.metadata.get('License'),'homepage':d.metadata.get('Home-page'),'license_files':[str(d.locate_file(p)) for p in d.files or [] if any(s in str(p).lower() for s in ('license','copying','notice'))]})
print(json.dumps(sorted(rows,key=lambda x:x['name'].lower())))
"""
    rows = json.loads(run([python, "-c", code], log, capture=True))
    write_json(setup_root / "dependency-attribution.json", rows)


def prepare(data_root, asset_root):
    setup_root = asset_root / "setup" / "community1-cpu-py312"
    setup_root.mkdir(parents=True, exist_ok=True)
    log = setup_root / "setup.log"
    environment_root = asset_root / "lab" / "venvs" / "community1-cpu-py312"
    python = environment_root / "Scripts" / "python.exe"
    if not python.is_file():
        print("Creating the isolated Community-1 Python environment.", flush=True)
        venv.EnvBuilder(with_pip=True).create(environment_root)
    if (environment_root / "pyvenv.cfg").read_text().find("include-system-site-packages = false") < 0:
        raise ValueError("The speaker environment must not use system Python packages")
    constraints = setup_root / "core-constraints.txt"
    constraints.write_text("\n".join(f"{name}=={version}" for name, version in PINS.items()) + "\n", encoding="utf-8")
    pip = [python, "-m", "pip", "--isolated", "--disable-pip-version-check", "--timeout", "120", "--retries", "5"]
    attempt = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    print("Installing pinned CPU packages (no model weights).", flush=True)
    run(pip + ["install", "--prefer-binary", "--index-url", CPU_INDEX,
               "--report", setup_root / ("torch-install-" + attempt + ".json"), "torch==" + PINS["torch"],
               "torchaudio==" + PINS["torchaudio"]], log)
    install = pip + ["install", "--prefer-binary", "--index-url", PYPI_INDEX,
                     "--constraint", constraints, "--report", setup_root / ("pyannote-install-" + attempt + ".json")]
    lock = Path(__file__).with_name("community1-windows-py312.lock.txt")
    if lock.is_file():
        install += ["--requirement", lock]
    else:
        install += ["torchcodec==" + PINS["torchcodec"], "pyannote.audio==" + PINS["pyannote.audio"]]
    run(install, log)
    run(pip + ["check"], log)
    print("Installing hash-verified shared FFmpeg libraries.", flush=True)
    ffmpeg = install_ffmpeg(asset_root)
    # Private to this venv. Retain the DLL-directory handle for the interpreter's
    # lifetime; the same bootstrap applies when Rust directly launches python.exe.
    bootstrap_path = install_decoder_bootstrap(environment_root, ffmpeg)
    freeze = run(pip + ["freeze"], log, capture=True)
    (setup_root / "requirements.freeze.txt").write_text(freeze, encoding="utf-8")
    attribution(python, setup_root, log)
    print("Checking offline TorchCodec and pyannote decoding with generated PCM.", flush=True)
    validation = json.loads(run([python, "-B", Path(__file__).resolve(), "--probe"], log, capture=True))
    manifest = {"version": 1, "prepared_at": datetime.now(timezone.utc).isoformat(),
                "environment": str(environment_root), "python_executable": str(python),
                "data_root": str(data_root), "asset_root": str(asset_root), "pins": PINS,
                "ffmpeg": {"url": FFMPEG_URL, "archive_sha256": FFMPEG_SHA256, "bin": str(ffmpeg),
                           "license": "LGPL build; upstream license files retained in extracted archive"},
                "sources": SOURCES, "validation": validation, "model_available": False,
                "runtime_config_published": False, "setup_script_sha256": sha(__file__),
                "decoder_bootstrap_sha256": sha(bootstrap_path),
                "freeze_sha256": sha(setup_root / "requirements.freeze.txt")}
    write_json(setup_root / "readiness.json", manifest)
    return python, setup_root, manifest


def activate(python, setup_root, manifest, data_root, asset_root):
    log = setup_root / "setup.log"
    helper = Path(__file__).with_name("prepare_speaker_model.py")
    # The reviewed helper checks locally stored authentication, never prints it,
    # and does not accept model conditions on the user's behalf.
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run([str(python), "-B", str(helper), "--check-access"], capture_output=True,
                            text=True, encoding="utf-8", stdin=subprocess.DEVNULL, creationflags=flags)
    access = json.loads(result.stdout)
    if result.returncode or not access.get("ready"):
        print(json.dumps({"dependencies_ready": True, "model_available": False,
                          "reason": access.get("reason", "Hugging Face access is not ready"),
                          "readiness": str(setup_root / "readiness.json")}))
        return 2
    revision = access["revision"]
    model = asset_root / "lab" / "models" / "community1" / revision
    if not (model / "sttapp-model-manifest.json").is_file():
        print("Downloading the access-approved model at its exact revision.", flush=True)
        run([python, "-B", helper, "--revision", revision, "--output", model], log)
    free_memory = available_memory_bytes()
    if free_memory is not None and free_memory < 4 * 1024**3:
        raise RuntimeError("Model download is saved. Free at least 4 GiB RAM before rerunning the speaker activation test.")
    print("Validating model hashes and the complete offline pipeline on generated audio.", flush=True)
    validation = json.loads(run([python, "-B", Path(__file__).resolve(), "--probe",
                                 "--model-path", model, "--revision", revision], log, capture=True))
    if not validation.get("model_validated"):
        raise ValueError("Speaker runtime validation is incomplete")
    source = Path(__file__).resolve().parents[1] / "sttbench"
    filenames = ["speaker_worker.py", "speaker_supervision.py", "speaker_reconciliation.py"]
    contents = {name: (source / name).read_bytes() for name in filenames}
    source_hashes = {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}
    source_id = hashlib.sha256(json.dumps(source_hashes, sort_keys=True).encode()).hexdigest()[:16]
    runtime = asset_root / "speaker-runtime" / source_id
    package = runtime / "sttbench"
    package.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        (package / name).write_bytes(content)
    (package / "__init__.py").write_text("", encoding="utf-8")
    worker = runtime / "worker.py"
    worker.write_text("import runpy\nrunpy.run_module('sttbench.speaker_worker', run_name='__main__')\n", encoding="utf-8")
    config = {"version": 1, "python_executable": str(python), "worker_script": str(worker),
              "model_path": str(model), "model_revision": revision, "device": "cpu", "threads": 4,
              "enabled": True, "source_id": source_id, "source_hashes": source_hashes,
              "model_id": access["model_id"], "license": "CC-BY-4.0", "network_inference": False,
              "decoder_bootstrap_sha256": manifest.get("decoder_bootstrap_sha256"),
              "validation": validation}
    write_json(runtime / "source-manifest.json", config)
    # Publication is last. Native availability checks must never see a partial setup.
    write_json(data_root / "speaker-runtime.json", config)
    manifest.update(validation=validation, model_available=True, runtime_config_published=True,
                    model_path=str(model), runtime_config=str(data_root / "speaker-runtime.json"))
    write_json(setup_root / "readiness.json", manifest)
    print(json.dumps({"dependencies_ready": True, "model_available": True,
                      "runtime_config": str(data_root / "speaker-runtime.json")}))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(os.environ["STTAPP_DATA_DIR"]) if os.environ.get("STTAPP_DATA_DIR") else Path(os.environ.get("LOCALAPPDATA", ".")) / "STTApp"
    parser.add_argument("--data-root", type=Path, default=default_root)
    parser.add_argument("--asset-root", type=Path, help="Optional existing model/runtime root outside OneDrive")
    parser.add_argument("--activate", action="store_true", help="After local user authentication, download and validate Community-1, then enable it")
    parser.add_argument("--probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--model-path", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--revision", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.probe:
        probe(args.model_path, args.revision)
        return 0
    if os.name != "nt" or sys.version_info[:2] != (3, 12):
        raise ValueError("This locked runtime setup requires Windows x64 Python 3.12")
    data_root = local_root(args.data_root)
    asset_root = local_root(args.asset_root or data_root)
    python, setup_root, manifest = prepare(data_root, asset_root)
    if args.activate:
        return activate(python, setup_root, manifest, data_root, asset_root)
    print(json.dumps({"dependencies_ready": True, "model_available": False,
                      "readiness": str(setup_root / "readiness.json")}))
    return 0


if __name__ == "__main__":
    if "--probe" in sys.argv:
        raise SystemExit(main())
    from windows_https import verified_windows_https
    with verified_windows_https():
        raise SystemExit(main())
