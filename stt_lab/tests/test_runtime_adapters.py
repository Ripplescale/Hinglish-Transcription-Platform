"""Adapter contract tests; fake inference is never a model quality benchmark."""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from sttbench.runtime import inspect_capabilities, reset_runtime_cache, transcribe
from sttbench.runtime import verify_conversion_provenance, verify_model_assets
from sttbench.runtime import api
from sttbench.runtime import assets as runtime_assets


class Array:
    def __init__(self, values):
        self.values = values

    def astype(self, dtype):
        return self

    def __truediv__(self, divisor):
        return Array([value / divisor for value in self.values])


class Tensor:
    def __init__(self, data=None):
        self.data = data
        self.moves = []

    def to(self, *args, **kwargs):
        self.moves.append((args, kwargs))
        return self


class FakeTokenizer:
    def __init__(self):
        self.vocab = {"<|hi|>": 12, "<|en|>": 13, "<|transcribe|>": 15, "<|notimestamps|>": 16}
        # This deliberately expands to multiple IDs: never invent or hardcode
        # the custom marker's token ID from a different tokenizer revision.
        self.mixed_ids = [90, 91]
        self.text = " Kal meeting at three. "
        self.offsets = [{"timestamp": (0.0, 0.25), "text": "Kal meeting at three."}]

    def get_vocab(self):
        return self.vocab

    def __call__(self, text, *, add_special_tokens):
        assert text == "<|mixedcode|>" and add_special_tokens is False
        return SimpleNamespace(input_ids=self.mixed_ids)

    def decode(self, output, skip_special_tokens, output_offsets=False):
        return {"text": self.text, "offsets": self.offsets} if output_offsets else self.text


class FakeProcessor:
    def __init__(self):
        self.tokenizer = FakeTokenizer()
        self.calls = []
        self.features, self.mask = Tensor(), Tensor()

    def __call__(self, samples, **kwargs):
        self.calls.append((samples, kwargs))
        return SimpleNamespace(input_features=self.features, attention_mask=self.mask)


class FakeModel:
    def __init__(self):
        self.config = SimpleNamespace(decoder_start_token_id=11, max_target_positions=448)
        self.generation_config = SimpleNamespace(forced_decoder_ids=[[1, 999]], language="fr")
        self.calls = []

    def to(self, device):
        return self

    def eval(self):
        return self

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return [[101, 102]]


class RuntimeContracts(unittest.TestCase):
    def setUp(self):
        reset_runtime_cache()
        self.temp = tempfile.TemporaryDirectory(prefix="runtime-test-", dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        self.assets = self.root / "model"
        self.assets.mkdir()
        for name in ("config.json", "preprocessor_config.json", "tokenizer.json"):
            (self.assets / name).write_text("{}", encoding="utf-8")
        (self.assets / "model.safetensors").write_bytes(b"fake weights for contract tests")
        self.wav = self.root / "clip.wav"
        with wave.open(str(self.wav), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(struct.pack("<hhhh", -32768, 0, 16384, 32767) * 2000)
        self.spec = {"model_id": "swift", "repo_id": "Oriserve/Whisper-Hindi2Hinglish-Swift",
                     "family": "whisper", "source_revision": "a" * 40,
                     "artifact_path": str(self.assets), "decoding": {"task": "transcribe", "language": "en"}}
        self.processor, self.model = FakeProcessor(), FakeModel()
        self.processor_load = Mock(return_value=self.processor)
        self.model_load = Mock(return_value=self.model)
        self.torch = SimpleNamespace(float32="fp32", float16="fp16", bfloat16="bf16", long="long",
                                     cuda=SimpleNamespace(is_available=lambda: False),
                                     inference_mode=contextlib.nullcontext,
                                     tensor=lambda values, **kwargs: Tensor(values), set_num_threads=Mock())
        self.modules = {
            "torch": self.torch,
            "transformers": SimpleNamespace(AutoProcessor=SimpleNamespace(from_pretrained=self.processor_load),
                                             AutoModelForSpeechSeq2Seq=SimpleNamespace(from_pretrained=self.model_load)),
            "numpy": SimpleNamespace(frombuffer=lambda data, dtype: Array(list(struct.unpack("<" + "h" * (len(data) // 2), data)))),
        }

    def tearDown(self):
        reset_runtime_cache()
        self.temp.cleanup()

    def fake_dependencies(self):
        return patch.object(api, "_load_dependency", side_effect=lambda name: self.modules[name])

    def test_oriserve_local_loading_decoding_normalization_and_cache(self):
        previous_offline = os.environ.get("HF_HUB_OFFLINE")
        def load_model(*args, **kwargs):
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
            return self.model
        self.model_load.side_effect = load_model
        with self.fake_dependencies():
            first = transcribe(self.spec, self.wav, {"threads": 2})
            second = transcribe(self.spec, self.wav)
        self.assertEqual(first["status"], "ok", first)
        self.assertEqual(first["text"], "Kal meeting at three.")
        self.assertEqual(first["segments"], [])
        self.assertNotIn("streaming_events", first)
        self.assertFalse(first["provenance"]["live_latency_verified"])
        self.assertFalse(first["provenance"]["model_reused"])
        self.assertTrue(second["provenance"]["model_reused"])
        self.model_load.assert_called_once_with(str(self.assets.resolve()), local_files_only=True,
                                                trust_remote_code=False, use_safetensors=True, torch_dtype="fp32")
        self.processor_load.assert_called_once_with(str(self.assets.resolve()), local_files_only=True, trust_remote_code=False)
        generate = self.model.calls[0]
        self.assertEqual((generate["language"], generate["task"]), ("en", "transcribe"))
        self.assertEqual((generate["num_beams"], generate["max_new_tokens"]), (5, 256))
        self.assertIsNone(generate["generation_config"].forced_decoder_ids)
        self.assertEqual(self.model.generation_config.forced_decoder_ids, [[1, 999]])
        self.assertFalse(generate["return_timestamps"])
        self.assertEqual(self.processor.calls[0][0].values[:3], [-1.0, 0.0, 0.5])
        self.assertEqual(self.processor.calls[0][1]["sampling_rate"], 16000)
        self.assertEqual(os.environ.get("HF_HUB_OFFLINE"), previous_offline)

    def test_registry_generation_profile_and_explicit_runtime_overrides_reach_model(self):
        spec = {**self.spec, "decoding": {"task": "transcribe", "language": "en",
                                         "num_beams": 3, "max_new_tokens": 96}}
        self.model.generation_config.suppress_tokens = [7, 8, 9]
        with self.fake_dependencies():
            declared = transcribe(spec, self.wav)
            overridden = transcribe(spec, self.wav, {"num_beams": 2, "max_new_tokens": 64})
        self.assertEqual(declared["status"], "ok", declared)
        self.assertEqual(overridden["status"], "ok", overridden)
        self.assertEqual((self.model.calls[0]["num_beams"], self.model.calls[0]["max_new_tokens"]), (3, 96))
        self.assertEqual((self.model.calls[1]["num_beams"], self.model.calls[1]["max_new_tokens"]), (2, 64))
        self.assertEqual(overridden["provenance"]["effective_generation"], {
            "task": "transcribe", "language": "en", "do_sample": False,
            "num_beams": 2, "max_new_tokens": 64, "return_timestamps": False,
        })
        self.assertEqual(self.model.calls[0]["generation_config"].suppress_tokens, [7, 8, 9])
        self.assertEqual(self.model.generation_config.suppress_tokens, [7, 8, 9])

    def test_trelis_encodes_full_custom_prefix_and_caps_generation_length(self):
        spec = {**self.spec, "repo_id": "Trelis/tara", "decoding": {"mode": "mixedcode"}}
        with self.fake_dependencies():
            result = transcribe(spec, self.wav, {"max_new_tokens": 444})
        self.assertEqual(result["status"], "ok", result)
        generate = self.model.calls[0]
        self.assertEqual(generate["decoder_input_ids"].data, [[11, 12, 90, 91, 15, 16]])
        self.assertEqual(generate["max_new_tokens"], 442)
        self.assertEqual(generate["language"], "hi")
        self.assertEqual(result["provenance"]["decoder_prefix_ids"], [11, 12, 90, 91, 15, 16])

    def test_trelis_english_clip_uses_declared_en_without_mixed_marker(self):
        spec = {**self.spec, "repo_id": "Trelis/tara", "decoding": {"language": "en", "mixed_code": False}}
        with self.fake_dependencies():
            result = transcribe(spec, self.wav)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.model.calls[0]["language"], "en")
        self.assertNotIn("decoder_input_ids", self.model.calls[0])

    def test_trelis_supplied_mixed_prompt_is_not_part_of_recognized_text(self):
        spec = {**self.spec, "repo_id": "Trelis/tara", "decoding": {"mode": "mixedcode"}}
        self.model.generate = Mock(return_value=[[11, 12, 90, 91, 15, 16, 101, 102]])
        self.processor.tokenizer.decode = Mock(return_value="Kal meeting")
        with self.fake_dependencies():
            result = transcribe(spec, self.wav)
        self.assertEqual(result["status"], "ok", result)
        self.processor.tokenizer.decode.assert_called_once_with([101, 102], skip_special_tokens=True)
        self.assertTrue(result["provenance"]["supplied_prefix_removed_from_text"])

    def test_decoder_errors_fail_explicitly_before_loading_weights(self):
        with self.fake_dependencies():
            wrong = transcribe({**self.spec, "decoding": {"language": "hi"}}, self.wav)
            unsupported = transcribe({**self.spec, "repo_id": "Trelis/tara"}, self.wav, {"timestamps": True})
        self.assertEqual(wrong["error"]["code"], "decoder_mismatch")
        self.assertEqual(unsupported["status"], "unavailable")
        self.assertEqual(unsupported["error"]["code"], "timestamps_unsupported")
        self.model_load.assert_not_called()

    def test_timestamp_output_uses_backend_offsets_and_rejects_out_of_clip(self):
        with self.fake_dependencies():
            result = transcribe(self.spec, self.wav, {"timestamps": True})
            self.processor.tokenizer.offsets[0]["timestamp"] = (0, 90)
            invalid = transcribe(self.spec, self.wav, {"timestamps": True})
        self.assertEqual(result["segments"], [{"start": 0.0, "end": 0.25, "text": "Kal meeting at three."}])
        self.assertEqual(invalid["error"]["code"], "timestamp_output_invalid")
        self.assertEqual((invalid["text"], invalid["segments"]), ("", []))
        self.assertEqual(invalid["status"], "failed")
        self.assertEqual(invalid["diagnostics"]["rejected_text"], "Kal meeting at three.")
        self.assertEqual(invalid["diagnostics"]["rejected_segments"], [
            {"start": 0.0, "end": 90.0, "text": "Kal meeting at three."}])
        self.assertEqual(invalid["diagnostics"]["supplied_audio_seconds"], 0.5)

    def test_unimplemented_openvino_and_remote_paths_do_not_import_inference(self):
        with patch.object(api, "_load_dependency") as dependency:
            unavailable = transcribe(self.spec, self.wav, {"backend": "openvino_srota"})
            remote = transcribe({**self.spec, "artifact_path": "https://huggingface.co/model"}, self.wav)
        self.assertEqual(unavailable["status"], "unavailable")
        self.assertEqual(unavailable["error"]["code"], "backend_unknown")
        self.assertEqual(remote["error"]["code"], "model_assets_invalid")
        dependency.assert_not_called()

    def test_missing_dependencies_are_a_capability_state(self):
        with patch.object(api.importlib, "import_module", side_effect=ImportError("optional package absent")):
            result = transcribe(self.spec, self.wav)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["error"]["code"], "dependency_missing")

    def test_asset_hashes_missing_shards_and_path_traversal(self):
        good_hash = hashlib.sha256((self.assets / "model.safetensors").read_bytes()).hexdigest()
        result = verify_model_assets({**self.spec, "asset_sha256": {"model.safetensors": good_hash}})
        self.assertEqual(result["integrity"], "provided_hashes_verified")
        self.assertFalse(verify_model_assets({**self.spec, "asset_sha256": {"model.safetensors": "0" * 64}})["ok"])
        index = self.assets / "model.safetensors.index.json"
        index.write_text(json.dumps({"weight_map": {"tensor": "missing.safetensors"}}))
        self.assertIn("Missing or empty weights", " ".join(verify_model_assets(self.spec)["issues"]))
        index.write_text(json.dumps({"weight_map": {"tensor": "../escape.safetensors"}}))
        self.assertIn("escapes", " ".join(verify_model_assets(self.spec)["issues"]))

    def conversion_fixture(self):
        binary = self.root / "swift.bin"
        binary.write_bytes(b"converted fixture")
        exe = self.root / "whisper-cli.exe"
        exe.write_bytes(b"not executable: subprocess is mocked")
        metadata = {"source_repo_id": self.spec["repo_id"], "source_revision": self.spec["source_revision"],
                    "source_sha256": {"model.safetensors": "f" * 64},
                    "converter": {"name": "whisper.cpp/convert-h5-to-ggml.py", "revision": "testcommit"},
                    "quantization": "q5_0", "output_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        sidecar = binary.with_suffix(".bin.provenance.json")
        sidecar.write_text(json.dumps(metadata), encoding="utf-8")
        spec = {**self.spec, "artifact_path": str(binary)}
        config = {"backend": "whisper_cpp", "executable": str(exe), "work_dir": str(self.root), "threads": 2}
        return spec, config, binary, sidecar

    def test_download_manifest_verifies_identity_hashes_sizes_and_loaded_weights(self):
        manifest = {key: self.spec[key] for key in ("model_id", "repo_id", "source_revision")}
        manifest["license"] = "Apache-2.0"
        manifest["files"] = [{"path": file.name, "size": file.stat().st_size,
                              "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
                             for file in self.assets.iterdir()]
        manifest_path = self.assets / "artifact-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        verified = verify_model_assets(self.spec)
        self.assertTrue(verified["ok"], verified)
        self.assertEqual(verified["integrity"], "manifest_files_verified")
        self.assertFalse(verify_model_assets({**self.spec, "source_revision": "wrong"})["ok"])
        manifest["files"] = [entry for entry in manifest["files"] if not entry["path"].endswith(".safetensors")]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertIn("Manifest omits loaded weights", " ".join(verify_model_assets(self.spec)["issues"]))

    def test_truncated_wav_rejected_before_inference(self):
        data = self.wav.read_bytes()
        self.wav.write_bytes(data[:-16])
        with patch.object(api, "_load_dependency") as dependency:
            result = transcribe(self.spec, self.wav)
        self.assertEqual(result["error"]["code"], "audio_truncated")
        dependency.assert_not_called()

    def test_cli_uses_argument_vector_outputs_millisecond_offsets_and_no_fake_inference_timing(self):
        spec, config, _, _ = self.conversion_fixture()
        captured = []
        def run(command, **kwargs):
            captured.append((command, kwargs))
            self.assertEqual(command[command.index("--beam-size")+1], "5")
            self.assertEqual(command[command.index("--temperature-inc")+1], "0")
            self.assertIn("--no-fallback", command)
            self.assertIn("--suppress-nst", command)
            output = Path(command[command.index("--output-file") + 1]).with_suffix(".json")
            output.write_text(json.dumps({"transcription": [
                {"text": "Kal", "offsets": {"from": 0, "to": 100}},
                {"text": " meeting", "offsets": {"from": 100, "to": 250}},
            ]}), encoding="utf-8")
            return SimpleNamespace(returncode=0, stderr="")
        with patch.object(api.subprocess, "run", side_effect=run):
            cpu = transcribe(spec, self.wav, config)
            vulkan = transcribe(spec, self.wav, {**config, "device": "vulkan"})
        self.assertEqual(cpu["status"], "ok", cpu)
        self.assertEqual(cpu["text"], "Kal meeting")
        self.assertEqual(cpu["segments"][1]["end"], 0.25)
        self.assertIsNone(cpu["timing"]["inference_seconds"])
        self.assertGreaterEqual(cpu["timing"]["process_seconds"], 0)
        self.assertIn("--no-gpu", captured[0][0])
        self.assertNotIn("--no-gpu", captured[1][0])
        self.assertEqual(captured[0][0][captured[0][0].index("--language") + 1], "en")
        self.assertFalse(captured[0][1]["shell"])
        self.assertEqual(captured[0][1]["env"]["HF_HUB_OFFLINE"], "1")
        self.assertEqual(vulkan["provenance"]["device_requested"], "vulkan")
        self.assertFalse(cpu["provenance"]["conversion"]["source_hashes_independently_verified"])

    def test_cli_rejects_modified_binary_and_mismatched_source_revision(self):
        spec, config, binary, _ = self.conversion_fixture()
        wrong_revision = verify_conversion_provenance({**spec, "source_revision": "b" * 40})
        self.assertFalse(wrong_revision["ok"])
        binary.write_bytes(b"changed after conversion")
        with patch.object(api.subprocess, "run") as process:
            result = transcribe(spec, self.wav, config)
        self.assertEqual(result["error"]["code"], "conversion_unverified")
        process.assert_not_called()

    def test_cli_invalid_timestamps_remain_failed_with_rejected_diagnostics(self):
        spec, config, _, _ = self.conversion_fixture()
        def run(command, **kwargs):
            output = Path(command[command.index("--output-file") + 1]).with_suffix(".json")
            output.write_text(json.dumps({"transcription": [
                {"text": "Kal", "offsets": {"from": 0, "to": 100}},
                {"text": " meeting", "offsets": {"from": 100, "to": 90000}},
            ]}), encoding="utf-8")
            return SimpleNamespace(returncode=0, stderr="synthetic decoder diagnostic")
        for timestamp_config in ({}, {"timestamps": True}):
            with self.subTest(timestamp_config=timestamp_config), patch.object(api.subprocess, "run", side_effect=run):
                result = transcribe(spec, self.wav, {**config, **timestamp_config})
            self.assertEqual(result["status"], "failed", result)
            self.assertEqual(result["error"]["code"], "timestamp_output_invalid")
            self.assertEqual((result["text"], result["segments"]), ("", []))
            self.assertEqual(result["diagnostics"], {
                "rejected_text": "Kal meeting", "supplied_audio_seconds": 0.5,
                "rejected_segments": [{"start": 0.0, "end": 0.1, "text": "Kal"},
                                      {"start": 0.1, "end": 90.0, "text": " meeting"}],
            })
            self.assertFalse(result["provenance"]["live_latency_verified"])

    def test_cli_explicit_text_only_retains_unadmitted_raw_offsets(self):
        spec, config, _, _ = self.conversion_fixture()
        def run(command, **kwargs):
            self.assertIn("--no-timestamps", command)
            output = Path(command[command.index("--output-file") + 1]).with_suffix(".json")
            output.write_text(json.dumps({"transcription": [
                {"text": "Kal meeting", "offsets": {"from": 0, "to": 90000}},
            ]}), encoding="utf-8")
            return SimpleNamespace(returncode=0, stderr="")
        with patch.object(api.subprocess, "run", side_effect=run):
            result = transcribe(spec, self.wav, {**config, "timestamps": False})
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["text"], "Kal meeting")
        self.assertEqual(result["segments"], [])
        self.assertEqual(result["provenance"]["unrequested_decoder_segments"], [
            {"start": 0.0, "end": 90.0, "text": "Kal meeting"}])
        self.assertIn("--no-timestamps disables timestamp-token generation", " ".join(result["warnings"]))
        self.assertFalse(result["provenance"]["effective_cli_decoding"]["timestamps"])
        self.assertFalse(result["provenance"]["live_latency_verified"])
        self.assertNotIn("diagnostics", result)

    def test_conversion_hash_cache_rechecks_modified_mtime_or_size(self):
        spec, _, binary, _ = self.conversion_fixture()
        original = binary.read_bytes()
        original_stat = binary.stat()
        with patch.object(runtime_assets, "sha256_file", wraps=runtime_assets.sha256_file) as hash_file:
            first = verify_conversion_provenance(spec)
            self.assertTrue(first["ok"], first)
            self.assertTrue(verify_conversion_provenance(spec)["ok"])
            self.assertEqual(hash_file.call_count, 1)
            self.assertIn("mtime_ns", first["hash_cache_policy"])

            # Even unchanged bytes are rechecked when the file modification time changes.
            os.utime(binary, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000_000))
            self.assertTrue(verify_conversion_provenance(spec)["ok"])
            self.assertEqual(hash_file.call_count, 2)

            # Same-size replacement must invalidate the cached digest through mtime.
            binary.write_bytes(b"X" + original[1:])
            os.utime(binary, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 2_000_000_000))
            modified = verify_conversion_provenance(spec)
            self.assertFalse(modified["ok"])
            self.assertIn("SHA-256", " ".join(modified["issues"]))
            self.assertEqual(hash_file.call_count, 3)

            # A size change is independently detected even if mtime is held constant.
            binary.write_bytes(original + b"X")
            os.utime(binary, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 2_000_000_000))
            resized = verify_conversion_provenance(spec)
            self.assertFalse(resized["ok"])
            self.assertEqual(hash_file.call_count, 4)

    def test_capabilities_checks_presence_without_importing_optional_packages(self):
        with patch.object(api.importlib.util, "find_spec", return_value=None), patch.object(api, "_load_dependency") as dependency:
            capabilities = inspect_capabilities()
        self.assertFalse(capabilities["transformers"]["dependencies_available"])
        self.assertNotIn("openvino_srota", capabilities)
        self.assertNotIn("qwen_asr", capabilities)
        self.assertFalse(capabilities["whisper_cpp"]["executable_available"])
        dependency.assert_not_called()


if __name__ == "__main__":
    unittest.main()
