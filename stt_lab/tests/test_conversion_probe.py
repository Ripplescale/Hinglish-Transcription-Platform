"""Synthetic conversion checks; no model packages, inference, or personal audio."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import wave


LAB = Path(__file__).resolve().parents[1]


def _module(name, filename):
    spec = importlib.util.spec_from_file_location(name, LAB / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _module("conversion_probe_under_test", "compare_converted.py")
scorer = _module("conversion_fixture_scorer", "score_reviewed_screening.py")


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ConversionProbeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="stt-conversion-probe-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.screening_path = self.root / "screening.json"
        self.references_path = self.root / "review" / "references.json"
        self.policy_path = self.root / "review" / "policy.json"
        self.source_path = self.root / "results" / "apex.json"
        self.config_path = self.root / "converted-config.json"
        self.output = self.root / "converted-run"
        references = ["alpha delivers 10 ml", "ready for next call", "outside reviewed interval"]
        drafts = ["beta delivers 10 ml", references[1], "synthetic excluded draft"]
        clips = []
        for index in range(3):
            audio_path = self.root / f"clip-{index + 1}.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes((index + 1).to_bytes(2, "little") * 16000)
            clips.append({"id": f"clip-{index + 1}", "audio": audio_path.name,
                          "duration_seconds": 1.0, "audio_sha256": _hash(audio_path)})
        _write(self.screening_path, {"version": 1, "clips": clips})
        self.references = {"version": 2, "received_date": "2026-09-22",
            "source": "Synthetic explicit user corrections", "reference_author": "user",
            "screening_sha256": _hash(self.screening_path),
            "samples": [{"sample_id": f"clip-{i + 1}", "excerpt": i + 1,
                "review_completed": True, "reference_roman": text,
                "raw_transcript": text, "audio_reference_alignment": "user_reviewed",
                "names": ["alpha"] if i == 0 else [], "numbers": ["10 ml"] if i == 0 else [],
                "preference": "none", "user_feedback": "Synthetic fixture only."}
                for i, text in enumerate(references)]}
        self.references["samples"][0].update(
            raw_transcript="alpha delivers 10 ml then the next statement",
            full_user_reference_roman="alpha delivers 10 ml then the next statement",
            reference_scope_note="Synthetic user assumes the ending belongs after this clip.",
            audio_reference_alignment="user_assumed_ending_outside_clip")
        _write(self.references_path, self.references)
        self.policy = {"version": 2, "references_sha256": _hash(self.references_path),
            "boundary_clarification": {"sample_id": "clip-1", "decision": "Score the scoped synthetic reference."},
            "excluded_samples": {"clip-3": "Outside the scored synthetic interval."},
            "entity_annotations": {"clip-1": [{"kind": "name", "text": "alpha"},
                                               {"kind": "number", "text": "10 ml"}]}}
        _write(self.policy_path, self.policy)
        self.model_spec = {"id": "apex", "model_id": "apex",
            "repo_id": "Oriserve/Whisper-Hindi2Hinglish-Apex", "source_revision": "a" * 40,
            "license": "apache-2.0", "decoding": {"language": "en", "task": "transcribe"}}
        source = {"model_id": "apex", "model_spec": self.model_spec,
            "runtime_config": {"backend": "transformers", "device": "cpu"},
            "screening_sha256": _hash(self.screening_path),
            "clips": {f"clip-{i + 1}": {"status": "ok", "text": draft,
                "provenance": {key: self.model_spec[key] for key in ("model_id", "repo_id", "source_revision")}}
                for i, draft in enumerate(drafts)}}
        _write(self.source_path, source)
        baseline_dir = self.root / "baseline"
        self.baseline = scorer.evaluate(self.root, self.references_path, self.policy_path,
                                        baseline_dir, models=["apex"])
        self.baseline_path = baseline_dir / "scores.json"
        _write(self.config_path, {"backend": "whisper_cpp", "artifact_path": str(self.root / "fake-model.bin"),
                                  "device": "cpu", "timestamps": True})
        self.calls = []

    def _result(self, text="beta delivers 10 ml", status="ok", segments=None):
        return {"status": status, "text": text if status == "ok" else "",
                "segments": [{"start": 0.0, "end": 1.0, "text": text}] if segments is None else segments,
                "timing": {"total_seconds": 0.25}, "provenance": {"mode": "batch_cli"}}

    def _run(self, responses=None):
        responses = responses or [self._result(), self._result("ready for next call")]
        def fake_transcribe(spec, audio, config):
            self.calls.append((copy.deepcopy(spec), audio, copy.deepcopy(config)))
            value = responses[len(self.calls) - 1]
            if isinstance(value, BaseException):
                raise value
            return copy.deepcopy(value)
        with patch.object(probe, "transcribe", side_effect=fake_transcribe), contextlib.redirect_stdout(io.StringIO()):
            return probe.run(self.screening_path, self.baseline_path, "apex", self.config_path, self.output)

    def test_frozen_input_bytes_and_audio_are_checked_before_inference(self):
        for path in (self.screening_path, self.references_path, self.policy_path, self.source_path,
                     self.root / "clip-1.wav"):
            original = path.read_bytes()
            with self.subTest(path=path.name):
                path.write_bytes(original + b" ")
                try:
                    with self.assertRaises(ValueError):
                        self._run()
                    self.assertEqual(self.calls, [])
                    self.assertFalse(self.output.exists())
                finally:
                    path.write_bytes(original)

    def test_excluded_sample_is_never_inferred_and_sources_are_unchanged(self):
        inputs = [self.references_path, self.policy_path, self.source_path, self.baseline_path]
        hashes = {path: _hash(path) for path in inputs}
        report = self._run()
        self.assertEqual([call[1].stem for call in self.calls], ["clip-1", "clip-2"])
        self.assertEqual(report["summary"]["expected_samples"], 2)
        self.assertEqual(report["summary"]["reference_words"], 8)
        for spec, _, config in self.calls:
            self.assertNotIn("reference", spec)
            self.assertNotIn("initial_prompt", config)
            self.assertNotIn("reference", config)
        self.assertEqual({path: _hash(path) for path in inputs}, hashes)

    def test_baseline_agreement_is_not_human_reference_accuracy(self):
        report = self._run([self._result("BETA delivers 10 ml!"), self._result("Ready for next call.")])
        self.assertEqual(report["summary"]["identical_normalized_samples"], 2)
        self.assertEqual(report["summary"]["reference_errors"], 1)
        self.assertEqual(report["summary"]["reference_wer"], 1 / 8)
        self.assertEqual(report["samples"][0]["baseline_text_changes"]["errors"], 0)
        self.assertEqual(report["summary"]["entities"]["name"], {"matched": 0, "reference": 1})

    def test_improved_reference_accuracy_can_disagree_with_original_runtime(self):
        report = self._run([self._result("alpha delivers 10 ml"), self._result("ready for next call")])
        self.assertEqual(report["summary"]["reference_wer"], 0.0)
        self.assertEqual(report["summary"]["identical_normalized_samples"], 1)
        self.assertEqual(report["samples"][0]["baseline_text_changes"]["errors"], 1)

    def test_failed_last_sample_never_publishes_complete_reference_wer(self):
        report = self._run([self._result(), self._result(status="failed", segments=[])])
        self.assertEqual(report["state"], "failed")
        self.assertFalse(report["summary"]["complete"])
        self.assertIsNone(report["summary"]["reference_wer"])
        self.assertEqual(report["summary"]["successful_samples"], 1)
        self.assertNotIn("reference_word_errors", report["samples"][1])
        self.assertFalse(report["summary"]["all_timestamp_sanity_checks_pass"])

    def test_interrupted_partial_run_keeps_results_without_complete_wer(self):
        connection = socket.socket.connect
        with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
            self._run([self._result(), RuntimeError("synthetic interruption")])
        report = json.loads((self.output / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(report["state"], "interrupted")
        self.assertFalse(report["summary"]["complete"])
        self.assertIsNone(report["summary"]["reference_wer"])
        self.assertEqual(len(report["samples"]), 1)
        self.assertIs(socket.socket.connect, connection)

    def test_valid_timestamps_are_only_sanity_not_alignment_accuracy(self):
        report = self._run()
        self.assertTrue(report["summary"]["all_timestamp_sanity_checks_pass"])
        self.assertFalse(report["summary"]["timestamp_alignment_accuracy_verified"])
        self.assertFalse(report["summary"]["qualified_for_release"])
        self.assertFalse(report["live_latency_verified"])
        for row in report["samples"]:
            self.assertFalse(row["timestamp_sanity"]["alignment_accuracy_verified"])

    def test_missing_unordered_or_outside_timestamps_fail_sanity(self):
        cases = [([], "nonempty"),
                 ([{"start": 0.5, "end": 0.8}, {"start": 0.1, "end": 0.4}], "ordered"),
                 ([{"start": 0.0, "end": 2.0}], "inside_audio")]
        for index, (segments, failed_key) in enumerate(cases):
            with self.subTest(failed_key=failed_key):
                self.output = self.root / f"timestamp-run-{index}"
                self.calls = []
                report = self._run([self._result(segments=segments), self._result("ready for next call")])
                self.assertFalse(report["samples"][0]["timestamp_sanity"][failed_key])
                self.assertFalse(report["summary"]["all_timestamp_sanity_checks_pass"])
                self.assertFalse(report["summary"]["timestamp_alignment_accuracy_verified"])

    def test_baseline_rows_cannot_replace_bound_reference_policy_or_original_text(self):
        changes = [
            ("reference", lambda rows: rows[0].update(reference="beta delivers 10 ml")),
            ("entities", lambda rows: rows[0].update(entity_details=[])),
            ("original_text", lambda rows: rows[0].update(hypothesis="alpha delivers 10 ml")),
            ("exclusion", lambda rows: rows[0].update(included_in_headline=False)),
            ("audio", lambda rows: rows[0].update(audio_sha256="0" * 64)),
        ]
        for name, change in changes:
            with self.subTest(name=name):
                modified = copy.deepcopy(self.baseline)
                change(modified["models"]["apex"]["samples"])
                _write(self.baseline_path, modified)
                self.output = self.root / f"invalid-{name}"
                self.calls = []
                with self.assertRaises(ValueError):
                    self._run()
                self.assertEqual(self.calls, [])
                self.assertFalse(self.output.exists())

    def test_empty_baseline_selection_is_rejected(self):
        modified = copy.deepcopy(self.baseline)
        modified["models"]["apex"]["samples"] = []
        _write(self.baseline_path, modified)
        with self.assertRaises(ValueError):
            self._run()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.output.exists())

    def test_existing_output_cannot_resume_or_overwrite(self):
        self.output.mkdir()
        sentinel = self.output / "retain.txt"
        sentinel.write_text("previous result", encoding="utf-8")
        with self.assertRaises(ValueError):
            self._run()
        self.assertEqual(self.calls, [])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "previous result")


if __name__ == "__main__":
    unittest.main()
