"""Synthetic local fixtures exercise metrics, leakage checks, resume and gates."""
import copy
import json
import tempfile
import unittest
import wave
from pathlib import Path

from sttbench.cli import main
from sttbench.execution import run, select_model
from sttbench.manifest import file_digest, load_manifest, read_jsonl, write_json
from sttbench.normalization import Normalizer, basic_tokens, word_errors
from sttbench.reporting import release_report
from sttbench.scoring import corpus_digest, score


class NormalizationTests(unittest.TestCase):
    def test_devanagari_marks_and_numbers_survive(self):
        self.assertEqual(basic_tokens("किताब की कीमत ₹1,000.50 है!"), ["किताब", "की", "कीमत", "₹1,000.50", "है"])
        self.assertNotEqual(basic_tokens("की"), basic_tokens("क"))
        self.assertNotEqual(basic_tokens("1.5"), basic_tokens("15"))
        self.assertNotEqual(basic_tokens("-20"), basic_tokens("20"))

    def test_equivalence_is_symmetric_and_does_not_change_numbers(self):
        normalizer = Normalizer({"okay": "ok", "theek hai": "thik hai"})
        self.assertEqual(normalizer.tokens("Okay, theek hai"), normalizer.tokens("OK thik hai"))
        for mapping in ({"20": "30"}, {"twenty": "20"}, {"a": "b", "b": "c"}, {"a": ""}):
            with self.assertRaises(ValueError):
                Normalizer(mapping)

    def test_wer_deletion_insertion_and_silence(self):
        result = word_errors(["a", "b", "c"], ["a", "x", "c", "d"])
        self.assertEqual((result["substitutions"], result["insertions"], result["errors"]), (1, 1, 2))
        self.assertEqual(word_errors(["a"], [])["wer"], 1)
        silence = word_errors([], ["invented"])
        self.assertIsNone(silence["wer"])
        self.assertEqual(silence["silence_hallucination_words"], 1)


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        for number in (1, 2):
            with wave.open(str(self.directory / f"{number}.wav"), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(bytes([number, 0]) * 16000)
        self.document = {"version": 1, "required_views": ["original", "roman"], "samples": [
            {"id": "dev", "audio": "1.wav", "split": "dev", "call_id": "call-a", "speaker_ids": ["a"],
             "reference": {"original": "ठीक है", "roman": "thik hai"}, "reference_status": "reviewed", "license": "synthetic"},
            {"id": "test", "audio": "2.wav", "split": "test", "call_id": "call-b", "speaker_ids": ["b"],
             "reference": {"original": "राहुल 20 भेजो", "roman": "Rahul 20 bhejo"}, "reference_status": "reviewed",
             "entities": [{"kind": "name", "text": "राहुल", "aliases": ["Rahul"]}, {"kind": "number", "text": "20"}],
             "slices": {"language": "hinglish"}, "license": "synthetic"}]}
        self.path = self.directory / "manifest.json"
        write_json(self.path, self.document)
        self.manifest = load_manifest(self.path)
        self.prediction = {"sample_id": "test", "status": "ok", "text": "राहुल 20 भेजो", "roman_text": "Rahul 20 bhejo",
            "manifest_sha256": self.manifest["manifest_sha256"], "config_sha256": "configuration",
            "audio_sha256": self.manifest["samples"][1]["_audio_sha256"], "processing_seconds": .5, "duration_seconds": 1}

    def qualified_scores_fixture(self):
        """Synthetic report contract only; does not measure or qualify a model."""
        prediction = copy.deepcopy(self.prediction)
        prediction["provenance"] = {"source_revision": "synthetic-test-revision", "asset_verification": {
            "ok": True, "integrity": "manifest_files_verified", "manifest_sha256": "synthetic-manifest",
            "verified_hashes": {"synthetic.bin": "synthetic-hash"}, "source_revision_declaration_matches": True,
            "source_revision": "synthetic-test-revision"}}
        result = score(self.manifest, [prediction])
        result["corpus_coverage"].update(unique_audio_seconds=1800,
            unique_audio_seconds_by_split={"dev": 900, "test": 900})
        return result

    def test_prevents_call_speaker_and_audio_leakage(self):
        for field, value in (("call_id", "call-a"), ("speaker_ids", ["a"]), ("audio", "1.wav")):
            changed = copy.deepcopy(self.document)
            changed["require_disjoint_speakers"] = True
            changed["samples"][1][field] = value
            write_json(self.path, changed)
            with self.assertRaisesRegex(ValueError, "leakage"):
                load_manifest(self.path)

    def test_shared_speaker_warns_unless_disjoint_required(self):
        changed = copy.deepcopy(self.document)
        changed["samples"][1]["speaker_ids"] = ["a"]
        write_json(self.path, changed)
        manifest = load_manifest(self.path)
        self.assertTrue(any("unseen-speaker" in warning for warning in manifest["warnings"]))

    def test_declared_audio_hash_must_match_bytes(self):
        self.document["samples"][1]["audio_sha256"] = file_digest(self.directory / "2.wav").upper()
        write_json(self.path, self.document)
        load_manifest(self.path)
        self.document["samples"][1]["audio_sha256"] = "0" * 64
        write_json(self.path, self.document)
        with self.assertRaisesRegex(ValueError, "actual WAV bytes"):
            load_manifest(self.path)

    def test_repeated_audio_does_not_inflate_corpus_coverage(self):
        duplicate = dict(self.document["samples"][1], id="duplicate-test")
        self.document["samples"].append(duplicate)
        write_json(self.path, self.document)
        manifest = load_manifest(self.path)
        prediction = dict(self.prediction, manifest_sha256=manifest["manifest_sha256"])
        result = score(manifest, [prediction])
        self.assertEqual(result["corpus_coverage"]["unique_audio_seconds"], 2)
        self.assertEqual(result["corpus_coverage"]["unique_audio_seconds_by_split"]["test"], 1)

    def test_missing_reference_is_pending_not_perfect(self):
        changed = copy.deepcopy(self.document)
        del changed["samples"][1]["reference"]
        changed["samples"][1]["reference_status"] = "pending"
        write_json(self.path, changed)
        manifest = load_manifest(self.path)
        prediction = dict(self.prediction, manifest_sha256=manifest["manifest_sha256"])
        result = score(manifest, [prediction])
        self.assertIsNone(result["total"]["views"]["original"]["wer"])
        self.assertEqual(release_report(result)["decision"], "pending")

    def test_scores_views_slices_entities_and_truthful_batch_timing(self):
        result = score(self.manifest, [self.prediction])
        self.assertEqual(result["total"]["views"]["roman"]["wer"], 0)
        self.assertEqual(result["total"]["entity_recall"], 1)
        self.assertIn("language=hinglish", result["slices"])
        self.assertEqual(result["total"]["batch_timing"]["processing_audio_ratio"], .5)
        self.assertIsNone(result["total"]["batch_timing"]["live_latency_p95_seconds"])

    def test_missing_roman_never_falls_back_to_original(self):
        prediction = dict(self.prediction)
        del prediction["roman_text"]
        result = score(self.manifest, [prediction])
        self.assertEqual(result["samples"][0]["views"]["roman"]["status"], "missing_hypothesis")
        self.assertEqual(release_report(result)["criteria"]["roman_wer"]["status"], "pending")

    def test_native_roman_not_scored_against_mixed_reference(self):
        prediction = dict(self.prediction, text="Rahul 20 bhejo", text_view="roman")
        result = score(self.manifest, [prediction])
        self.assertEqual(result["samples"][0]["views"]["original"]["status"], "missing_hypothesis")
        self.assertEqual(result["total"]["entity_recall"], 1)
        self.document["required_views"] = ["roman"]
        write_json(self.path, self.document)
        manifest = load_manifest(self.path)
        prediction["manifest_sha256"] = manifest["manifest_sha256"]
        roman_only = release_report(score(manifest, [prediction]))
        self.assertNotIn("original_wer", roman_only["criteria"])
        self.assertEqual(roman_only["criteria"]["entity_preservation"]["status"], "pass")

    def test_bad_number_counts_as_entity_error(self):
        prediction = dict(self.prediction, roman_text="Rahul 30 bhejo")
        scores = score(self.manifest, [prediction])
        self.assertEqual(scores["total"]["entity_recall"], .5)
        self.assertEqual(scores["total"]["entities_by_view"]["original"]["recall"], 1)
        self.assertEqual(release_report(scores)["criteria"]["entity_preservation"]["status"], "fail")

    def test_duplicate_unknown_wrong_manifest_and_nan_rejected(self):
        invalid = [[self.prediction, self.prediction], [dict(self.prediction, sample_id="unknown")],
                   [dict(self.prediction, manifest_sha256="other")], [dict(self.prediction, processing_seconds=float("nan"))]]
        for predictions in invalid:
            with self.assertRaises(ValueError):
                score(self.manifest, predictions)

    def test_resume_and_config_changes(self):
        calls = []
        def fake(spec, audio, config):
            calls.append(audio)
            return {"status": "ok", "text": "hello", "segments": []}
        spec = {"id": "fake", "source_revision": "abc123", "license": "mit", "output_script": "roman"}
        output = self.directory / "run"
        config = {"artifact_path": str(self.directory), "backend": "fake"}
        run(self.manifest, spec, config, output, "dev", transcriber=fake)
        run(self.manifest, spec, config, output, "dev", transcriber=fake)
        self.assertEqual(len(calls), 1)
        saved = json.loads((output / "hypotheses.jsonl").read_text())
        self.assertEqual(saved["roman_text"], "hello")
        with self.assertRaises(ValueError):
            run(self.manifest, spec, dict(config, backend="other"), output, "dev", transcriber=fake)

    def test_runtime_failure_is_durable_and_retryable(self):
        def failed(spec, audio, config):
            raise RuntimeError("synthetic failure")
        spec = {"id": "fake", "source_revision": "abc123", "license": "mit"}
        output = self.directory / "failed"
        config = {"artifact_path": str(self.directory)}
        result = run(self.manifest, spec, config, output, transcriber=failed)
        self.assertEqual(result["successful"], 0)
        result = run(self.manifest, spec, config, output, transcriber=lambda *args: {"status": "ok", "text": "fixed"})
        self.assertEqual(result["successful"], 1)

    def test_reviewed_references_rebind_only_with_unchanged_inputs(self):
        spec = {"id": "fake", "source_revision": "abc123", "license": "mit", "output_script": "roman"}
        output = self.directory / "draft"
        run(self.manifest, spec, {"artifact_path": str(self.directory)}, output, "test",
            transcriber=lambda *args: {"status": "ok", "text": "Rahul 20 bhejo"})
        hypotheses = read_jsonl(output / "hypotheses.jsonl")
        corrected = copy.deepcopy(self.document)
        corrected["samples"][1]["reference"]["roman"] = "Rahul 20 bhejo please"
        write_json(self.path, corrected)
        manifest = load_manifest(self.path)
        with self.assertRaises(ValueError):
            score(manifest, hypotheses)
        result = score(manifest, hypotheses, rebind_references=True)
        self.assertTrue(result["provenance_verified"])
        self.assertEqual(result["reference_rebinding"]["samples"], ["test"])
        self.assertEqual(result["total"]["views"]["roman"]["wer"], .25)
        corrected["samples"][1]["call_id"] = "different-call"
        write_json(self.path, corrected)
        with self.assertRaises(ValueError):
            score(load_manifest(self.path), hypotheses, rebind_references=True)

    def evidence(self, scores):
        return {"version": 1, "phase": "live", "clock": "monotonic",
            **{key: scores[key] for key in ("manifest_sha256", "corpus_sha256", "config_sha256")},
            "capture_dropouts": 0,
            "backlog_observations": [{"elapsed_seconds": t, "queued_audio_seconds": 1} for t in (0, 1800, 3600)],
            "samples": [{"sample_id": "test", "latency_events": [{"speech_end_ms": 1000, "roman_displayed_ms": 2000, "speaker_displayed_ms": 3000}],
                "speaker_agreement": {"scope": "remote", "method": "optimal_mapping_reference_speaker_time",
                                      "correct_speaker_seconds": 95, "reference_speaker_seconds": 100}}]}

    def test_gate_requires_all_evidence_and_exact_identity(self):
        scores = self.qualified_scores_fixture()
        self.assertEqual(release_report(scores)["decision"], "pending")
        evidence = self.evidence(scores)
        self.assertEqual(release_report(scores, evidence)["decision"], "pass")
        evidence["config_sha256"] = "wrong"
        self.assertEqual(release_report(scores, evidence)["decision"], "pending")

    def test_invalid_live_evidence_cannot_pass(self):
        scores = self.qualified_scores_fixture()
        for bad in (-1, float("nan"), None):
            evidence = self.evidence(scores)
            evidence["samples"][0]["latency_events"][0]["speaker_displayed_ms"] = bad
            self.assertEqual(release_report(scores, evidence)["decision"], "pending")
        evidence = self.evidence(scores)
        evidence["samples"][0]["latency_events"][0]["speaker_displayed_ms"] = 17000
        self.assertEqual(release_report(scores, evidence)["decision"], "fail")

    def test_sustained_live_59_minutes_fails_60_passes(self):
        scores = self.qualified_scores_fixture()
        evidence = self.evidence(scores)
        evidence["backlog_observations"][-1]["elapsed_seconds"] = 59 * 60
        result = release_report(scores, evidence)
        self.assertEqual(result["criteria"]["stable_backlog"]["status"], "fail")
        self.assertIn("3600s", result["criteria"]["stable_backlog"]["reason"])
        evidence["backlog_observations"][-1]["elapsed_seconds"] = 60 * 60
        self.assertEqual(release_report(scores, evidence)["decision"], "pass")

    def test_smoke_and_unverified_weights_cannot_qualify(self):
        scores = score(self.manifest, [self.prediction])
        report = release_report(scores, self.evidence(scores))
        self.assertEqual(report["criteria"]["representative_corpus"]["status"], "pending")
        self.assertEqual(report["criteria"]["verified_model_assets"]["status"], "pending")
        self.assertEqual(report["decision"], "pending")
        scores = self.qualified_scores_fixture()
        scores["model_assets_verified"] = False
        self.assertEqual(release_report(scores, self.evidence(scores))["decision"], "pending")
        evidence = self.evidence(scores)
        evidence["backlog_observations"][-1]["queued_audio_seconds"] = 10
        self.assertEqual(release_report(scores, evidence)["decision"], "fail")

    def test_cli_can_validate_and_mutable_revision_rejected(self):
        self.assertEqual(main(["validate", "--manifest", str(self.path)]), 0)
        with self.assertRaises(ValueError):
            select_model({"models": [{"id": "x", "source_revision": "main", "license": "mit"}]}, "x")


if __name__ == "__main__":
    unittest.main()
