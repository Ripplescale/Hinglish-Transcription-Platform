"""Timing invariants tested using a deterministic clock, not sleeping or ASR."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('sustained_replay', Path(__file__).resolve().parents[1] / 'tools/sustained_replay.py')
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class Clock:
    def __init__(self): self.now = 0
    def time(self): return self.now
    def sleep(self, seconds): self.now += seconds


class ReplayTests(unittest.TestCase):
    def chunks(self, n=4):
        return [{'id': str(i), 'start_seconds': i*10, 'end_seconds': (i+1)*10} for i in range(n)]

    def measure(self, durations, mode='paced', fail=None, abort=60):
        clock = Clock()
        def invoke(item):
            index = int(item['id'])
            self.assertGreaterEqual(clock.time(), item['end_seconds'] if mode == 'paced' else 0)
            clock.sleep(durations[index])
            return {'status': 'failed' if index == fail else 'ok'}
        return replay.measure_chunks(self.chunks(len(durations)), invoke, mode, clock.time, clock.sleep,
                                     abort_backlog_seconds=abort)

    def test_fast_worker_waits_for_source_without_extra_sleep(self):
        rows, reason = self.measure([2, 2, 2])
        self.assertIsNone(reason)
        self.assertEqual([r['started_seconds'] for r in rows], [10, 20, 30])
        self.assertEqual([r['source_end_delay_seconds'] for r in rows], [2, 2, 2])
        self.assertEqual([r['oldest_audio_delay_seconds'] for r in rows], [12, 12, 12])

    def test_slow_worker_preserves_growing_backlog(self):
        rows, _ = self.measure([15, 15, 15, 15])
        self.assertEqual([r['queue_delay_seconds'] for r in rows], [0, 5, 10, 15])
        self.assertEqual([r['source_end_delay_seconds'] for r in rows], [15, 20, 25, 30])

    def test_cold_load_not_silently_excluded(self):
        rows, _ = self.measure([22, 2, 2, 2])
        summary = replay.summary(rows, 'paced', 4, 40)
        self.assertEqual(summary['cold_first_service_seconds'], 22)
        self.assertEqual(summary['after_first_service_p95_seconds'], 2)
        self.assertEqual(summary['oldest_audio_delay_p95_seconds'], 32)
        self.assertEqual(summary['oldest_audio_deadline_misses'], 3)

    def test_failure_is_not_a_good_low_latency_update(self):
        rows, reason = self.measure([2, 1, 2], fail=1)
        summary = replay.summary(rows, 'paced', 3, 30)
        self.assertEqual(reason, 'worker_failure')
        self.assertFalse(summary['complete'])
        self.assertEqual(summary['failed_chunks'], 1)
        self.assertEqual(summary['oldest_audio_deadline_misses'], 1)

    def test_capacity_mode_has_no_latency_claim(self):
        rows, _ = self.measure([12, 12], 'capacity')
        summary = replay.summary(rows, 'capacity', 2, 20)
        self.assertNotIn('source_end_delay_p95_seconds', summary)
        self.assertNotIn('queue_delay_seconds', rows[0])
        self.assertEqual(rows[0]['started_seconds'], 0)

    def test_last_chunk_failure_is_not_complete(self):
        rows, _ = self.measure([2, 1], fail=1)
        result = replay.summary(rows, 'paced', 2, 20)
        self.assertTrue(result['all_chunks_attempted'])
        self.assertFalse(result['complete'])

    def test_abort_is_incomplete_run(self):
        rows, reason = self.measure([25, 25, 25], abort=30)
        self.assertEqual(reason, 'source_end_delay_exceeded_abort_limit')
        self.assertEqual(len(rows), 2)
        self.assertFalse(replay.summary(rows, 'paced', 3, 30)['complete'])

    def test_nearest_rank(self):
        self.assertEqual(replay.percentile(list(range(1,101))), 95)
        self.assertIsNone(replay.percentile([]))

    def test_deadline_budget_aborts_only_after_planned_target_impossible(self):
        clock = Clock()
        def invoke(item):
            clock.sleep(6)
            return {'status': 'ok'}
        rows, reason = replay.measure_chunks(self.chunks(40), invoke, clock=clock.time, sleep=clock.sleep,
                                            deadline_miss_budget=2)
        self.assertEqual(len(rows), 3)
        self.assertEqual(reason, 'planned_interval_cannot_meet_95_percent_oldest_audio_deadline')
        self.assertEqual([r['oldest_audio_delay_seconds'] for r in rows], [16,16,16])


if __name__ == '__main__':
    unittest.main()
