"""Batch ASR adapters with explicit offline, decoding, and capability contracts.

No adapter reports live latency from batch timing. Timestamps are absent unless
the underlying backend actually returns them. Optional inference packages are
imported only when a local, validated model is requested.
"""

from __future__ import annotations

import contextlib
import copy
import importlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import wave

from .assets import local_path, verify_conversion_provenance, verify_model_assets, verify_openvino_assets

_CACHE: dict[tuple, tuple] = {}
_LOCK = threading.RLock()
_BACKENDS = {
    "transformers": ("torch", "transformers", "numpy"),
    "openvino": ("torch", "transformers", "numpy", "openvino", "optimum.intel.openvino"),
    "whisper_cpp": (),
}
_OFFLINE = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1", "TOKENIZERS_PARALLELISM": "false",
            "NNCF_TELEMETRY_DISABLED": "1", "OPENVINO_TELEMETRY_DISABLED": "1"}


class RuntimeFailure(Exception):
    def __init__(self, code: str, message: str, status: str = "failed"):
        super().__init__(message)
        self.code, self.status = code, status


def reset_runtime_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def inspect_capabilities(runtime_config: dict | None = None) -> dict:
    """Inspect installation only: no importing Torch, loading weights, or downloads."""
    config = runtime_config or {}
    result = {}
    for backend, names in _BACKENDS.items():
        deps = {}
        for name in names:
            try:
                # find_spec on a dotted module imports its parents. Capability
                # inspection must not import optimum.intel (and thus Torch).
                present = importlib.util.find_spec(name.split(".")[0]) is not None
            except (ImportError, ValueError):
                present = False
            try:
                package = "optimum-intel" if name == "optimum.intel.openvino" else name.replace("_", "-")
                version = importlib.metadata.version(package) if present else None
            except importlib.metadata.PackageNotFoundError:
                version = None
                if name == "optimum.intel.openvino":
                    present = False
            deps[name] = {"installed": present, "version": version}
        supported = True
        result[backend] = {"implemented": supported, "dependencies": deps,
                           "dependencies_available": all(v["installed"] for v in deps.values()),
                           "mode": "batch", "live_latency_verified": False}
        if backend == "whisper_cpp":
            executable = config.get("executable")
            try:
                exists = bool(executable) and local_path(executable).is_file()
            except ValueError:
                exists = False
            result[backend]["executable_available"] = exists
    return result


@contextlib.contextmanager
def _offline_environment():
    previous = {key: os.environ.get(key) for key in _OFFLINE}
    os.environ.update(_OFFLINE)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _load_dependency(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise RuntimeFailure("dependency_missing", f"Cannot import optional dependency {name}: {exc}", "unavailable") from exc


def _positive(value, name: str, integer: bool = False):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be positive.")
    parsed = int(value) if integer else float(value)
    if not math.isfinite(parsed) or parsed <= 0 or (integer and parsed != float(value)):
        raise ValueError(f"{name} must be positive{' and integral' if integer else ''}.")
    return parsed


def _read_pcm(path: Path, max_seconds: float = 30.0):
    """Require normalized benchmark audio instead of silently resampling it."""
    with wave.open(str(path), "rb") as audio:
        channels, width, rate, frames = (audio.getnchannels(), audio.getsampwidth(),
                                        audio.getframerate(), audio.getnframes())
        if audio.getcomptype() != "NONE" or (channels, width, rate) != (1, 2, 16000):
            raise RuntimeFailure("audio_format", "Expected uncompressed mono 16 kHz, 16-bit PCM WAV.")
        duration = frames / rate
        if frames == 0 or duration > max_seconds:
            raise RuntimeFailure("audio_duration", f"Expected a nonempty benchmark clip of at most {max_seconds:g} seconds.")
        raw = audio.readframes(frames)
        if len(raw) != frames * width:
            raise RuntimeFailure("audio_truncated", "WAV data is shorter than its declared frame count.")
    return raw, duration


def _decoding(spec: dict) -> dict:
    decoding = dict(spec.get("decoding") or {})
    repo = str(spec.get("repo_id", "")).lower()
    if repo.startswith("oriserve/"):
        if decoding.get("language", "en") != "en" or decoding.get("task", "transcribe") != "transcribe":
            raise RuntimeFailure("decoder_mismatch", "Oriserve requires the documented transcribe/en decoder configuration.")
        decoding.update(language="en", task="transcribe")
    elif repo.startswith("trelis/") or decoding.get("mixed_code") or decoding.get("mode") == "mixedcode":
        language = decoding.get("language", "hi")
        if language not in ("hi", "en") or decoding.get("task", "transcribe") != "transcribe":
            raise RuntimeFailure("decoder_mismatch", "Trelis requires an explicit hi/en transcription prefix.")
        decoding.update(language=language, task="transcribe", mixed_code=decoding.get("mixed_code", True))
    else:
        if decoding.get("task", "transcribe") != "transcribe":
            raise RuntimeFailure("decoder_mismatch", "This transcription benchmark does not run translation.")
        decoding.setdefault("task", "transcribe")
    return decoding


def _device_dtype(torch, config):
    device = config.get("device", "cpu")
    if device not in ("cpu", "cuda", "cuda:0", "xpu", "xpu:0"):
        raise RuntimeFailure("device_unsupported", "Python adapters support explicit cpu, cuda[:0], or xpu[:0] devices.", "unavailable")
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeFailure("device_unavailable", "CUDA was requested but is unavailable.", "unavailable")
    if device.startswith("xpu") and not (hasattr(torch, "xpu") and torch.xpu.is_available()):
        raise RuntimeFailure("device_unavailable", "Intel XPU was requested but is unavailable in this Torch build.", "unavailable")
    dtype_name = config.get("dtype", "float32" if device == "cpu" else "float16")
    if dtype_name not in ("float32", "float16", "bfloat16"):
        raise ValueError("dtype must be float32, float16, or bfloat16.")
    if device == "cpu" and dtype_name == "float16":
        raise RuntimeFailure("dtype_unsupported", "Use float32 or an explicitly benchmarked bfloat16 path on CPU.", "unavailable")
    if "threads" in config:
        torch.set_num_threads(_positive(config["threads"], "threads", integer=True))
    return device, getattr(torch, dtype_name), dtype_name


def _hf_whisper(spec, audio, raw, config, result):
    torch, transformers, np = (_load_dependency(name) for name in _BACKENDS["transformers"])
    device, dtype, dtype_name = _device_dtype(torch, config)
    decoding = _decoding(spec)
    if decoding.get("mixed_code") and config.get("timestamps"):
        raise RuntimeFailure("timestamps_unsupported", "Trelis mixedcode uses its published no-timestamps prefix; alignment is a separate evaluation.", "unavailable")
    key = ("transformers", spec["artifact_path"], spec.get("source_revision"), device, dtype_name)
    load_start = time.perf_counter()
    cached = _CACHE.get(key) if config.get("reuse_model", True) else None
    if cached:
        processor, model = cached
    else:
        processor = transformers.AutoProcessor.from_pretrained(spec["artifact_path"], local_files_only=True, trust_remote_code=False)
        model = transformers.AutoModelForSpeechSeq2Seq.from_pretrained(
            spec["artifact_path"], local_files_only=True, trust_remote_code=False,
            use_safetensors=True, torch_dtype=dtype,
        ).to(device).eval()
        if config.get("reuse_model", True):
            _CACHE[key] = (processor, model)
    result["timing"]["load_seconds"] = time.perf_counter() - load_start
    result["provenance"].update(device=device, dtype=dtype_name, model_reused=bool(cached), decoding=decoding)
    _generate_whisper(spec, raw, config, result, processor, model, torch, np, device, dtype, decoding)


def _generate_whisper(spec, raw, config, result, processor, model, torch, np, device, dtype, decoding):
    """One decoder implementation for HF and OpenVINO; keep prompts identical."""
    inference_start = time.perf_counter()
    samples = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
    inputs = processor(samples, sampling_rate=16000, return_tensors="pt", return_attention_mask=True)
    features = inputs.input_features.to(device=device, dtype=dtype)
    generation = copy.deepcopy(model.generation_config)
    generation.forced_decoder_ids = None
    generation.language = None
    oriserve = str(spec.get("repo_id", "")).lower().startswith("oriserve/")
    # The publisher calls Transformers' ASR pipeline, whose 4.56 defaults are
    # five beams and 256 new tokens. Direct model.generate defaults differ.
    default_tokens = 256 if oriserve else 440
    default_beams = 5 if oriserve else getattr(generation, "num_beams", 1)
    requested = _positive(config.get("max_new_tokens", decoding.get("max_new_tokens", default_tokens)),
                          "max_new_tokens", integer=True)
    num_beams = _positive(config.get("num_beams", decoding.get("num_beams", default_beams)),
                          "num_beams", integer=True)
    kwargs = {"generation_config": generation, "task": "transcribe",
              "return_timestamps": bool(config.get("timestamps", False)), "do_sample": False,
              "num_beams": num_beams}
    language = decoding.get("language")
    if language not in (None, "auto"):
        kwargs["language"] = language
    if hasattr(inputs, "attention_mask"):
        kwargs["attention_mask"] = inputs.attention_mask.to(device)
    prefix_size = 4
    if decoding.get("mixed_code"):
        language_token = f"<|{language}|>"
        tokens = [language_token, "<|mixedcode|>", "<|transcribe|>", "<|notimestamps|>"]
        vocab = processor.tokenizer.get_vocab()
        missing = [token for token in tokens if token != "<|mixedcode|>" and token not in vocab]
        if missing:
            raise RuntimeFailure("custom_token_missing", f"Trelis tokenizer is missing required tokens: {', '.join(missing)}")
        mixed_ids = processor.tokenizer("<|mixedcode|>", add_special_tokens=False).input_ids
        if not isinstance(mixed_ids, list) or not mixed_ids or any(not isinstance(token, int) for token in mixed_ids):
            raise RuntimeFailure("custom_token_missing", "Trelis tokenizer did not encode its mixedcode marker.")
        prefix = [model.config.decoder_start_token_id, vocab[language_token], *mixed_ids,
                  vocab["<|transcribe|>"], vocab["<|notimestamps|>"]]
        # Explicit decoder input preserves the custom position even in modern
        # Transformers, where forced_decoder_ids can be overwritten by Whisper.
        kwargs["decoder_input_ids"] = torch.tensor([prefix], dtype=torch.long, device=device)
        result["provenance"]["decoder_prefix_tokens"] = ["<|startoftranscript|>"] + tokens
        result["provenance"]["decoder_prefix_ids"] = prefix
        prefix_size = len(prefix)
    maximum = getattr(model.config, "max_target_positions", 448) - prefix_size
    if maximum <= 0:
        raise RuntimeFailure("decoder_mismatch", "Decoder prefix exhausts the model's maximum token length.")
    kwargs["max_new_tokens"] = min(requested, maximum)
    result["provenance"]["max_new_tokens"] = kwargs["max_new_tokens"]
    result["provenance"]["effective_generation"] = {
        "task": "transcribe", "language": language, "do_sample": False,
        "num_beams": num_beams, "max_new_tokens": kwargs["max_new_tokens"],
        "return_timestamps": kwargs["return_timestamps"],
    }
    with torch.inference_mode():
        output = model.generate(input_features=features, **kwargs)
    decoded_ids = output[0]
    if decoding.get("mixed_code"):
        actual_ids = decoded_ids.tolist() if hasattr(decoded_ids, "tolist") else list(decoded_ids)
        # The marker may comprise ordinary tokens, so skip_special_tokens alone
        # does not reliably remove this supplied prompt from the transcript.
        supplied_prefix_returned = actual_ids[:len(prefix)] == prefix
        if supplied_prefix_returned:
            decoded_ids = decoded_ids[len(prefix):]
        result["provenance"]["supplied_prefix_removed_from_text"] = supplied_prefix_returned
        result["provenance"]["returned_prefix_ids"] = actual_ids[:len(prefix)]
    if config.get("timestamps"):
        decoded = processor.tokenizer.decode(decoded_ids, skip_special_tokens=True, output_offsets=True)
        if not isinstance(decoded, dict):
            raise RuntimeFailure("timestamp_output_invalid", "Tokenizer did not return requested timestamp offsets.")
        result["text"] = decoded.get("text", "").strip()
        for item in decoded.get("offsets", []):
            start, end = item.get("timestamp", (None, None))
            if start is None or end is None:
                result["warnings"].append("An incomplete timestamp span was omitted.")
                continue
            result["segments"].append({"start": float(start), "end": float(end), "text": item.get("text", "")})
    else:
        result["text"] = processor.tokenizer.decode(decoded_ids, skip_special_tokens=True).strip()
        result["warnings"].append("This decoding mode returned text only; segment/word timestamps were not measured.")
    result["timing"]["inference_seconds"] = time.perf_counter() - inference_start


def _openvino_execution_devices(model, requested: str) -> dict:
    """Read the compiled graphs, rather than report only a device request."""
    found = {}
    components = getattr(model, "components", {})
    if not isinstance(components, dict) or not {"encoder", "decoder"}.issubset(components):
        raise RuntimeFailure("device_unverified", "OpenVINO did not expose both compiled Whisper components.", "unavailable")
    for name, component in components.items():
        request = getattr(component, "request", None)
        compiled = request.get_compiled_model() if hasattr(request, "get_compiled_model") else request
        try:
            devices = [str(device).upper() for device in compiled.get_property("EXECUTION_DEVICES")]
        except (AttributeError, RuntimeError, TypeError) as exc:
            raise RuntimeFailure("device_unverified", f"Cannot verify OpenVINO execution device for {name}.", "unavailable") from exc
        if not devices or any(not re.fullmatch(r"GPU(?:\.\d+)?", device) for device in devices):
            raise RuntimeFailure("device_mismatch", f"OpenVINO {name} compiled on {devices}; explicit GPU execution is required.", "unavailable")
        if requested != "GPU" and any(device != requested for device in devices):
            raise RuntimeFailure("device_mismatch", f"OpenVINO {name} did not compile on requested {requested}.", "unavailable")
        found[name] = devices
    return found


def _openvino_whisper(spec, audio, raw, config, result):
    decoding = _decoding(spec)
    if config.get("timestamps"):
        raise RuntimeFailure("timestamps_unsupported", "OpenVINO qualification is text-only; playback uses input-window boundaries, not aligned word timestamps.", "unavailable")
    device = str(config.get("device", "GPU")).upper()
    if not re.fullmatch(r"GPU(?:\.\d+)?", device):
        raise RuntimeFailure("device_unsupported", "This OpenVINO profile requires explicit GPU or GPU.n; AUTO, CPU, and silent fallback are disabled.", "unavailable")
    if config.get("dtype", "float16") != "float16":
        raise RuntimeFailure("dtype_unsupported", "This OpenVINO export is qualified for float16 inference only.", "unavailable")
    if _positive(config.get("num_beams", decoding.get("num_beams", 1)), "num_beams", integer=True) != 1:
        raise RuntimeFailure("decoder_mismatch", "This OpenVINO profile is qualified for greedy decoding (num_beams=1) only.", "unavailable")
    cache_dir = local_path(config.get("cache_dir"))
    if not cache_dir.is_dir():
        raise RuntimeFailure("cache_dir_missing", "Create a local OpenVINO cache_dir during setup.", "unavailable")
    conversion = result["provenance"]["conversion"]
    packages = conversion["metadata"]["converter"]["packages"]
    installed = {}
    for name, version in packages.items():
        try:
            installed[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeFailure("dependency_missing", f"Missing pinned OpenVINO dependency: {name}=={version}.", "unavailable") from exc
        if installed[name] != version:
            raise RuntimeFailure("dependency_version_mismatch", f"OpenVINO requires the verified {name}=={version}; installed {installed[name]}.", "unavailable")
    torch, transformers, np, ov, optimum = (_load_dependency(name) for name in _BACKENDS["openvino"])
    if "threads" in config:
        torch.set_num_threads(_positive(config["threads"], "threads", integer=True))
    ov_config = {"PERFORMANCE_HINT": "LATENCY", "INFERENCE_PRECISION_HINT": "f16", "CACHE_DIR": str(cache_dir)}
    target = conversion["export_path"]
    key = ("openvino", spec["artifact_path"], spec.get("source_revision"), target,
           conversion["sidecar_sha256"], conversion["export_manifest_sha256"], device, str(cache_dir))
    load_start = time.perf_counter()
    cached = _CACHE.get(key) if config.get("reuse_model", True) else None
    result["timing"]["compile_seconds"] = 0.0
    if cached:
        processor, model = cached
    else:
        available = [str(item).upper() for item in ov.Core().available_devices]
        if not any(item == device or (device == "GPU" and re.fullmatch(r"GPU(?:\.\d+)?", item)) for item in available):
            raise RuntimeFailure("device_unavailable", f"Requested OpenVINO {device} is unavailable; no CPU fallback was attempted.", "unavailable")
        processor = transformers.AutoProcessor.from_pretrained(spec["artifact_path"], local_files_only=True, trust_remote_code=False)
        model_config = transformers.AutoConfig.from_pretrained(target, local_files_only=True, trust_remote_code=False)
        # In pinned optimum-intel 2.2.0 the public loader tries HF-cache discovery
        # even for an absolute Windows directory, then may auto-export. This
        # local-only loader dispatches Whisper from verified IR without either.
        model = optimum.OVModelForSpeechSeq2Seq._from_pretrained(
            target, config=model_config, device=device, compile=False, local_files_only=True,
            trust_remote_code=False, load_in_8bit=False, ov_config=ov_config,
        )
        result["timing"]["load_seconds"] = time.perf_counter() - load_start
        compile_start = time.perf_counter()
        try:
            model.compile()
        finally:
            result["timing"]["compile_seconds"] = time.perf_counter() - compile_start
    if cached:
        result["timing"]["load_seconds"] = time.perf_counter() - load_start
    execution = _openvino_execution_devices(model, device)
    if not cached and config.get("reuse_model", True):
        _CACHE[key] = (processor, model)
    result["provenance"].update(device=device, device_requested=device, execution_devices=execution,
                                dtype="float16", tensor_device="cpu", tensor_dtype="float32",
                                model_reused=bool(cached), decoding=decoding, packages=installed,
                                ov_config=ov_config, export_path=target, timestamp_kind="input_window_only")
    # Optimum wraps GPU inference with CPU tensors. No Torch device transfer or
    # float16 CPU inference is implied by OpenVINO's FP16 graph execution.
    if raw is not None:
        _generate_whisper(spec, raw, {**config, "num_beams": 1}, result, processor, model,
                          torch, np, "cpu", torch.float32, decoding)


def _whisper_cpp(spec, audio, raw, config, result):
    decoding = _decoding(spec)
    if decoding.get("mixed_code"):
        raise RuntimeFailure("custom_token_unsupported", "Stock whisper.cpp is not validated for Trelis's custom mixedcode prefix. Use transformers.", "unavailable")
    executable = local_path(config.get("executable"))
    if not executable.is_file():
        raise RuntimeFailure("executable_missing", "A local whisper.cpp CLI executable is required.", "unavailable")
    conversion = verify_conversion_provenance(spec, config.get("sidecar_path"))
    if not conversion["ok"]:
        raise RuntimeFailure("conversion_unverified", "; ".join(conversion["issues"]), "unavailable")
    work_dir = local_path(config.get("work_dir"))
    if not work_dir.is_dir():
        raise RuntimeFailure("work_dir_missing", "Create a workspace-local work_dir for transient CLI outputs.", "unavailable")
    device = config.get("device", "cpu")
    if device not in ("cpu", "vulkan", "gpu"):
        raise RuntimeFailure("device_unsupported", "whisper.cpp device must be cpu, vulkan, or gpu.", "unavailable")
    language = decoding.get("language") or "auto"
    beams = _positive(config.get("num_beams", decoding.get("num_beams", 5)), "num_beams", integer=True)
    with tempfile.TemporaryDirectory(prefix="stt-cli-", dir=work_dir) as temporary:
        output = Path(temporary) / "transcript"
        command = [str(executable), "--model", spec["artifact_path"], "--file", str(audio),
                   "--language", language, "--output-json", "--output-file", str(output),
                   "--beam-size", str(beams), "--best-of", str(beams),
                   "--temperature", "0", "--temperature-inc", "0", "--no-fallback", "--suppress-nst"]
        if device == "cpu":
            command.append("--no-gpu")
        if config.get("timestamps") is False:
            command.append("--no-timestamps")
        if "threads" in config:
            command += ["--threads", str(_positive(config["threads"], "threads", integer=True))]
        start = time.perf_counter()
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 check=False, shell=False, timeout=_positive(config.get("timeout_seconds", 300), "timeout_seconds"),
                                 env={**os.environ, **_OFFLINE}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        result["timing"]["process_seconds"] = time.perf_counter() - start
        # The CLI loads its model inside the timed process. Do not call that
        # interval pure inference time or infer a first-partial latency from it.
        result["timing"]["inference_seconds"] = None
        result["timing"]["load_seconds"] = None
        result["provenance"].update(decoding=decoding, device_requested=device, executable=str(executable),
                                     conversion=conversion, command=command, mode="batch_cli", model_reused=False,
                                     runtime_log=process.stderr[-16000:],
                                     runtime_stdout=getattr(process, "stdout", "")[-16000:],
                                     effective_cli_decoding={"language": language, "beam_size": beams, "best_of": beams,
                                         "temperature": 0, "temperature_increment": 0, "fallback": False,
                                         "suppress_non_speech_tokens": True, "timestamps": config.get("timestamps") is not False,
                                         "max_new_tokens": "CLI does not expose the Transformers token cap"})
        result["warnings"].append("Cross-runtime decoder behavior differs: CLI does not expose Transformers max_new_tokens. Timestamp generation is explicitly controlled by --no-timestamps. Compare results before admission.")
        if process.returncode:
            raise RuntimeFailure("cli_failed", f"whisper.cpp exited {process.returncode}: {process.stderr[-2000:]}")
        try:
            payload = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeFailure("cli_output_invalid", f"Missing or invalid whisper.cpp JSON output: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("transcription"), list):
            raise RuntimeFailure("cli_output_invalid", "whisper.cpp JSON has no transcription array.")
        for item in payload["transcription"]:
            offsets = item.get("offsets", {})
            if not isinstance(item.get("text"), str) or not all(type(offsets.get(k)) in (float, int) for k in ("from", "to")):
                raise RuntimeFailure("cli_output_invalid", "whisper.cpp segment is missing text or millisecond offsets.")
            result["segments"].append({"start": offsets["from"] / 1000, "end": offsets["to"] / 1000, "text": item["text"]})
        result["text"] = "".join(segment["text"] for segment in result["segments"]).strip()
        if config.get("timestamps") is False:
            result["provenance"]["unrequested_decoder_segments"] = copy.deepcopy(result["segments"])
            result["segments"] = []
            result["warnings"].append("Text-only request: --no-timestamps disables timestamp-token generation; CLI wrapper offsets are retained only as unrequested provenance, not admitted timestamps.")
        result["warnings"].append("CLI process timing includes model loading; selected GPU use is not independently verified.")


def transcribe(model_spec: dict, audio_path: Path, runtime_config: dict | None = None) -> dict:
    """Transcribe one local normalized WAV clip, returning explicit failure state."""
    return _run(model_spec, audio_path, runtime_config)


def warmup(model_spec: dict, runtime_config: dict | None = None) -> dict:
    """Validate and compile OpenVINO once; does not infer or emit a transcript."""
    return _run(model_spec, None, runtime_config, warmup_only=True)


def _run(model_spec, audio_path, runtime_config, *, warmup_only=False):
    started = time.perf_counter()
    config = dict(runtime_config or {})
    spec = dict(model_spec)
    backend = config.get("backend", "qwen_asr" if "qwen" in str(spec.get("family", "")).lower() else "transformers")
    result = {"status": "failed", "text": "", "segments": [], "warnings": [],
              "timing": {"load_seconds": None, "inference_seconds": None, "total_seconds": None},
              "provenance": {"backend": backend, "model_id": spec.get("model_id"), "repo_id": spec.get("repo_id"),
                             "source_revision": spec.get("source_revision"), "mode": "batch", "offline": True,
                             "live_latency_verified": False}}
    if warmup_only:
        result["operation"] = "warmup"
        result["provenance"]["mode"] = "load_compile_only"
    try:
        if backend not in _BACKENDS:
            raise RuntimeFailure("backend_unknown", f"Unknown ASR backend: {backend}", "unavailable")
        if warmup_only and backend != "openvino":
            raise RuntimeFailure("warmup_unsupported", "Load/compile warmup is implemented only for OpenVINO.", "unavailable")
        if "artifact_path" in config:
            spec["artifact_path"] = config["artifact_path"]
        if backend == "whisper_cpp" and "model_path" in config:
            spec["artifact_path"] = config["model_path"]
        verify_started = time.perf_counter()
        verification = verify_model_assets(spec, backend)
        result["timing"]["verification_seconds"] = time.perf_counter() - verify_started
        result["provenance"]["asset_verification"] = verification
        if not verification["ok"]:
            raise RuntimeFailure("model_assets_invalid", "; ".join(verification["issues"]), "unavailable")
        spec["artifact_path"] = verification["artifact_path"]
        result["provenance"]["artifact_path"] = spec["artifact_path"]
        if backend == "openvino":
            conversion_started = time.perf_counter()
            conversion = verify_openvino_assets(spec, config.get("export_path"), verification)
            result["timing"]["conversion_verification_seconds"] = time.perf_counter() - conversion_started
            result["provenance"]["conversion"] = conversion
            if not conversion["ok"]:
                raise RuntimeFailure("conversion_unverified", "; ".join(conversion["issues"]), "unavailable")
        audio, raw = None, None
        if not warmup_only:
            audio = local_path(audio_path)
            raw, duration = _read_pcm(audio)
            result["audio_seconds"] = duration
        # Environment variables and cached model objects are process-global.
        # Serialize calls; use separate worker processes for parallel benchmarks.
        with _LOCK, _offline_environment():
            {"transformers": _hf_whisper, "whisper_cpp": _whisper_cpp, "openvino": _openvino_whisper}[backend](spec, audio, raw, config, result)
        for segment in result["segments"]:
            if not (0 <= segment["start"] <= segment["end"] <= duration + 0.1):
                result["diagnostics"] = {"rejected_text": result["text"], "rejected_segments": copy.deepcopy(result["segments"]),
                                         "supplied_audio_seconds": duration}
                raise RuntimeFailure("timestamp_output_invalid", "Backend timestamps fall outside the supplied clip.")
        result["status"] = "ok"
    except RuntimeFailure as exc:
        result.update(status=exc.status, error={"code": exc.code, "message": str(exc)})
    except subprocess.TimeoutExpired:
        result.update(error={"code": "inference_timeout", "message": "The local CLI exceeded its configured timeout."})
    except (OSError, ValueError, TypeError, AttributeError, wave.Error) as exc:
        result.update(error={"code": "runtime_error", "message": str(exc)})
    except Exception as exc:
        result.update(error={"code": "inference_failed", "message": f"{type(exc).__name__}: {exc}"})
    if result["status"] != "ok":
        result["text"], result["segments"] = "", []
    result["timing"]["total_seconds"] = time.perf_counter() - started
    return result
