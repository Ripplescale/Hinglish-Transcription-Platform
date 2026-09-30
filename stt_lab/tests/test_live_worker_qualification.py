import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('qualify_live_worker',Path(__file__).parents[1]/'tools/qualify_live_worker.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class PacedWorkerTests(unittest.TestCase):
    def test_future_audio_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'future'):
            module.commit(Path('never-created'),bytes(32000),0,.99)

    def test_routing_preserves_one_track_then_explicit_simultaneous_load(self):
        source=b'\x01\x00'*(305*16000)
        self.assertTrue(any(module.route(source,0,'microphone')))
        self.assertFalse(any(module.route(source,0,'system')))
        self.assertFalse(any(module.route(source,20,'microphone')))
        self.assertTrue(any(module.route(source,20,'system')))
        self.assertEqual(module.route(source,260,'microphone'),module.route(source,260,'system'))

    def test_commit_writes_synced_wavs_before_complete_journal_rows(self):
        with tempfile.TemporaryDirectory(prefix='paced-synthetic-',dir=Path(__file__).parents[1]) as root:
            session=Path(root)
            module.commit(session,b'\x01\x00'*16000,0,1.01)
            rows=[module.json.loads(line) for line in (session/'timeline.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows),2)
            for row in rows:
                self.assertEqual(module.file_digest(session/row['file']),row['sha256'])
            self.assertTrue(any(module.pcm(session/rows[0]['file'])))
            self.assertFalse(any(module.pcm(session/rows[1]['file'])))

    def test_latency_does_not_subtract_window_start(self):
        rows=[{'digital_silence':False,'ready_elapsed_seconds':28,'end_seconds':20,'start_seconds':0,
          'adapter_total_seconds':7,'backlog_seconds':8}]
        result=module.metrics(rows,30,'complete',25,30,[{'elapsed_seconds':1.01,'audio_end_seconds':1}])
        self.assertEqual(result['window_end_to_ready_p95_seconds'],8)
        self.assertEqual(result['adapter_realtime_factor'],.35)
        self.assertFalse(result['future_audio_exposed'])
        self.assertEqual(result['post_stop_drain_seconds'],5)
        stopped=module.metrics(rows,30,'stopped',25,30,[])
        self.assertIsNone(stopped['post_stop_drain_seconds'])
        self.assertEqual(stopped['stop_acknowledgement_seconds'],5)

if __name__=='__main__':unittest.main()
