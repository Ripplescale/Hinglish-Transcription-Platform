"""Downloader resume contracts using synthetic bytes and in-memory responses."""

import contextlib
import hashlib
import http.client
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.request


_TOOL = Path(__file__).resolve().parents[1] / "tools" / "model_assets.py"
_SPEC = importlib.util.spec_from_file_location("model_assets_resume_tests", _TOOL)
assert _SPEC and _SPEC.loader
model_assets = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(model_assets)


class Response:
    def __init__(self, chunks, status=200, content_range=None):
        self.chunks = list(chunks)
        self.status = status
        self.headers = {} if content_range is None else {"Content-Range": content_range}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if isinstance(chunk, Exception):
            raise chunk
        return chunk


class DownloadResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="stt-resume-tests-", dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.partial = Path(self.temporary.name) / "weights.safetensors.part"
        self.data = b"0123456789abcdef"
        self.entry = {"size": len(self.data), "lfs": {"sha256": hashlib.sha256(self.data).hexdigest()}}
        self.url = "https://example.invalid/pinned/model.safetensors"
        self.addCleanup(patch.stopall)
        self.sleep = patch.object(model_assets.time, "sleep").start()
        patch.object(model_assets.urllib.request, "urlopen", side_effect=AssertionError("Network forbidden")).start()
        patch.object(model_assets.urllib.request, "build_opener", side_effect=AssertionError("Network forbidden")).start()

    def run_download(self, *responses, attempts=3):
        opener = Mock()
        opener.open.side_effect = list(responses)
        with contextlib.redirect_stdout(io.StringIO()):
            model_assets.download_file(opener, self.url, self.partial, self.entry, attempts=attempts)
        return opener

    def test_interrupted_response_resumes_at_written_byte_offset_and_verifies_whole_hash(self):
        first = Response([self.data[:5], http.client.IncompleteRead(b"", 11)])
        second = Response([self.data[5:]], status=206, content_range="bytes 5-15/16")
        opener = self.run_download(first, second)
        self.assertEqual(opener.open.call_args_list[0].args[0], self.url)
        resumed = opener.open.call_args_list[1].args[0]
        self.assertIsInstance(resumed, urllib.request.Request)
        self.assertEqual(resumed.full_url, self.url)
        self.assertEqual(resumed.get_header("Range"), "bytes=5-")
        self.assertEqual(self.partial.read_bytes(), self.data)
        self.assertEqual(model_assets.file_digest(self.partial), self.entry["lfs"]["sha256"])
        self.sleep.assert_called_once_with(1)

    def test_server_200_ignoring_range_restarts_instead_of_appending(self):
        self.partial.write_bytes(self.data[:6])
        opener = self.run_download(Response([self.data], status=200))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Range"), "bytes=6-")
        self.assertEqual(self.partial.read_bytes(), self.data)
        self.assertEqual(self.partial.stat().st_size, len(self.data))
        self.sleep.assert_not_called()

    def test_malformed_wrong_start_or_wrong_total_range_rejected_without_writing(self):
        for header in ("invalid", "bytes 4-15/16", "bytes 5-15/17", "bytes 5-15/*"):
            with self.subTest(header=header):
                self.partial.write_bytes(self.data[:5])
                opener = Mock()
                opener.open.return_value = Response([self.data[5:]], status=206, content_range=header)
                with self.assertRaisesRegex(ValueError, "Content-Range"):
                    model_assets.download_file(opener, self.url, self.partial, self.entry)
                self.assertEqual(self.partial.read_bytes(), self.data[:5])
                opener.open.assert_called_once()

    def test_reversed_or_out_of_bounds_range_rejected_even_if_payload_hash_would_match(self):
        for header in ("bytes 5-4/16", "bytes 5-16/16"):
            with self.subTest(header=header):
                self.partial.write_bytes(self.data[:5])
                opener = Mock()
                opener.open.return_value = Response([self.data[5:]], status=206, content_range=header)
                with self.assertRaisesRegex(ValueError, "Content-Range"):
                    model_assets.download_file(opener, self.url, self.partial, self.entry)
                self.assertEqual(self.partial.read_bytes(), self.data[:5])

    def test_complete_corrupt_partial_forces_fresh_fetch_without_range(self):
        self.partial.write_bytes(b"X" * len(self.data))
        opener = self.run_download(Response([self.data]))
        self.assertEqual(opener.open.call_args.args[0], self.url)
        self.assertEqual(self.partial.read_bytes(), self.data)
        self.sleep.assert_not_called()

    def test_complete_verified_partial_skips_download(self):
        self.partial.write_bytes(self.data)
        opener = self.run_download()
        opener.open.assert_not_called()

    def test_resumed_wrong_content_rejected_by_full_upstream_hash(self):
        self.partial.write_bytes(self.data[:5])
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            self.run_download(Response([b"X" * 11], status=206, content_range="bytes 5-15/16"))


if __name__ == "__main__":
    unittest.main()
