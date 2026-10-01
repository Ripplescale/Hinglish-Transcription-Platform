"""Synthetic OpenVINO contracts; these tests do not measure model accuracy."""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

from sttbench.runtime import api
from sttbench.runtime.assets import verify_model_assets, verify_openvino_assets


class Tensor:
    def __init__(self, value=None):
        self.value = value
        self.moves = []

    def to(self, *args, **kwargs):
        self.moves.append((args, kwargs))
        return self

    def astype(self, dtype):
        return self

    def __truediv__(self, value):
        return self


class OpenVINOContracts(unittest.TestCase):
    def setUp(self):
        api.reset_runtime_cache()
        self.temp = tempfile.TemporaryDirectory(prefix="openvino-test-", dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        self.source, self.export, self.cache = (self.root / name for name in ("source", "export", "cache"))
        for directory in (self.source, self.export, self.cache):
            directory.mkdir()
        self.spec = {"model_id": "trelis", "repo_id": "Trelis/whisper-hinglish-preview",
                     "family": "whisper", "source_revision": "a" * 40,
                     "artifact_path": str(self.source), "decoding": {"language": "hi", "mixed_code": True}}
        self.config = {"backend": "openvino", "export_path": str(self.export),
                       "cache_dir": str(self.cache), "device": "GPU", "dtype": "float16"}
        model_config = {"model_type": "whisper", "vocab_size": 51867,
                        "decoder_start_token_id": 50258, "max_target_positions": 448}
        generation_config = {"num_beams": 1, "suppress_tokens": [7, 8], "transformers_version": "source-version"}
        self.write(self.source / "config.json", model_config)
        self.write(self.source / "generation_config.json", generation_config)
        for name in ("preprocessor_config.json", "tokenizer.json"):
            self.write(self.source / name, {})
        (self.source / "model.safetensors").write_bytes(b"synthetic source weights")
        source_manifest = {key: self.spec[key] for key in ("model_id", "repo_id", "source_revision")}
        source_manifest["files"] = [{"path": f.name, "size": f.stat().st_size, "sha256": self.digest(f)}
                                    for f in self.source.iterdir()]
        self.write(self.source / "artifact-manifest.json", source_manifest)
        self.write(self.export / "config.json", model_config)
        self.write(self.export / "generation_config.json", {**generation_config, "transformers_version": "export-version"})
        self.write(self.export / "preprocessor_config.json", {})
        for component in ("encoder", "decoder"):
            for extension in ("xml", "bin"):
                (self.export / f"openvino_{component}_model.{extension}").write_bytes(b"synthetic OpenVINO graph")
        self.packages = {"openvino": "2026.4.0", "optimum-intel": "2.2.0", "transformers": "4.57.6",
                         "torch": "2.8.0+cpu", "numpy": "2.2.6"}
        self.receipt = {
            "schema_version": 1, "model_id": self.spec["model_id"], "source_repo_id": self.spec["repo_id"],
            "source_revision": self.spec["source_revision"],
            "source_manifest_sha256": self.digest(self.source / "artifact-manifest.json"),
            "converter": {"name": "optimum.exporters.openvino.main_export", "version": "2.2.0",
                          "packages": self.packages, "task": "automatic-speech-recognition-with-past",
                          "dtype": "fp16", "stateful": True, "convert_tokenizer": False, "load_in_8bit": False},
        }
        self.seal_export()
        self.wav = self.root / "clip.wav"
        with wave.open(str(self.wav), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 320000)
        self.prefix = [50258, 50276, 51866, 50360, 50364]
        self.tokenizer = SimpleNamespace(
            get_vocab=lambda: {"<|hi|>": 50276, "<|en|>": 50259, "<|transcribe|>": 50360, "<|notimestamps|>": 50364},
            decode=Mock(return_value="कल design review है।"),
        )
        # __call__ is looked up on the type rather than an instance namespace.
        class Tokenizer:
            def __call__(inner, text, **kwargs):
                self.assertEqual((text, kwargs), ("<|mixedcode|>", {"add_special_tokens": False}))
                return SimpleNamespace(input_ids=[51866])
        token = Tokenizer()
        token.get_vocab, token.decode = self.tokenizer.get_vocab, self.tokenizer.decode
        self.features, self.mask = Tensor(), Tensor()
        class Processor:
            tokenizer = token

            def __call__(inner, samples, **kwargs):
                return SimpleNamespace(input_features=self.features, attention_mask=self.mask)
        self.processor = Processor()
        self.execution = ["GPU.0"]
        compiled = SimpleNamespace(get_property=lambda key: list(self.execution))
        self.model = SimpleNamespace(
            config=SimpleNamespace(**model_config),
            generation_config=SimpleNamespace(num_beams=1, forced_decoder_ids=[[1, 999]], language="fr", suppress_tokens=[7, 8]),
            generate=Mock(return_value=[self.prefix + [100, 101]]), compile=Mock(),
            components={"encoder": SimpleNamespace(request=compiled),
                        "decoder": SimpleNamespace(request=SimpleNamespace(get_compiled_model=lambda: compiled))},
        )
        self.processor_load = Mock(return_value=self.processor)
        self.model_load = Mock(return_value=self.model)
        self.config_load = Mock(return_value=SimpleNamespace(**model_config))
        self.devices = ["CPU", "GPU.0"]
        self.modules = {
            "torch": SimpleNamespace(float32="fp32", tensor=lambda value, **kwargs: Tensor(value), long="long",
                                     inference_mode=contextlib.nullcontext, set_num_threads=Mock()),
            "numpy": SimpleNamespace(frombuffer=lambda *args, **kwargs: Tensor()),
            "transformers": SimpleNamespace(AutoProcessor=SimpleNamespace(from_pretrained=self.processor_load),
                                             AutoConfig=SimpleNamespace(from_pretrained=self.config_load)),
            "openvino": SimpleNamespace(Core=lambda: SimpleNamespace(available_devices=list(self.devices))),
            "optimum.intel.openvino": SimpleNamespace(OVModelForSpeechSeq2Seq=SimpleNamespace(_from_pretrained=self.model_load)),
        }
        self.dependencies = patch.object(api, "_load_dependency", side_effect=self.modules.__getitem__)
        self.versions = patch.object(api.importlib.metadata, "version", side_effect=self.packages.__getitem__)
        self.dependencies.start()
        self.versions.start()

    def tearDown(self):
        self.dependencies.stop()
        self.versions.stop()
        api.reset_runtime_cache()
        self.temp.cleanup()

    @staticmethod
    def write(path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def seal_export(self):
        files = {f.name: {"size": f.stat().st_size, "sha256": self.digest(f)} for f in self.export.iterdir()
                 if f.name not in ("export-complete.json", "conversion-provenance.json")}
        self.write(self.export / "export-complete.json", {"files": files, "vocab_size": 51867, "load_in_8bit": False})
        self.receipt["export_manifest_sha256"] = self.digest(self.export / "export-complete.json")
        self.write(self.export / "conversion-provenance.json", self.receipt)

    def run_clip(self, **config):
        return api.transcribe(self.spec, self.wav, {**self.config, **config})

    def test_real_backend_identity_explicit_gpu_exact_prefix_and_no_fabricated_timestamps(self):
        previous = os.environ.get("OPENVINO_TELEMETRY_DISABLED")
        def load_model(*args, **kwargs):
            self.assertEqual(os.environ["HF_HUB_OFFLINE"], "1")
            self.assertEqual(os.environ["OPENVINO_TELEMETRY_DISABLED"], "1")
            return self.model
        self.model_load.side_effect = load_model
        result = self.run_clip()
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["text"], "कल design review है।")
        self.assertEqual(result["audio_seconds"], 20.0)
        self.assertEqual(result["segments"], [])
        self.assertEqual(result["provenance"]["backend"], "openvino")
        self.assertEqual(result["provenance"]["execution_devices"], {"encoder": ["GPU.0"], "decoder": ["GPU.0"]})
        self.assertEqual(result["provenance"]["timestamp_kind"], "input_window_only")
        self.assertEqual(result["provenance"]["effective_generation"], {
            "task": "transcribe", "language": "hi", "do_sample": False,
            "num_beams": 1, "max_new_tokens": 440, "return_timestamps": False,
        })
        self.assertEqual(self.model.generate.call_args.kwargs["decoder_input_ids"].value, [self.prefix])
        self.assertEqual(self.model.generate.call_args.kwargs["generation_config"].suppress_tokens, [7, 8])
        self.assertEqual(self.model.generation_config.forced_decoder_ids, [[1, 999]])
        self.tokenizer.decode.assert_called_once_with([100, 101], skip_special_tokens=True)
        self.processor_load.assert_called_once_with(str(self.source.resolve()), local_files_only=True, trust_remote_code=False)
        load = self.model_load.call_args.kwargs
        self.assertEqual((load["device"], load["compile"], load["load_in_8bit"], load["local_files_only"]), ("GPU", False, False, True))
        self.assertEqual(load["ov_config"]["INFERENCE_PRECISION_HINT"], "f16")
        self.assertEqual(self.features.moves[0], ((), {"device": "cpu", "dtype": "fp32"}))
        for timing in ("load_seconds", "compile_seconds", "inference_seconds", "conversion_verification_seconds"):
            self.assertGreaterEqual(result["timing"][timing], 0)
        self.assertFalse(result["provenance"]["live_latency_verified"])
        self.assertEqual(os.environ.get("OPENVINO_TELEMETRY_DISABLED"), previous)

    def test_cache_reuses_compiled_graph_but_never_reuses_generation_settings(self):
        first = self.run_clip()
        second = self.run_clip(max_new_tokens=900)
        self.assertEqual((first["status"], second["status"]), ("ok", "ok"))
        self.assertFalse(first["provenance"]["model_reused"])
        self.assertTrue(second["provenance"]["model_reused"])
        self.assertEqual(second["timing"]["compile_seconds"], 0)
        self.assertEqual(second["provenance"]["max_new_tokens"], 443)
        self.model_load.assert_called_once()
        self.model.compile.assert_called_once()
        self.assertEqual(self.model.generate.call_count, 2)

    def test_warmup_only_validates_loads_compiles_then_real_window_reuses_model(self):
        warmup = api.warmup(self.spec, self.config)
        self.assertEqual(warmup["status"], "ok", warmup)
        self.assertEqual(warmup["operation"], "warmup")
        self.assertNotIn("audio_seconds", warmup)
        self.assertIsNone(warmup["timing"]["inference_seconds"])
        self.assertEqual((warmup["text"], warmup["segments"]), ("", []))
        self.model.generate.assert_not_called()
        result = self.run_clip()
        self.assertTrue(result["provenance"]["model_reused"])
        self.model.compile.assert_called_once()

    def test_english_non_mixed_profile_preserved(self):
        self.spec["decoding"] = {"language": "en", "mixed_code": False}
        result = self.run_clip()
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(self.model.generate.call_args.kwargs["language"], "en")
        self.assertNotIn("decoder_input_ids", self.model.generate.call_args.kwargs)

    def test_cpu_auto_beams_dtype_and_timestamps_rejected(self):
        for settings, code in (({"device": "CPU"}, "device_unsupported"), ({"device": "AUTO:GPU,CPU"}, "device_unsupported"),
                               ({"num_beams": 2}, "decoder_mismatch"), ({"dtype": "float32"}, "dtype_unsupported"),
                               ({"timestamps": True}, "timestamps_unsupported")):
            with self.subTest(settings=settings):
                result = self.run_clip(**settings)
                self.assertEqual(result["error"]["code"], code, result)
        self.model_load.assert_not_called()

    def test_no_gpu_fails_without_loading_or_falling_back(self):
        self.devices[:] = ["CPU"]
        result = self.run_clip()
        self.assertEqual(result["error"]["code"], "device_unavailable", result)
        self.model_load.assert_not_called()

    def test_actual_compiled_cpu_or_wrong_gpu_fails_and_is_not_cached(self):
        self.execution[:] = ["CPU"]
        failed = self.run_clip()
        self.assertEqual(failed["error"]["code"], "device_mismatch", failed)
        self.assertEqual(api._CACHE, {})
        self.execution[:] = ["GPU.1"]
        self.devices[:] = ["GPU.0", "GPU.1"]
        wrong = self.run_clip(device="GPU.0")
        self.assertEqual(wrong["error"]["code"], "device_mismatch", wrong)
        self.model.generate.assert_not_called()

    def test_unverifiable_compiled_components_fail(self):
        self.model.components.pop("decoder")
        result = self.run_clip()
        self.assertEqual(result["error"]["code"], "device_unverified", result)
        self.model.generate.assert_not_called()

    def test_changed_ir_fails_even_after_model_cached(self):
        self.assertEqual(self.run_clip()["status"], "ok")
        (self.export / "openvino_decoder_model.bin").write_bytes(b"modified weights")
        result = self.run_clip()
        self.assertEqual(result["error"]["code"], "conversion_unverified", result)
        self.assertEqual(self.model.generate.call_count, 1)

    def test_source_receipt_and_export_manifest_pins_fail_closed(self):
        for key in ("source_revision", "source_manifest_sha256", "export_manifest_sha256"):
            with self.subTest(key=key):
                wrong = {**self.receipt, key: "wrong"}
                self.write(self.export / "conversion-provenance.json", wrong)
                result = self.run_clip()
                self.assertEqual(result["error"]["code"], "conversion_unverified", result)
        self.model_load.assert_not_called()

    def test_runtime_package_drift_is_explicit(self):
        # The sealed receipt retains the original pin even as metadata changes.
        self.packages["openvino"] = "different-installed-version"
        result = self.run_clip()
        self.assertEqual(result["error"]["code"], "dependency_version_mismatch", result)
        self.model_load.assert_not_called()

    def test_converted_files_cannot_be_mistaken_for_hf_weights(self):
        self.assertFalse(verify_model_assets({**self.spec, "artifact_path": str(self.export)}, "openvino")["ok"])
        source = verify_model_assets(self.spec)
        self.assertTrue(verify_openvino_assets(self.spec, self.export, source)["ok"])
        (self.export / "unlisted-decoder.xml").write_text("unregistered", encoding="utf-8")
        invalid = verify_openvino_assets(self.spec, self.export, source)
        self.assertFalse(invalid["ok"])
        self.assertIn("omits loadable file", " ".join(invalid["issues"]))

    def test_receipted_generation_or_vocabulary_changes_are_rejected(self):
        for filename, payload in (("generation_config.json", {"suppress_tokens": [999]}),
                                  ("config.json", {"model_type": "whisper", "vocab_size": 99})):
            with self.subTest(filename=filename):
                original = (self.export / filename).read_bytes()
                self.write(self.export / filename, payload)
                self.seal_export()
                result = self.run_clip()
                self.assertEqual(result["error"]["code"], "conversion_unverified", result)
                (self.export / filename).write_bytes(original)
                self.seal_export()
        self.model_load.assert_not_called()

    def test_no_pickle_fallback_or_export_when_conversion_receipt_missing(self):
        (self.export / "conversion-provenance.json").unlink()
        result = self.run_clip()
        self.assertEqual(result["error"]["code"], "conversion_unverified", result)
        self.model_load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
