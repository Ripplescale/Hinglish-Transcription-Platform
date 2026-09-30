"""Protect script compatibility, scoring scope and result bindings in the sweep."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from sttbench.manifest import file_digest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("chunk_size_scoring_subject", TOOLS / "chunk_size_sweep.py")
sweep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sweep)


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class ChunkSizeScoringTests(unittest.TestCase):
    """All inputs are synthetic; no model assets or user recordings are opened."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stt-chunk-score-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.samples = [
            self.sample("scoped", "red blue", scope="User placed the disputed ending outside this clip."),
            self.sample("hinglish", "ki alpha beta"),
            self.sample("english", "one two", english=True),
            self.sample("pending", None),
        ]
        self.manifest = {"sizes": [10, 20], "samples": self.samples}
        save(self.root / "manifest.json", self.manifest)
        self.results = {}
        for model, drafts in {
            "apex": {
                "scoped": ["red"],
                "hinglish": ["ki alpha", "gamma"],
                "english": ["one extra two"],
                "pending": ["Unreviewed\n  words & <literal>", "  trailing words\n"],
            },
            "trelis": {
                "scoped": ["लाल नीला"],
                "hinglish": ["की अल्फा", "बीटा"],
                "english": ["one extra two"],
                "pending": ["Unreviewed\n  शब्द & <literal>", "  अंतिम शब्द\n"],
            },
        }.items():
            result = {"state": "complete", "manifest_sha256": file_digest(self.root / "manifest.json"),
                      "network_attempts": [], "chunks": {}}
            for sample in self.samples:
                for parts in sample["partitions"].values():
                    for part, text in zip(parts, drafts[sample["id"]], strict=True):
                        result["chunks"][part["id"]] = {"status": "ok", "audio_sha256": part["audio_sha256"], "text": text}
            self.results[model] = result
            self.save_result(model)

    @staticmethod
    def sample(identifier, reference, *, scope="", english=False):
        count = 2 if identifier in ("hinglish", "pending") else 1
        partitions = {
            str(size): [{"id": f"{identifier}:{size}:{i}",
                         "audio_sha256": hashlib.sha256(f"{identifier}:{size}:{i}".encode()).hexdigest()}
                        for i in range(count)]
            for size in (10, 20)
        }
        return {"id": identifier, "reference_roman": reference,
                "reference_status": "user_reviewed" if reference is not None else "pending",
                "reference_scope_note": scope, "direct_trelis_reference_candidate": english,
                "entities": [], "partitions": partitions}

    def save_result(self, model):
        save(self.root / "results" / f"{model}.json", self.results[model])

    def score(self):
        with redirect_stdout(io.StringIO()):
            sweep.score(self.root)
        return json.loads((self.root / "summary.json").read_text(encoding="utf-8"))

    def assert_rejected_without_summary(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            sweep.score(self.root)
        self.assertFalse((self.root / "summary.json").exists())

    def test_trelis_devanagari_english_prediction_is_not_scored(self):
        self.results["trelis"]["chunks"]["english:10:0"]["text"] = "one दो"
        self.save_result("trelis")
        summary = self.score()
        short, long = summary["models"]["trelis"]["sizes"]
        candidate = next(s for s in short["samples"] if s["sample_id"] == "english")
        self.assertEqual(candidate["score_status"], "script_confounded")
        self.assertEqual(candidate["text"], "one दो")
        self.assertIsNone(candidate["metrics"])
        self.assertIsNone(candidate["entity_counts"])
        self.assertEqual(short["scored_clips"], 0)
        self.assertEqual(short["aggregate"]["reference_words"], 0)
        self.assertIsNone(short["aggregate"]["wer"])
        self.assertIsNone(short["aggregate"]["deletion_rate"])
        self.assertEqual(long["scored_clips"], 1)
        self.assertFalse(summary["trelis_romanization"])

    def test_trelis_hinglish_never_uses_roman_reference_even_for_latin_output(self):
        # Merely producing Latin characters must not admit a Hinglish reference.
        self.results["trelis"]["chunks"]["hinglish:20:0"]["text"] = "ki alpha"
        self.results["trelis"]["chunks"]["hinglish:20:1"]["text"] = "beta"
        self.save_result("trelis")
        summary = self.score()
        for row in summary["models"]["trelis"]["sizes"]:
            self.assertEqual(row["scored_clips"], 1)
            self.assertEqual(row["aggregate"]["reference_words"], 2)
            for sample in row["samples"]:
                if sample["sample_id"] in ("scoped", "hinglish"):
                    self.assertEqual(sample["score_status"], "pending_native_script_reference")
                    self.assertIsNone(sample["metrics"])
                elif sample["sample_id"] == "pending":
                    self.assertEqual(sample["score_status"], "pending_human_review")
                    self.assertIsNone(sample["metrics"])

    def test_apex_counts_word_errors_and_cutoff_sensitivity_separately(self):
        summary = self.score()
        for row in summary["models"]["apex"]["sizes"]:
            self.assertEqual(row["scored_clips"], 3)
            all_clips = row["aggregate"]
            self.assertEqual({key: all_clips[key] for key in ("reference_words", "hypothesis_words", "errors", "substitutions", "deletions", "insertions")},
                             {"reference_words": 7, "hypothesis_words": 7, "errors": 3, "substitutions": 1, "deletions": 1, "insertions": 1})
            self.assertAlmostEqual(all_clips["wer"], 3 / 7)
            self.assertAlmostEqual(all_clips["deletion_rate"], 1 / 7)
            certain = row["boundary_certain_aggregate"]
            self.assertEqual({key: certain[key] for key in ("reference_words", "hypothesis_words", "errors", "substitutions", "deletions", "insertions")},
                             {"reference_words": 5, "hypothesis_words": 6, "errors": 2, "substitutions": 1, "deletions": 0, "insertions": 1})
            self.assertAlmostEqual(certain["wer"], 2 / 5)

    def test_raw_joined_text_and_input_files_remain_unchanged(self):
        before = {name: file_digest(self.root / name) for name in ("manifest.json", "results/apex.json", "results/trelis.json")}
        summary = self.score()
        for model in ("apex", "trelis"):
            for row in summary["models"][model]["sizes"]:
                pending = next(s for s in row["samples"] if s["sample_id"] == "pending")
                expected = ("Unreviewed\n  शब्द & <literal>\n\n  अंतिम शब्द\n" if model == "trelis"
                            else "Unreviewed\n  words & <literal>\n\n  trailing words\n")
                self.assertEqual(pending["text"], expected)
                self.assertEqual(pending["chunks"], 2)
        self.assertEqual(before, {name: file_digest(self.root / name) for name in before})
        self.assertFalse(summary["held_out"])
        self.assertFalse(summary["release_qualified"])

    def test_incomplete_second_model_rejects_without_partial_summary(self):
        self.results["trelis"]["state"] = "running"
        self.save_result("trelis")
        self.assert_rejected_without_summary()

    def test_manifest_binding_mismatch_rejects_without_summary(self):
        self.results["apex"]["manifest_sha256"] = "0" * 64
        self.save_result("apex")
        self.assert_rejected_without_summary()

    def test_prediction_audio_binding_mismatch_rejects_without_summary(self):
        self.results["trelis"]["chunks"]["english:20:0"]["audio_sha256"] = "f" * 64
        self.save_result("trelis")
        self.assert_rejected_without_summary()

    def test_failed_chunk_rejects_without_summary(self):
        self.results["apex"]["chunks"]["english:10:0"]["status"] = "error"
        self.save_result("apex")
        self.assert_rejected_without_summary()

    def test_missing_chunk_rejects_without_summary(self):
        del self.results["trelis"]["chunks"]["english:20:0"]
        self.save_result("trelis")
        # Current scorer uses a direct dictionary lookup, so missing results
        # raise KeyError rather than the ValueError used for malformed results.
        with redirect_stdout(io.StringIO()), self.assertRaises((KeyError, ValueError)):
            sweep.score(self.root)
        self.assertFalse((self.root / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
