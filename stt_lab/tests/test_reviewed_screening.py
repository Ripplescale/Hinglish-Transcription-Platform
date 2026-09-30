"""Synthetic safeguards for human-reviewed pilot scoring; no inference or network."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import wave


LAB_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "score_reviewed_screening", LAB_ROOT / "tools" / "score_reviewed_screening.py"
)
assert SPEC is not None and SPEC.loader is not None
scorer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scorer)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReviewedScreeningTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="stt-reviewed-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.references_path = self.root / "references.json"
        self.policy_path = self.root / "policy.json"
        self.output = self.root / "evaluation"
        texts = {
            "clip-1": "retain 99 boxes at boundary",
            "clip-2": "project alpha starts next monday",
            "clip-3": "deliver exactly 10 lakh meals",
        }
        clips = []
        for index, sample_id in enumerate(texts, 1):
            audio_path = self.root / f"{sample_id}.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(index.to_bytes(2, "little") * 16000)
            clips.append({
                "id": sample_id, "call_id": "synthetic-call", "audio": audio_path.name,
                "start_seconds": float(index - 1), "duration_seconds": 1.0,
                "audio_sha256": _digest(audio_path),
            })
        screening_path = self.root / "screening.json"
        _write_json(screening_path, {"version": 1, "clips": clips})
        self.screening_hash = _digest(screening_path)
        self.references = {
            "version": 1, "received_date": "2026-09-22",
            "screening_sha256": self.screening_hash,
            "samples": [
                {"sample_id": sample_id, "excerpt": index, "review_completed": True,
                 "reference_roman": text, "preference": "none", "user_feedback": "Synthetic review only."}
                for index, (sample_id, text) in enumerate(texts.items(), 1)
            ],
        }
        _write_json(self.references_path, self.references)
        self.policy = {
            "version": 1, "references_sha256": _digest(self.references_path),
            "excluded_samples": {"clip-1": "Synthetic boundary alignment is uncertain."},
            "entity_annotations": {
                "clip-1": [{"kind": "number", "text": "99 boxes", "aliases": []}],
                "clip-2": [{"kind": "name", "text": "alpha", "aliases": []}],
                "clip-3": [{"kind": "number", "text": "10 lakh meals", "aliases": []}],
            },
        }
        _write_json(self.policy_path, self.policy)
        self.results = {}
        for model, revision in (("apex", "1" * 40), ("swift", "2" * 40)):
            repo = f"Oriserve/Whisper-Hindi2Hinglish-{model.title()}"
            model_spec = {"id": model, "model_id": model, "repo_id": repo,
                          "source_revision": revision, "license": "apache-2.0"}
            self.results[model] = {
                "version": 1, "model_id": model, "model_spec": model_spec,
                "runtime_config": {"backend": "transformers", "device": "cpu"},
                "screening_sha256": self.screening_hash,
                "clips": {
                    sample_id: {
                        "status": "ok", "text": text if sample_id != "clip-1" else "omit everything",
                        "provenance": {"model_id": model, "repo_id": repo, "source_revision": revision},
                    }
                    for sample_id, text in texts.items()
                },
            }
            self._save_result(model)

    def _save_result(self, model: str) -> None:
        _write_json(self.root / "results" / f"{model}.json", self.results[model])

    def _evaluate(self, output=None, models=None, roman_views=None):
        return scorer.evaluate(self.root, self.references_path, self.policy_path, output or self.output,
                               models=models, roman_views=roman_views)

    def _add_mixed_model(self, model):
        repo = {"srota": "moorlee/qwen3-asr-0.6b-hinglish", "trelis": "Trelis/whisper-hinglish-preview"}[model]
        revision = ("3" if model == "srota" else "4") * 40
        spec = {"id": model, "model_id": model, "repo_id": repo, "source_revision": revision,
                "license": "apache-2.0", "output_script": "mixed"}
        self.results[model] = {
            "version": 1, "model_id": model, "model_spec": spec,
            "runtime_config": {"backend": "qwen_asr" if model == "srota" else "transformers", "device": "cpu"},
            "screening_sha256": self.screening_hash,
            "clips": {sample["sample_id"]: {
                "status": "ok", "text": sample["reference_roman"].replace("lakh", "लाख"),
                "timing": {"load_seconds": 1.0, "inference_seconds": 2.0, "total_seconds": 3.0},
                "provenance": {"model_id": model, "repo_id": repo, "source_revision": revision},
            } for sample in self.references["samples"]},
        }
        self._save_result(model)

    def _roman_artifact(self, models=("srota",)):
        document = {"version": 1, "screening_sha256": self.screening_hash, "models": {}}
        for model in models:
            result = self.results[model]
            document["models"][model] = {
                **{key: result["model_spec"][key] for key in ("model_id", "repo_id", "source_revision")},
                "source_results_sha256": _digest(self.root / "results" / f"{model}.json"),
                "renderer": {"name": "synthetic-transliteration-fixture", "source_revision": "fixture-v1",
                             "configuration": {"preserve_latin": True}},
                "clips": {sample_id: {
                    "source_text_sha256": hashlib.sha256(row["text"].encode("utf-8")).hexdigest(),
                    "roman_text": row["text"].replace("लाख", "lakh"),
                } for sample_id, row in result["clips"].items()},
            }
        path = self.root / "derived" / "roman-views.json"
        _write_json(path, document)
        return path, document

    def test_exclusion_removes_all_headline_counts_but_preserves_checked_reference(self):
        reference_bytes = self.references_path.read_bytes()
        original_drafts = {model: (self.root / "results" / f"{model}.json").read_bytes()
                           for model in self.results}
        report = self._evaluate()
        self.assertEqual(report["human_reviewed_excerpts"], 3)
        self.assertEqual(report["comparable_excerpts"], 2)
        self.assertEqual(report["comparable_audio_seconds"], 2.0)
        self.assertFalse(report["release_qualified"])
        for model in ("apex", "swift"):
            headline = report["models"][model]["headline"]
            self.assertEqual(headline["reference_words"], 10)
            self.assertEqual(headline["errors"], 0)
            self.assertEqual(headline["wer"], 0.0)
            self.assertEqual(headline["entities"]["number"], {"matched": 1, "reference": 1})
            excluded = next(row for row in report["models"][model]["samples"] if row["sample_id"] == "clip-1")
            self.assertFalse(excluded["included_in_headline"])
            self.assertEqual(excluded["reference_status"], "human_reviewed")
            self.assertEqual(excluded["reference"], "retain 99 boxes at boundary")
            self.assertGreater(excluded["strict_word_errors"]["errors"], 0)
            self.assertEqual((self.root / "results" / f"{model}.json").read_bytes(), original_drafts[model])
        self.assertEqual(self.references_path.read_bytes(), reference_bytes)
        markdown = (self.output / "comparison.md").read_text(encoding="utf-8")
        self.assertIn("retain 99 boxes at boundary", markdown)
        self.assertIn("Synthetic boundary alignment is uncertain.", markdown)

    def test_swapped_model_files_are_rejected_before_report_creation(self):
        _write_json(self.root / "results" / "apex.json", self.results["swift"])
        _write_json(self.root / "results" / "swift.json", self.results["apex"])
        with self.assertRaises(ValueError):
            self._evaluate()
        self.assertFalse(self.output.exists())

    def test_included_boundary_scoped_reference_preserves_full_correction(self):
        sample = self.references["samples"][0]
        full_correction = sample["reference_roman"]
        sample.update({
            "reference_roman": "retain 99 boxes",
            "full_user_reference_roman": full_correction,
            "reference_scope_note": "Assume the final words were outside the sampled clip.",
            "audio_reference_alignment": "user_assumed_ending_outside_clip",
        })
        _write_json(self.references_path, self.references)
        reference_bytes = self.references_path.read_bytes()
        self.policy.update(references_sha256=_digest(self.references_path), excluded_samples={})
        _write_json(self.policy_path, self.policy)
        for model in ("apex", "swift"):
            self.results[model]["clips"]["clip-1"]["text"] = sample["reference_roman"]
            self._save_result(model)
        report = self._evaluate()
        saved = json.loads((self.output / "scores.json").read_text(encoding="utf-8"))
        self.assertEqual(report["comparable_excerpts"], 3)
        self.assertEqual(report["comparable_audio_seconds"], 3.0)
        self.assertEqual(report["excluded_samples"], {})
        self.assertNotIn("excluded", report["interpretation"].lower())
        for model in ("apex", "swift"):
            self.assertEqual(report["models"][model]["headline"]["reference_words"], 13)
            self.assertEqual(report["models"][model]["headline"]["wer"], 0.0)
            row = report["models"][model]["samples"][0]
            self.assertTrue(row["included_in_headline"])
            self.assertEqual(row["reference"], "retain 99 boxes")
            self.assertEqual(row["full_user_reference_roman"], full_correction)
            self.assertEqual(row["reference_scope_note"], sample["reference_scope_note"])
            self.assertEqual(row["audio_reference_alignment"], "user_assumed_ending_outside_clip")
            self.assertEqual(saved["models"][model]["samples"][0], row)
        self.assertEqual(self.references_path.read_bytes(), reference_bytes)
        markdown = (self.output / "comparison.md").read_text(encoding="utf-8")
        self.assertIn(full_correction, markdown)
        self.assertIn(sample["reference_scope_note"], markdown)
        self.assertIn("Full user correction (preserved separately", markdown)
        self.assertIn("All reviewed excerpts are included.", markdown)
        self.assertNotIn("Excluded excerpts", markdown)
        self.assertNotIn("clips excluded", markdown)
        self.assertNotIn("Excluded from headline", markdown)

    def test_missing_revision_on_both_spec_and_prediction_is_rejected(self):
        self.results["apex"]["model_spec"].pop("source_revision")
        for prediction in self.results["apex"]["clips"].values():
            prediction["provenance"].pop("source_revision")
        self._save_result("apex")
        with self.assertRaises(ValueError):
            self._evaluate()
        self.assertFalse(self.output.exists())

    def test_prediction_identity_must_match_model_run(self):
        baseline = copy.deepcopy(self.results["apex"])
        for field, wrong in (("model_id", "swift"),
                             ("repo_id", "Oriserve/Whisper-Hindi2Hinglish-Swift"),
                             ("source_revision", "3" * 40)):
            with self.subTest(field=field):
                self.results["apex"] = copy.deepcopy(baseline)
                self.results["apex"]["clips"]["clip-2"]["provenance"][field] = wrong
                self._save_result("apex")
                with self.assertRaises(ValueError):
                    self._evaluate()
                self.assertFalse(self.output.exists())

    def test_shortened_quantity_alias_cannot_make_missing_units_a_match(self):
        self.policy["entity_annotations"]["clip-3"][0]["aliases"] = ["10"]
        _write_json(self.policy_path, self.policy)
        self.results["apex"]["clips"]["clip-3"]["text"] = "deliver exactly 10"
        self._save_result("apex")
        with self.assertRaises(ValueError):
            self._evaluate()
        self.assertFalse(self.output.exists())

    def test_duplicate_normalized_entity_does_not_inflate_denominator(self):
        self.policy["entity_annotations"]["clip-2"].append({"kind": "name", "text": "ALPHA", "aliases": []})
        _write_json(self.policy_path, self.policy)
        with self.assertRaises(ValueError):
            self._evaluate()
        self.assertFalse(self.output.exists())

    def test_swift_leading_fixture_changes_report_verdict(self):
        self.results["apex"]["clips"]["clip-2"]["text"] = "project beta ends next friday"
        self.results["apex"]["clips"]["clip-3"]["text"] = "deliver only 20 small boxes"
        self._save_result("apex")
        report = self._evaluate()
        self.assertGreater(report["models"]["apex"]["headline"]["wer"],
                           report["models"]["swift"]["headline"]["wer"])
        self.assertEqual(report["models"]["swift"]["headline"]["wer"], 0.0)
        markdown = (self.output / "comparison.md").read_text(encoding="utf-8")
        self.assertRegex(markdown, r"(?i)\bswift\s+leads\b")
        self.assertNotRegex(markdown, r"(?i)\bapex\s+leads\b")

    def test_tie_at_twenty_percent_is_reported_as_meeting_target(self):
        for model in ("apex", "swift"):
            self.results[model]["clips"]["clip-2"]["text"] = "project alpha starts next tuesday"
            self.results[model]["clips"]["clip-3"]["text"] = "deliver exactly 20 lakh meals"
            self._save_result(model)
        report = self._evaluate()
        for model in ("apex", "swift"):
            self.assertEqual(report["models"][model]["headline"]["wer"], 0.2)
        markdown = (self.output / "comparison.md").read_text(encoding="utf-8")
        self.assertRegex(markdown, r"(?i)\b(tie|tied)\b")
        self.assertNotRegex(markdown, r"(?i)\b(apex|swift)\s+leads\b")
        self.assertNotIn("target is unmet", markdown)
        self.assertRegex(markdown, r"(?i)(\b(meet|meets|met|pass|passes)\b[^\n]{0,100}20%|20%[^\n]{0,100}\b(meet|meets|met|pass|passes)\b)")

    def test_mixed_script_model_requires_explicit_derived_roman_view(self):
        self._add_mixed_model("srota")
        with self.assertRaisesRegex(ValueError, "require"):
            self._evaluate(models=["srota"])
        self.assertFalse(self.output.exists())

    def test_four_models_keep_raw_text_and_score_only_bound_roman_views(self):
        for model in ("srota", "trelis"):
            self._add_mixed_model(model)
        for model in ("apex", "swift"):
            self.results[model]["clips"]["clip-2"]["text"] = "incorrect synthetic draft"
            self._save_result(model)
        self.results["trelis"]["clips"]["clip-3"]["text"] = "deliver exactly 20 लाख meals"
        self._save_result("trelis")
        views_path, _ = self._roman_artifact(("srota", "trelis"))
        original_hashes = {model: _digest(self.root / "results" / f"{model}.json") for model in self.results}
        views_hash = _digest(views_path)
        report = self._evaluate(models=["apex", "swift", "srota", "trelis"], roman_views=views_path)
        self.assertEqual(list(report["models"]), ["apex", "swift", "srota", "trelis"])
        self.assertEqual(report["roman_views_sha256"], views_hash)
        self.assertEqual(report["models"]["srota"]["headline"]["wer"], 0.0)
        self.assertEqual(report["models"]["trelis"]["headline"]["wer"], 0.1)
        self.assertEqual(report["models"]["srota"]["headline"]["entities"]["number"]["matched"], 1)
        self.assertEqual(report["models"]["trelis"]["headline"]["entities"]["number"]["matched"], 0)
        row = next(row for row in report["models"]["srota"]["samples"] if row["sample_id"] == "clip-3")
        self.assertIn("लाख", row["raw_hypothesis"])
        self.assertEqual(row["hypothesis"], "deliver exactly 10 lakh meals")
        self.assertEqual(row["scoring_view"], "derived_roman")
        self.assertIsNotNone(row["roman_derivation"])
        self.assertEqual(report["models"]["apex"]["scoring_view"], "native_roman")
        for model, expected in original_hashes.items():
            self.assertEqual(_digest(self.root / "results" / f"{model}.json"), expected)
        self.assertEqual(_digest(views_path), views_hash)
        markdown = (self.output / "comparison.md").read_text(encoding="utf-8")
        self.assertIn("Srota leads this small pilot.", markdown)
        self.assertIn("Srota original draft", markdown)
        self.assertIn("Srota derived Roman reading view (scored)", markdown)
        self.assertIn("deliver exactly 10 लाख meals", markdown)
        self.assertIn("retain 99 boxes at boundary", markdown)
        self.assertFalse(report["release_qualified"])

    def test_derived_views_reject_stale_screen_result_or_text_binding(self):
        self._add_mixed_model("srota")
        views_path, baseline = self._roman_artifact()
        changes = (
            ("screening", lambda data: data.update(screening_sha256="0" * 64)),
            ("results", lambda data: data["models"]["srota"].update(source_results_sha256="0" * 64)),
            ("raw_text", lambda data: data["models"]["srota"]["clips"]["clip-3"].update(source_text_sha256="0" * 64)),
            ("model", lambda data: data["models"]["srota"].update(model_id="trelis")),
            ("renderer", lambda data: data["models"]["srota"]["renderer"].update(source_revision="")),
            ("missing_clip", lambda data: data["models"]["srota"]["clips"].pop("clip-2")),
        )
        for case, change in changes:
            with self.subTest(case=case):
                modified = copy.deepcopy(baseline)
                change(modified)
                _write_json(views_path, modified)
                with self.assertRaises(ValueError):
                    self._evaluate(models=["srota"], roman_views=views_path)
                self.assertFalse(self.output.exists())

    def test_mixed_script_cannot_be_relabelled_as_a_roman_view(self):
        self._add_mixed_model("srota")
        views_path, views = self._roman_artifact()
        views["models"]["srota"]["clips"]["clip-3"]["roman_text"] = "deliver exactly 10 लाख meals"
        _write_json(views_path, views)
        with self.assertRaisesRegex(ValueError, "Devanagari"):
            self._evaluate(models=["srota"], roman_views=views_path)
        self.assertFalse(self.output.exists())

    def test_batch_times_retain_excluded_work_without_claiming_live_latency(self):
        self.results["apex"]["clips"]["clip-1"]["timing"] = {
            "load_seconds": 90.0, "inference_seconds": 10.0, "total_seconds": 100.0,
        }
        for sample_id in ("clip-2", "clip-3"):
            self.results["apex"]["clips"][sample_id]["timing"] = {
                "load_seconds": 1.0, "inference_seconds": 2.0, "total_seconds": 3.0,
            }
        self._save_result("apex")
        report = self._evaluate()
        timing = report["models"]["apex"]["batch_timing"]
        self.assertEqual(timing["all_reviewed_excerpts"]["totals"]["total_seconds"], 106.0)
        self.assertEqual(timing["headline_excerpts"]["totals"]["total_seconds"], 6.0)
        self.assertEqual(timing["headline_excerpts"]["processing_audio_ratio"], 3.0)
        self.assertFalse(timing["all_reviewed_excerpts"]["live_latency_verified"])
        missing = report["models"]["swift"]["batch_timing"]["all_reviewed_excerpts"]
        self.assertIsNone(missing["totals"]["total_seconds"])
        self.assertIsNone(missing["processing_audio_ratio"])
        self.assertFalse(missing["total_timing_complete"])
        self.assertEqual(report["models"]["apex"]["headline"]["reference_words"], 10)

    def test_partial_batch_timings_do_not_imply_full_run_speed(self):
        self.results["apex"]["clips"]["clip-2"]["timing"] = {"total_seconds": 3.0}
        self._save_result("apex")
        report = self._evaluate(models=["apex"])
        timing = report["models"]["apex"]["batch_timing"]["all_reviewed_excerpts"]
        self.assertEqual(timing["totals"]["total_seconds"], 3.0)
        self.assertEqual(timing["coverage"]["total_seconds"], 1)
        self.assertIsNone(timing["processing_audio_ratio"])
        markdown = (self.output / "comparison.md").read_text(encoding="utf-8")
        self.assertIn("3.00s (1/3 clips)", markdown)
        self.assertIn("no comparative winner is implied", markdown)


if __name__ == "__main__":
    unittest.main()
