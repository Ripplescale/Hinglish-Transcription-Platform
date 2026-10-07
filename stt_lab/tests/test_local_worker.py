"""Synthetic recording tests; no microphone, downloaded model or network calls."""
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np
from scipy.signal import resample_poly
from sttbench import local_worker as worker
from sttbench.manifest import file_digest, read_json, write_json


class LocalWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='stt-worker-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = self.root/'session'
        self.session.mkdir()
        write_json(self.session/'session.json', {'id':'synthetic'})

    def chunk(self, sequence, samples, rate=16000, start=None):
        start = float(sequence) if start is None else start
        relative = f'tracks/microphone/{sequence:08}.wav'
        path = self.session/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
            out.writeframes(np.asarray(samples, dtype='<i2').tobytes())
        return {'kind':'audio_chunk','track':'microphone','file':relative,'sequence':sequence,
                'sample_rate':rate,'sample_count':len(samples),'start_seconds':start,
                'end_seconds':start+len(samples)/rate,'sha256':file_digest(path)}

    def journal(self, rows, tail=b''):
        (self.session/'timeline.jsonl').write_bytes(('\n'.join(json.dumps(r) for r in rows)+'\n').encode()+tail)

    def test_partial_tail_is_ignored_but_missing_chunk_fails(self):
        row = self.chunk(0, np.zeros(16000))
        self.journal([row], b'{"kind":')
        self.assertEqual(len(worker.journal_snapshot(self.session)[0]['microphone']), 1)
        self.journal([row, {**row,'sequence':2}])
        with self.assertRaisesRegex(ValueError, 'sequence'):
            worker.journal_snapshot(self.session)
        self.journal([row], b'{broken}\n')
        with self.assertRaises(json.JSONDecodeError): worker.journal_snapshot(self.session)

    def test_source_hash_and_containment_are_enforced(self):
        row = self.chunk(0, np.zeros(16000))
        with self.assertRaisesRegex(ValueError, 'digest'):
            worker.materialize(self.session, [{**row,'sha256':'invalid'}], 0, 1, self.root/'window.wav')
        (self.root/'outside.wav').write_bytes((self.session/row['file']).read_bytes())
        with self.assertRaisesRegex(ValueError, 'leaves'):
            worker.materialize(self.session, [{**row,'file':'../outside.wav'}], 0, 1, self.root/'window.wav')

    def test_recovered_partial_tail_only_finalizes_with_bound_verification(self):
        row = self.chunk(0,np.zeros(16000))
        self.journal([row],b'{incomplete')
        write_json(self.session/'metadata.json',{'status':'recovered'})
        write_json(self.session/'recovery-verified.json',{'verified':True,'duration_seconds':1,
            'source_journal_sha256':file_digest(self.session/'timeline.jsonl')})
        self.assertTrue(worker.journal_snapshot(self.session)[1])
        self.journal([row],b'{changed')
        with self.assertRaisesRegex(ValueError,'verified journal'): worker.journal_snapshot(self.session)

    def test_contiguous_files_resample_as_one_span(self):
        rate = 48000
        values = np.rint(np.sin(np.arange(rate*2)*.1)*8000).astype('<i2')
        rows = [self.chunk(0, values[:rate],rate), self.chunk(1, values[rate:],rate)]
        target = self.root/'window.wav'
        report = worker.materialize(self.session, rows, 0, 2, target)
        with wave.open(str(target), 'rb') as wav:
            actual = np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2')
        expected = np.clip(np.rint(resample_poly(values.astype(np.float32)/32768,1,3)*32768),-32768,32767).astype('<i2')
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(report['uncovered_seconds'], 0)

    def test_gap_is_kept_as_silence_and_overlap_is_flagged(self):
        rows = [self.chunk(0,np.ones(16000)*1000,start=1),self.chunk(1,np.ones(16000)*2000,start=1.5)]
        report = worker.materialize(self.session,rows,0,3,self.root/'window.wav')
        self.assertEqual(report['uncovered_seconds'],1.5)
        self.assertEqual(report['overlap_seconds'],.5)
        self.assertIn('capture_gap',worker.review_flags('words',report,3))

    def test_short_retry_preserves_original_instead_of_choosing_longer(self):
        row = self.chunk(0,np.ones(15*16000)*1000)
        calls = []
        def infer(spec,path,config):
            calls.append(path)
            return {'status':'ok','text':'original' if len(calls)==1 else 'an alternative with several more words'}
        result = worker.evaluate_window({}, {}, self.session, [row],0,15,self.root/'window.wav',infer)
        self.assertEqual(result['text'],'original')
        self.assertTrue(result['alternative']['requires_review'])
        self.assertEqual(len(calls),3)
        self.assertEqual([(p['start_seconds'],p['end_seconds']) for p in result['alternative']['chunks']],[(0,10),(10,15)])

    def test_only_digital_silence_bypasses_inference(self):
        row = self.chunk(0,np.zeros(16000))
        def infer(*args): self.fail('All-zero PCM must not call a model')
        result = worker.evaluate_window({}, {}, self.session,[row],0,1,self.root/'zero.wav',infer)
        self.assertEqual(result['text'],'')
        self.assertTrue(result['audio']['digital_silence'])

    def test_quiet_nonzero_audio_preserves_recognition_and_flags_review(self):
        row = self.chunk(0, np.ones(16000) * 8)
        for text in ('nan', 'Mira'):
            with self.subTest(text=text):
                calls = []
                def infer(*args):
                    calls.append(args)
                    return {'status': 'ok', 'text': text}
                result = worker.evaluate_window({}, {}, self.session, [row], 0, 1,
                                                self.root / 'quiet.wav', infer)
                self.assertEqual(result['text'], text)
                self.assertEqual(len(calls), 1)
                self.assertIn('very_quiet_audio', result['quality_flags'])
                self.assertIsNone(result['alternative'])
        loud = self.chunk(1, np.ones(16000) * 1000)
        result = worker.evaluate_window({}, {}, self.session, [loud], 1, 2,
                                        self.root / 'audible.wav', infer)
        self.assertNotIn('very_quiet_audio', result['quality_flags'])

    def keep_gate(self, *args):
        return {'status': 'ok', 'skip_stt': False, 'decision': 'keep',
                'model_sha256': 'a' * 64, 'elapsed_seconds': .01,
                'raw_max_probability': .6, 'boosted_max_probability': .8}

    def test_repetition_recognizes_three_word_loop_but_allows_short_repeats(self):
        for phrase in ('जब', 'वो जब', 'वो जब था', 'a b c d e f g h'):
            with self.subTest(phrase=phrase):
                repeats = max(4, (12 + len(phrase.split()) - 1) // len(phrase.split()))
                self.assertIsNotNone(worker.repetition_details((' ' + phrase) * repeats))
        for text in ('yes yes yes', 'वो जब था वो जब था वो जब था', 'a b c d a b c d a b c d'):
            self.assertIsNone(worker.repetition_details(text))

    def test_trelis_loop_retries_once_at_five_seconds_and_preserves_every_source(self):
        loop = 'वो जब था ' * 5
        for duration in (10, 20):
            with self.subTest(duration=duration):
                row = self.chunk(0, np.ones(duration * 16000) * 1000, start=2)
                before = file_digest(self.session / row['file'])
                calls, gates = [], []
                def infer(spec, path, config):
                    calls.append(path)
                    return {'status': 'ok', 'text': loop if len(calls) == 1 else f'piece number {len(calls)}'}
                def gate(path, config):
                    gates.append(path)
                    return self.keep_gate()
                result = worker.evaluate_window({'id': 'trelis'}, {}, self.session, [row], 2, 2 + duration,
                                                self.root / 'loop.wav', infer, gate=gate)
                self.assertEqual(len(calls), 1 + duration // 5)
                self.assertEqual(len(gates), len(calls))
                self.assertEqual(result['original_result']['text'], loop)
                self.assertNotEqual(result['text'], loop)
                self.assertTrue(result['alternative']['promoted'])
                self.assertNotIn('repeated_phrase', result['quality_flags'])
                self.assertIn('needs_review', result['quality_flags'])
                pieces = result['alternative']['chunks']
                self.assertEqual([(p['start_seconds'], p['end_seconds']) for p in pieces],
                                 [(2 + i, 7 + i) for i in range(0, duration, 5)])
                for piece in pieces:
                    self.assertEqual(piece['audio_provenance']['source_chunks'][0]['sha256'], before)
                    self.assertEqual(piece['audio_provenance']['uncovered_seconds'], 0)
                    self.assertEqual(len(piece['audio_provenance']['audio_sha256']), 64)
                    self.assertEqual(piece['source_audio_sha256'], result['audio']['audio_sha256'])
                    self.assertEqual(piece['audio_provenance']['speech_gate']['model_sha256'], 'a' * 64)
                self.assertEqual(file_digest(self.session / row['file']), before)

    def test_five_second_loop_does_not_retry_and_ordinary_repetition_is_retained(self):
        row = self.chunk(0, np.ones(5 * 16000) * 1000)
        for text in ('yes yes yes', 'वो जब था ' * 5):
            calls = []
            def infer(*args):
                calls.append(args)
                return {'status': 'ok', 'text': text}
            result = worker.evaluate_window({'id': 'trelis'}, {}, self.session, [row], 0, 5,
                                            self.root / 'five.wav', infer, gate=self.keep_gate)
            self.assertEqual(result['text'], text)
            self.assertEqual(len(calls), 1)
            self.assertIsNone(result['alternative'])
            self.assertEqual('needs_review' in result['quality_flags'], len(text.split()) > 3)

    def test_failed_or_still_looping_retry_keeps_original_and_failed_piece_audio(self):
        loop = 'वो जब था ' * 5
        row = self.chunk(0, np.ones(10 * 16000) * 1000)
        for outcome in ('failed', 'exception', 'persistent'):
            calls = []
            def infer(*args):
                calls.append(args)
                if len(calls) == 1:
                    return {'status': 'ok', 'text': loop}
                if len(calls) == 2 and outcome == 'exception':
                    raise RuntimeError('synthetic model failure')
                if len(calls) == 2 and outcome == 'failed':
                    return {'status': 'failed', 'error': 'synthetic model failure'}
                return {'status': 'ok', 'text': loop if outcome == 'persistent' else 'speech recovered'}
            result = worker.evaluate_window({'id': 'trelis'}, {}, self.session, [row], 0, 10,
                                            self.root / 'failure.wav', infer, gate=self.keep_gate)
            self.assertEqual(len(calls), 3)
            self.assertEqual(result['text'], loop)
            self.assertFalse(result['alternative']['promoted'])
            self.assertIn('needs_review', result['quality_flags'])
            self.assertEqual(len(result['alternative']['chunks'][0]['audio_provenance']['audio_sha256']), 64)
            self.assertIn('repeated_phrase', result['quality_flags'])

    def test_conservative_gate_skip_retains_nonzero_audio_and_unavailable_keeps_words(self):
        row = self.chunk(0, np.ones(20 * 16000) * 8)
        before = file_digest(self.session / row['file'])
        def infer(*args):
            return {'status': 'ok', 'text': 'Mira'}
        def no_speech(*args):
            return {'status': 'ok', 'skip_stt': True, 'raw_max_probability': .01,
                    'boosted_max_probability': .01, 'model_sha256': 'a' * 64, 'elapsed_seconds': .02}
        with patch.object(worker, 'transcribe') as model:
            result = worker.evaluate_window({'id': 'trelis'}, {}, self.session, [row], 0, 20,
                                            self.root / 'nonzero.wav', model, gate=no_speech)
            model.assert_not_called()
        self.assertEqual(result['text'], '')
        self.assertFalse(result['audio']['digital_silence'])
        self.assertEqual(result['audio']['speech_gate']['model_sha256'], 'a' * 64)
        def unavailable(*args):
            raise RuntimeError('synthetic unavailable classifier')
        result = worker.evaluate_window({'id': 'trelis'}, {}, self.session, [row], 0, 20,
                                        self.root / 'nonzero.wav', infer, gate=unavailable)
        self.assertEqual(result['text'], 'Mira')
        self.assertIn('speech_gate_unavailable', result['quality_flags'])
        self.assertEqual(file_digest(self.session / row['file']), before)

    def test_all_empty_or_skipped_retry_does_not_erase_speech_containing_original(self):
        loop = 'वो जब था ' * 5
        row = self.chunk(0, np.ones(10 * 16000) * 1000)
        for skip_retry in (False, True):
            calls, gates = [], []
            def infer(*args):
                calls.append(args)
                return {'status': 'ok', 'text': loop if len(calls) == 1 else ''}
            def gate(*args):
                gates.append(args)
                report = self.keep_gate()
                if len(gates) > 1 and skip_retry:
                    report.update(skip_stt=True, decision='skip', raw_max_probability=.01,
                                  boosted_max_probability=.01)
                return report
            result = worker.evaluate_window({'id': 'trelis'}, {}, self.session, [row], 0, 10,
                                            self.root / 'empty-retry.wav', infer, gate=gate)
            self.assertEqual(len(gates), 3)
            self.assertEqual(len(calls), 1 if skip_retry else 3)
            self.assertEqual(result['text'], loop)
            self.assertEqual(result['alternative']['text'], '')
            self.assertFalse(result['alternative']['promoted'])
            self.assertIn('needs_review', result['quality_flags'])
            self.assertIn('repeated_phrase', result['quality_flags'])
            self.assertEqual(len(result['alternative']['chunks']), 2)

    def test_trelis_retry_checkpoint_keeps_drafts_provenance_and_cursor_on_resume(self):
        row = self.chunk(0, np.ones(10 * 16000) * 1000)
        self.journal([row, {'kind': 'capture_stopped', 'at_seconds': 10}, {'kind': 'capture_finalized'}])
        registry = self.root / 'models.json'
        write_json(registry, {'models': [{'id': 'trelis', 'decoding': {}}]})
        config = self.root / 'runtime.json'
        gate_settings = {'model_path': 'synthetic', 'model_sha256': 'a' * 64, 'threshold': .15}
        settings = {'registry_path': str(registry), 'models': {'trelis': {}}, 'speech_gate': gate_settings}
        write_json(config, settings)
        job = self.root / 'retry-job'; job.mkdir()
        request = job / 'request.json'
        write_json(request, {'job_id': 'retry', 'session_dir': str(self.session),
                             'profile': 'trelis-10', 'language_mode': 'hinglish'})
        calls = []
        def infer(spec, path, runtime):
            self.assertEqual(runtime['speech_gate'], gate_settings)
            calls.append(path)
            return {'status': 'ok', 'text': 'वो जब था ' * 5 if len(calls) == 1 else f'recovered piece {len(calls)}'}
        original = worker.evaluate_window
        def evaluate(*args):
            return original(*args, infer=infer, gate=self.keep_gate)
        with patch.object(socket.socket, 'connect', socket.socket.connect), patch.object(socket.socket, 'connect_ex', socket.socket.connect_ex), patch.object(socket, 'create_connection', socket.create_connection), patch.object(worker, 'parent_alive', return_value=True), patch.object(worker, 'evaluate_window', side_effect=evaluate):
            first = worker.run(config, request)
            second = worker.run(config, request)
        self.assertEqual(first['state'], 'complete')
        self.assertEqual(len(calls), 3)
        self.assertEqual(first['segments'], second['segments'])
        self.assertEqual(second['cursors']['microphone'], 10)
        self.assertEqual(second['segments'][0]['recognition_original']['text'], 'वो जब था ' * 5)
        self.assertTrue(second['segments'][0]['alternative']['promoted'])
        self.assertEqual(second['identity']['speech_gate'], gate_settings)
        write_json(config, {**settings, 'speech_gate': {**gate_settings, 'threshold': .1}})
        with self.assertRaisesRegex(ValueError, 'Cannot resume changed'):
            worker.run(config, request)

    def test_checkpoint_resume_does_not_duplicate_and_identity_changes_fail(self):
        row = self.chunk(0,np.zeros(16000))
        self.journal([row,{'kind':'capture_stopped','at_seconds':1},{'kind':'capture_finalized'}])
        registry = self.root/'models.json'; write_json(registry,{'models':[{'id':'apex','decoding':{}}]})
        config = self.root/'runtime.json'; write_json(config,{'registry_path':str(registry),'models':{'apex':{}}})
        job = self.root/'job'; job.mkdir()
        request = job/'request.json'
        req = {'job_id':'synthetic','session_dir':str(self.session),'profile':'apex-15','language_mode':'english'}
        write_json(request,req)
        with patch.object(socket.socket,'connect',socket.socket.connect), patch.object(socket.socket,'connect_ex',socket.socket.connect_ex), patch.object(socket,'create_connection',socket.create_connection), patch.object(worker,'parent_alive',return_value=True):
            first = worker.run(config,request)
            second = worker.run(config,request)
        self.assertEqual(first['state'],'complete')
        self.assertEqual(first['segments'],second['segments'])
        self.assertEqual(len(second['segments']),1)
        write_json(request,{**req,'language_mode':'hinglish'})
        with self.assertRaisesRegex(ValueError,'Cannot resume changed'): worker.run(config,request)

    def test_process_lock_is_exclusive_and_released(self):
        path = self.root/'worker.lock'
        with worker.job_lock(path):
            with self.assertRaises(OSError):
                with worker.job_lock(path): self.fail('Second writer entered')
        with worker.job_lock(path): pass

    def test_gpu_warmup_emits_no_transcript_and_failure_keeps_audio(self):
        row = self.chunk(0, np.zeros(16000))
        self.journal([row, {'kind':'capture_stopped','at_seconds':1}, {'kind':'capture_finalized'}])
        registry = self.root/'models.json'
        write_json(registry, {'models':[{'id':'trelis','decoding':{}}]})
        config = self.root/'runtime.json'
        write_json(config, {'registry_path':str(registry),'models':{'trelis':{'backend':'openvino'}}})
        before = file_digest(self.session/row['file'])
        for failed in (False, True):
            job = self.root/str(failed); job.mkdir()
            request = job/'request.json'
            write_json(request, {'job_id':'warmup','session_dir':str(self.session),'profile':'trelis-20','language_mode':'hinglish'})
            def load(spec, runtime):
                checkpoint = read_json(job/'status.json')
                self.assertEqual(checkpoint['phase'], 'loading_model')
                self.assertEqual(checkpoint['segments'], [])
                return {'status':'failed' if failed else 'ok','text':'','segments':[], 'error':'synthetic failure' if failed else None}
            with patch.object(socket.socket,'connect',socket.socket.connect), patch.object(socket.socket,'connect_ex',socket.socket.connect_ex), patch.object(socket,'create_connection',socket.create_connection), patch.object(worker,'parent_alive',return_value=True), patch.object(worker,'warmup',side_effect=load) as warm:
                result = worker.run(config, request)
            warm.assert_called_once()
            self.assertEqual(result['state'], 'failed' if failed else 'complete')
            self.assertEqual(len(result['segments']), 0 if failed else 1)
            self.assertEqual(file_digest(self.session/row['file']), before)


if __name__ == '__main__': unittest.main()
