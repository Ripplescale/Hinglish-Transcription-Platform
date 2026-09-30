"""Offline guards for model intake and user-recording preparation.

All network calls are replaced with in-memory responses. Audio fixtures contain
only deterministic synthetic PCM, never microphone or user-recording data.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave


LAB_ROOT = Path(__file__).resolve().parents[1]


def _import_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, LAB_ROOT / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


model_assets = _import_tool("model_assets")
prepare_recordings = _import_tool("prepare_recordings")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blob(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def _write_wav(path: Path, duration=5.25, rate=16000, channels=1, width=2, value=17):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(rate)
        frame = value.to_bytes(width, "little", signed=True) * channels
        audio.writeframes(frame * int(duration * rate))


class TemporaryTest(unittest.TestCase):
    def setUp(self):
        # Use system TEMP so OneDrive does not race atomic-replacement tests.
        self.temporary = tempfile.TemporaryDirectory(prefix="stt-tool-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)


class RemoteIntegrityTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.data = b"small artifact\x00\x01\xff"
        self.path = self.root / "artifact.bin"
        self.path.write_bytes(self.data)

    def test_lfs_sha256_accepts_only_matching_content(self):
        entry = {"size": len(self.data), "lfs": {"sha256": _sha(self.data)}}
        model_assets.verify_remote_file(self.path, entry)
        self.path.write_bytes(b"X" + self.data[1:])
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            model_assets.verify_remote_file(self.path, entry)

    def test_git_blob_hash_includes_git_header(self):
        model_assets.verify_remote_file(self.path, {"blobId": _blob(self.data)})
        with self.assertRaisesRegex(ValueError, "Git blob mismatch"):
            model_assets.verify_remote_file(self.path, {"blobId": hashlib.sha1(self.data).hexdigest()})

    def test_size_mismatch_fails_even_when_digest_matches(self):
        with self.assertRaisesRegex(ValueError, "Size mismatch"):
            model_assets.verify_remote_file(self.path, {"size": len(self.data) + 1, "lfs": {"sha256": _sha(self.data)}})

    def test_missing_upstream_digest_is_not_called_verified(self):
        with self.assertRaisesRegex(ValueError, "No upstream digest"):
            model_assets.verify_remote_file(self.path, {"size": len(self.data)})


class ModelAssetTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.revision = "1" * 40
        self.spec = {"id": "fixture", "repo_id": "fixture-owner/model", "source_revision": self.revision, "license": "apache-2.0"}
        self.registry = self.root / "registry.json"
        self._write_registry()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(model_assets, "REGISTRY", self.registry).start()
        mock.patch.object(model_assets, "runtime_root", return_value=self.root / "runtime").start()
        # A test must explicitly supply a fake response to use the downloader.
        self.opener_factory = mock.patch.object(model_assets.urllib.request, "build_opener", side_effect=AssertionError("Network access forbidden in tests")).start()

    def _write_registry(self):
        self.registry.write_text(json.dumps({"policy": {"stt_licenses": ["apache-2.0", "mit"]}, "models": [self.spec], "excluded": [{"repo_id": "shunyalabs/zero-stt-hinglish"}]}), encoding="utf-8")

    def _metadata(self, files=None):
        files = files if files is not None else {"config.json": b"{}", "model.safetensors": b"tiny synthetic weights"}
        return {"sha": self.revision, "cardData": {"license": "apache-2.0"}, "siblings": [{"rfilename": name, "size": len(data), "lfs": {"sha256": _sha(data)}} for name, data in files.items()]}, files

    def _fake_opener(self, metadata, files):
        seen = []

        def open_response(url, **kwargs):
            url = url.full_url if isinstance(url, model_assets.urllib.request.Request) else url
            seen.append(url)
            if "/api/models/" in url:
                return io.BytesIO(json.dumps(metadata).encode())
            prefix = f"https://huggingface.co/{self.spec['repo_id']}/resolve/{self.revision}/"
            self.assertTrue(url.startswith(prefix), url)
            name = model_assets.urllib.parse.unquote(url[len(prefix):])
            return io.BytesIO(files[name])

        opener = mock.Mock()
        opener.open.side_effect = open_response
        self.opener_factory.side_effect = None
        self.opener_factory.return_value = opener
        return seen

    def _artifact(self, files=None):
        directory = self.root / "runtime" / "models" / self.spec["id"] / self.revision
        directory.mkdir(parents=True)
        files = files if files is not None else {"model.safetensors": b"synthetic weights"}
        entries = []
        for name, data in files.items():
            (directory / name).write_bytes(data)
            entries.append({"path": name, "size": len(data), "sha256": _sha(data)})
        manifest = {"version": 1, "model_id": self.spec["id"], "repo_id": self.spec["repo_id"], "source_revision": self.revision, "license": self.spec["license"], "files": entries}
        path = directory / "artifact-manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return directory, path, manifest

    def test_excluded_unknown_models_fail_before_network(self):
        for name in ("shunyalabs/zero-stt-hinglish", "shunya", "orato", "missing"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unknown or excluded"):
                model_assets.fetch(name)
        self.opener_factory.assert_not_called()

    def test_mutable_or_malformed_revision_is_rejected(self):
        for revision in ("main", "v1", "a" * 39, "z" * 40, "../escape"):
            with self.subTest(revision=revision):
                self.spec["source_revision"] = revision
                self._write_registry()
                with self.assertRaisesRegex(ValueError, "immutable commit"):
                    model_assets.load_spec("fixture")

    def test_restricted_license_cannot_be_admitted_by_id(self):
        self.spec["license"] = "openrail"
        self._write_registry()
        with self.assertRaisesRegex(ValueError, "license is not admitted"):
            model_assets.load_spec("fixture")

    def test_fetch_checks_pinned_revision_before_creating_artifacts(self):
        metadata, files = self._metadata()
        metadata["sha"] = "2" * 40
        self._fake_opener(metadata, files)
        with self.assertRaisesRegex(ValueError, "revision does not match"):
            model_assets.fetch("fixture")
        self.assertFalse((self.root / "runtime").exists())

    def test_fetch_checks_upstream_license_before_creating_artifacts(self):
        metadata, files = self._metadata()
        metadata["cardData"]["license"] = "openrail"
        self._fake_opener(metadata, files)
        with self.assertRaisesRegex(ValueError, "license does not match"):
            model_assets.fetch("fixture")
        self.assertFalse((self.root / "runtime").exists())

    def test_fetch_uses_commit_urls_and_reuses_only_verified_files(self):
        metadata, files = self._metadata()
        seen = self._fake_opener(metadata, files)
        result = model_assets.fetch("fixture")
        self.assertEqual(result["status"], "downloaded_and_verified")
        self.assertEqual(len(seen), 3)
        self.assertIn(f"/revision/{self.revision}?blobs=true", seen[0])
        artifact = Path(result["artifact_path"])
        manifest = json.loads((artifact / "artifact-manifest.json").read_text())
        self.assertFalse(manifest["smoke_audio_is_human_verified_reference"])
        self.assertEqual(manifest["source_revision"], self.revision)
        seen.clear()
        model_assets.fetch("fixture")
        self.assertEqual(len(seen), 1, "Valid local content should avoid another binary download")
        (artifact / "model.safetensors").write_bytes(b"corrupt")
        seen.clear()
        model_assets.fetch("fixture")
        self.assertEqual(len(seen), 2)
        self.assertEqual((artifact / "model.safetensors").read_bytes(), files["model.safetensors"])

    def test_corrupt_download_does_not_replace_existing_model(self):
        metadata, files = self._metadata()
        directory, manifest_path, _ = self._artifact({"model.safetensors": b"old content"})
        files["model.safetensors"] = b"X" * len(files["model.safetensors"])
        self._fake_opener(metadata, files)
        with self.assertRaises(ValueError):
            model_assets.fetch("fixture")
        self.assertEqual((directory / "model.safetensors").read_bytes(), b"old content")

    def test_verify_detects_tamper_and_provenance_change(self):
        directory, path, manifest = self._artifact()
        self.assertEqual(model_assets.verify("fixture")["status"], "verified")
        (directory / "model.safetensors").write_bytes(b"changed content")
        with self.assertRaisesRegex(ValueError, "corrupted"):
            model_assets.verify("fixture")
        manifest["source_revision"] = "2" * 40
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "provenance"):
            model_assets.verify("fixture")

    def test_verify_rejects_path_traversal(self):
        _, path, manifest = self._artifact()
        manifest["files"][0]["path"] = "../outside.safetensors"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "escapes"):
            model_assets.verify("fixture")

    def test_verify_rejects_empty_manifest_instead_of_claiming_verified(self):
        self._artifact({})
        with self.assertRaises(ValueError):
            model_assets.verify("fixture")

    def test_verify_rejects_license_or_model_identity_mismatch(self):
        _, path, manifest = self._artifact()
        for field, value in (("model_id", "another-model"), ("license", "openrail")):
            with self.subTest(field=field):
                changed = dict(manifest, **{field: value})
                path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    model_assets.verify("fixture")


class RecordingIntakeTests(TemporaryTest):
    def setUp(self):
        super().setUp()
        self.source = self.root / "call's [review] $(local) हिंदी.wav"
        _write_wav(self.source)
        self.output = self.root / "private intake"

    def _prepare(self, paths=None, **kwargs):
        return prepare_recordings.prepare(paths or [self.source], self.output, **kwargs)

    def test_verified_copy_preserves_original_and_audio_frames(self):
        before = self.source.read_bytes()
        stat = self.source.stat()
        manifest_path = self._prepare(clip_seconds=5)
        manifest = json.loads(manifest_path.read_text())
        provenance = json.loads((self.output / "intake-provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.source.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual((self.output / "call-01" / "original.wav").read_bytes(), before)
        self.assertEqual(provenance["sources"][0]["original_path"], str(self.source))
        self.assertEqual(provenance["sources"][0]["sha256"], _sha(before))
        recovered = b""
        for sample in manifest["samples"]:
            clip = self.output / sample["audio"]
            self.assertEqual(sample["audio_sha256"], _sha(clip.read_bytes()))
            with wave.open(str(clip), "rb") as audio:
                self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getsampwidth()), (16000, 1, 2))
                recovered += audio.readframes(audio.getnframes())
        with wave.open(str(self.source), "rb") as original:
            self.assertEqual(recovered, original.readframes(original.getnframes()))
        self.assertEqual(manifest["samples"][-1]["source_end_seconds"], 5.25)

    def test_corrupt_copy_fails_before_generating_analysis(self):
        copyfile = shutil.copyfile

        def corrupt_copy(source, target):
            result = copyfile(source, target)
            if Path(target).name.startswith("original"):
                Path(target).write_bytes(b"tampered copy")
            return result

        before = self.source.read_bytes()
        with mock.patch.object(prepare_recordings.shutil, "copyfile", side_effect=corrupt_copy):
            with self.assertRaisesRegex(ValueError, "Copy verification failed"):
                self._prepare()
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.output / "call-01" / "analysis.wav").exists())

    def test_split_keeps_every_call_together(self):
        other = self.root / "other.wav"
        _write_wav(other, duration=7.25, value=28)
        manifest_path = self._prepare([self.source, other], clip_seconds=5)
        document = json.loads(manifest_path.read_text())
        self.assertEqual(len(document["samples"]), 4)
        per_call = {}
        for sample in document["samples"]:
            per_call.setdefault(sample["call_id"], set()).add(sample["split"])
        self.assertEqual(per_call, {"call-01": {"dev"}, "call-02": {"test"}})
        sys.path.insert(0, str(LAB_ROOT))
        try:
            from sttbench.manifest import load_manifest
            loaded = load_manifest(manifest_path)
        finally:
            sys.path.remove(str(LAB_ROOT))
        self.assertEqual(len(loaded["samples"]), 4)

    def test_pending_references_are_not_fabricated_as_silence(self):
        document = json.loads(self._prepare().read_text())
        for sample in document["samples"]:
            self.assertEqual(sample["reference_status"], "pending")
            self.assertNotIn("reference", sample)
            self.assertEqual(sample["entities"], [])
            self.assertNotIn("speaker_ids", sample)
        self.assertEqual({sample["split"] for sample in document["samples"]}, {"dev"})

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "manifest.json"
        sentinel.write_text("existing intake")
        with self.assertRaisesRegex(ValueError, "never overwritten"):
            self._prepare()
        self.assertEqual(sentinel.read_text(), "existing intake")
        self.assertEqual(list(self.output.iterdir()), [sentinel])

    def test_empty_inputs_and_out_of_range_duration_rejected(self):
        for seconds in (0, 4, 31):
            with self.subTest(seconds=seconds), self.assertRaisesRegex(ValueError, "between 5 and 30"):
                prepare_recordings.prepare([self.source], self.output, clip_seconds=seconds)
        with self.assertRaisesRegex(ValueError, "at least one"):
            prepare_recordings.prepare([], self.output)
        self.assertFalse(self.output.exists())

    def test_incompatible_wav_requires_explicit_decoder(self):
        for rate, channels, width in ((8000, 1, 2), (16000, 2, 2), (16000, 1, 1)):
            with self.subTest(rate=rate, channels=channels, width=width):
                source = self.root / f"{rate}-{channels}-{width}.wav"
                _write_wav(source, rate=rate, channels=channels, width=width)
                output = self.root / f"output-{rate}-{channels}-{width}"
                with self.assertRaisesRegex(ValueError, "needs FFmpeg"):
                    prepare_recordings.prepare([source], output)
                self.assertEqual((output / "call-01" / "original.wav").read_bytes(), source.read_bytes())
                self.assertFalse((output / "manifest.json").exists())

    def test_decoder_uses_argument_list_and_normalized_pcm(self):
        source = self.root / "meeting's $(echo) [1].mp3"
        source.write_bytes(b"placeholder compressed audio")
        ffmpeg = self.root / "decoder with spaces.exe"
        ffmpeg.touch()

        def decode(arguments, **kwargs):
            self.assertIsInstance(arguments, list)
            self.assertEqual(arguments[0], str(ffmpeg.resolve()))
            self.assertFalse(kwargs.get("shell", False))
            self.assertIn("-nostdin", arguments)
            self.assertEqual(arguments[arguments.index("-i") + 1], str(self.output / "call-01" / "original.mp3"))
            self.assertEqual(arguments[arguments.index("-ar") + 1], "16000")
            _write_wav(Path(arguments[-1]))
            return subprocess.CompletedProcess(arguments, 0, "", "")

        with mock.patch.object(prepare_recordings.subprocess, "run", side_effect=decode) as runner:
            path = self._prepare([source], ffmpeg=ffmpeg)
        runner.assert_called_once()
        self.assertTrue(path.exists())
        self.assertEqual(source.read_bytes(), b"placeholder compressed audio")

    def test_decoder_failure_preserves_original_and_does_not_publish_manifest(self):
        source = self.root / "compressed.mp3"
        source.write_bytes(b"original audio")
        ffmpeg = self.root / "decoder.exe"
        ffmpeg.touch()
        failure = subprocess.CompletedProcess([], 1, "", "unsupported codec")
        with mock.patch.object(prepare_recordings.subprocess, "run", return_value=failure):
            with self.assertRaisesRegex(ValueError, "Audio decoding failed.*unsupported codec"):
                self._prepare([source], ffmpeg=ffmpeg)
        self.assertEqual((self.output / "call-01" / "original.mp3").read_bytes(), b"original audio")
        self.assertFalse((self.output / "manifest.json").exists())

    def test_decoder_output_is_validated_not_silently_relabelled(self):
        source = self.root / "compressed.mp3"
        source.write_bytes(b"compressed fixture")
        ffmpeg = self.root / "decoder.exe"
        ffmpeg.touch()

        def bad_decoder(arguments, **kwargs):
            _write_wav(Path(arguments[-1]), rate=8000, channels=2)
            return subprocess.CompletedProcess(arguments, 0, "", "")

        with mock.patch.object(prepare_recordings.subprocess, "run", side_effect=bad_decoder):
            with self.assertRaises(ValueError):
                self._prepare([source], ffmpeg=ffmpeg)
        self.assertFalse((self.output / "manifest.json").exists())

    def test_empty_audio_does_not_publish_unusable_empty_manifest(self):
        empty = self.root / "empty.wav"
        _write_wav(empty, duration=0)
        with self.assertRaises(ValueError):
            self._prepare([empty])
        self.assertFalse((self.output / "manifest.json").exists())

    def test_duplicate_call_is_rejected_before_creating_dev_test_leakage(self):
        with self.assertRaises(ValueError):
            self._prepare([self.source, self.source])
        self.assertFalse((self.output / "manifest.json").exists())

    def test_duplicate_content_under_another_filename_is_rejected(self):
        duplicate = self.root / "renamed.wav"
        duplicate.write_bytes(self.source.read_bytes())
        with self.assertRaises(ValueError):
            self._prepare([self.source, duplicate])
        self.assertFalse(self.output.exists())

    def test_truncated_wav_does_not_publish_inaccurate_clip_timestamps(self):
        truncated = self.root / "truncated.wav"
        # Preserve the header's original 5.25-second length while removing
        # actual sample frames, as can happen after an interrupted file write.
        truncated.write_bytes(self.source.read_bytes()[:-16000])
        with self.assertRaises(ValueError):
            self._prepare([truncated])
        self.assertFalse((self.output / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
