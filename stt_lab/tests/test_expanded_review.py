"""Boundary, reference-integrity and HTML-injection checks for private review."""
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import prepare_expanded_review as prepare
import render_expanded_review as render


class ExpandedReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stt-expanded-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wav(self, path, frames=16000):
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x01\x00" * frames)

    def fixture(self):
        for name in ("score.wav", "context.wav"):
            self.wav(self.root / name)
        clip = {"id": "sample-1", "call_id": "call-03", "audio": "score.wav", "context_audio": "context.wav",
                "audio_sha256": prepare.digest(self.root / "score.wav"), "context_sha256": prepare.digest(self.root / "context.wav"),
                "start_seconds": 20, "end_seconds": 48, "duration_seconds": 28,
                "context_start_seconds": 15, "context_duration_seconds": 38, "expected_language": "Hinglish"}
        manifest = {"review_phase": "expanded_development_review_batch_1", "clips": [clip],
                    "calls": [{"id": "call-03", "title": "Meeting synthetic"}]}
        path = self.root / "screening.json"
        prepare.write_json(path, manifest)
        seed = {"version": 1, "kind": "expanded_human_reference_review", "screening_sha256": prepare.digest(path),
                "human_reviewed": False, "samples": [{"sample_id": clip["id"], "audio_sha256": clip["audio_sha256"],
                "reference_roman": "", "notes": "", "names": [], "numbers": [], "language": "unconfirmed",
                "listened": False, "review_completed": False, "no_speech": False,
                "human_reviewed": False, "reference_status": "pending"}]}
        prepare.write_json(self.root / "user-references-pending.json", seed)
        return path, clip

    def test_new_windows_exclude_old_context_and_padding(self):
        old = [{"start_seconds": start, "duration_seconds": 25.0373125} for start in (110.983, 399.11, 687.237)]
        windows = prepare.select_windows(823.22, old)
        self.assertEqual(len(windows), 4)
        self.assertEqual(windows, prepare.select_windows(823.22, old))
        for left, right in windows:
            self.assertEqual(right - left, 28)
            for previous in old:
                self.assertFalse(left - 5 < previous["start_seconds"] + previous["duration_seconds"] + 20 and right + 5 > previous["start_seconds"] - 20)

    def test_sample_accurate_cut_preserves_only_selected_frames(self):
        source = self.root / "source.wav"
        raw = b"".join(struct.pack("<h", value) for value in range(1000))
        with wave.open(str(source), "wb") as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000); output.writeframes(raw)
        target = self.root / "clip.wav"
        self.assertEqual(prepare.pcm_slice(source, target, 123 / 16000, 987 / 16000), 864 / 16000)
        with wave.open(str(target), "rb") as audio:
            self.assertEqual(audio.readframes(audio.getnframes()), raw[246:1974])

    def test_reject_out_of_range_audio_cut(self):
        source = self.root / "source.wav"
        self.wav(source)
        with self.assertRaisesRegex(ValueError, "outside"):
            prepare.pcm_slice(source, self.root / "bad.wav", 0, 2)

    def test_media_traversal_and_remote_paths_rejected(self):
        outside = self.root.parent / f"{self.root.name}-outside.wav"
        outside.write_bytes(b"x")
        self.addCleanup(outside.unlink)
        for value in ("https://example.com/audio.wav", "C:\\audio.wav", "../" + outside.name):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare.inside(self.root, value)

    def test_renderer_preserves_pending_blank_references(self):
        path, _ = self.fixture()
        html = render.render(path).read_text(encoding="utf-8")
        self.assertIn('data-field="reference_roman"', html)
        self.assertIn('Download review JSON', html)
        self.assertIn("connect-src &#x27;none&#x27;", html)
        self.assertNotIn('type="checkbox" checked', html)
        self.assertIn('"reference_roman": ""', html)
        self.assertIn('"human_reviewed": false', html)

    def test_transcript_markup_is_never_executable(self):
        path, clip = self.fixture()
        (self.root / "results").mkdir()
        attack = '</script><img src=x onerror="alert(1)">'
        prepare.write_json(self.root / "results" / "apex.json", {"model_id": "apex", "screening_sha256": prepare.digest(path),
                           "clips": {clip["id"]: {"status": "ok", "text": attack, "audio_sha256": clip["audio_sha256"]}}})
        html = render.render(path).read_text(encoding="utf-8")
        self.assertNotIn(attack, html)
        self.assertIn('&lt;/script&gt;&lt;img', html)
        self.assertNotIn('</script>', render.safe_json({"text": attack}))

    def test_draft_audio_mismatch_rejected(self):
        path, clip = self.fixture()
        (self.root / "results").mkdir()
        prepare.write_json(self.root / "results" / "apex.json", {"model_id": "apex", "screening_sha256": prepare.digest(path),
                           "clips": {clip["id"]: {"status": "ok", "text": "draft", "audio_sha256": "wrong"}}})
        with self.assertRaisesRegex(ValueError, "audio identity"):
            render.render(path)

    def test_nonblank_pending_template_cannot_become_reference(self):
        path, _ = self.fixture()
        template = self.root / "user-references-pending.json"
        value = json.loads(template.read_text())
        value["samples"][0]["reference_roman"] = "machine output"
        prepare.write_json(template, value)
        with self.assertRaisesRegex(ValueError, "blank and pending"):
            render.render(path)

    def test_changed_audio_rejected(self):
        path, _ = self.fixture()
        (self.root / "score.wav").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "audio changed"):
            render.render(path)

    def test_existing_artifact_not_overwritten(self):
        path, _ = self.fixture()
        render.render(path)
        with self.assertRaisesRegex(ValueError, "new HTML"):
            render.render(path)


if __name__ == "__main__":
    unittest.main()
