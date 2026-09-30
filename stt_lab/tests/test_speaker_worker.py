import json
from pathlib import Path
import tempfile
import unittest
import wave
import threading
import subprocess
import sys
import os
from unittest import mock
from sttbench.speaker_worker import MODEL_ID, file_sha, inspect_model, prepare_request
from sttbench.speaker_supervision import job_lock, parent_watchdog


class SpeakerWorkerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = self.root / 'model'; self.model.mkdir()
        (self.model / 'config.yaml').write_text('pipeline: test', encoding='utf-8')
        self.manifest = {'model_id': MODEL_ID, 'revision': 'a'*40, 'files': {'config.yaml': file_sha(self.model / 'config.yaml')}}
        (self.model / 'sttapp-model-manifest.json').write_text(json.dumps(self.manifest))
        self.config = {'model_path': str(self.model), 'model_revision': 'a'*40}
        self.session = self.root / 'session-one'; self.session.mkdir()
        (self.session / 'session.json').write_text(json.dumps({'session_id': 'session-one'}))
        with wave.open(str(self.session / 'audio.wav'), 'wb') as output:
            output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000); output.writeframes(bytes(32000))
        self.transcript = self.root / 'transcription.json'
        self.transcript.write_text(json.dumps({'state': 'complete', 'job_id': 'stt-one', 'session_dir': str(self.session), 'segments': []}))
        self.request = {'job_id': 'speakers-one', 'meeting_id': 'meeting-one', 'session_dir': str(self.session), 'transcription_job_id': 'stt-one', 'transcription_status_path': str(self.transcript)}
        self.request_path = self.root / 'request.json'; self.request_path.write_text(json.dumps(self.request))

    def tearDown(self):
        self.temp.cleanup()

    def test_hash_manifest_binds_local_model_and_source_audio(self):
        _, _, _, _, _, identity, _ = prepare_request(self.config, self.request, self.request_path)
        self.assertEqual(identity['audio_sha256'], file_sha(self.session / 'audio.wav'))
        self.assertEqual(identity['transcription_status_sha256'], file_sha(self.transcript))
        (self.model / 'config.yaml').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            inspect_model(self.config)

    def test_manifest_path_traversal_is_rejected(self):
        outside = self.root / 'other'; outside.write_text('external')
        self.manifest['files']['../other'] = file_sha(outside)
        (self.model / 'sttapp-model-manifest.json').write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, 'integrity'):
            inspect_model(self.config)

    def test_incomplete_or_unrelated_transcription_is_rejected(self):
        for changed in ({'state': 'running'}, {'job_id': 'other'}, {'session_dir': str(self.root)}):
            value = {'state': 'complete', 'job_id': 'stt-one', 'session_dir': str(self.session), **changed}
            self.transcript.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                prepare_request(self.config, self.request, self.request_path)

    def test_previous_speakers_from_another_call_are_rejected(self):
        previous = self.root / 'previous.json'; previous.write_text(json.dumps({'session_id': 'another'}))
        self.request['previous_speakers_path'] = str(previous)
        with self.assertRaisesRegex(ValueError, 'another recording'):
            prepare_request(self.config, self.request, self.request_path)

    def test_duplicate_worker_cannot_acquire_job_ownership(self):
        lock = self.root / 'worker.lock'
        with job_lock(lock):
            with self.assertRaises(OSError):
                with job_lock(lock):
                    self.fail('Two owners acquired the same job')
        with job_lock(lock):
            pass

    def test_parent_exit_is_seen_during_work_without_pipeline_callbacks(self):
        parent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=0x08000000 if os.name == 'nt' else 0)
        observed = threading.Event()
        codes = []
        def exited(code):
            codes.append(code); observed.set()
        try:
            with parent_watchdog(parent.pid, exited):
                parent.terminate(); parent.wait(timeout=5)
                self.assertTrue(observed.wait(3), 'Watchdog did not observe parent termination')
            self.assertEqual(codes, [2])
        finally:
            if parent.poll() is None:
                parent.terminate(); parent.wait(timeout=5)

    def test_atomic_status_retries_transient_windows_reader_conflicts(self):
        from sttbench import speaker_worker
        original = os.replace
        conflicts = 0
        def replace(source, destination):
            nonlocal conflicts
            conflicts += 1
            if conflicts < 3:
                raise PermissionError('Synthetic reader conflict')
            return original(source, destination)
        target = self.root / 'status.json'
        with mock.patch.object(speaker_worker.os, 'replace', side_effect=replace):
            speaker_worker.write_status(target, {'state': 'complete'})
        self.assertEqual(json.loads(target.read_text())['state'], 'complete')
        self.assertEqual(conflicts, 3)


if __name__ == '__main__':
    unittest.main()
