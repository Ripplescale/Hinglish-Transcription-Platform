"""Synthetic evidence tests for versioned correction intake and fixed panels."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sttbench.manifest import file_digest

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location('chunk_corrections_subject', TOOLS / 'score_chunk_corrections.py')
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class ChunkCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='stt-correction-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'study'
        self.root.mkdir()
        self.source = self.root.parent / 'review.json'
        samples = []
        for sid, old, lang in [('old', 'ki red blue', 'hi'), ('english', 'old words', 'en'),
                               ('native', None, 'hi'), ('omission', None, 'en')]:
            audio = self.root / f'{sid}.wav'
            audio.write_bytes(f'synthetic {sid}'.encode())
            ah = file_digest(audio)
            samples.append({'id': sid, 'title': sid, 'call_title': 'Synthetic',
                            'source_start_seconds': 0, 'duration_seconds': 30,
                            'audio': audio.name, 'audio_sha256': ah,
                            'reference_status': 'user_reviewed' if old else 'pending',
                            'reference_roman': old,
                            'reference_scope_note': 'Boundary assumption' if sid == 'old' else '',
                            'trelis_decoding': {'language': lang},
                            'partitions': {str(size): [{'id': f'{sid}:{size}:1', 'audio': audio.name,
                                                        'audio_sha256': ah}] for size in (15, 30)}})
        self.manifest = {'sizes': [15, 30], 'samples': samples}
        save(self.root / 'manifest.json', self.manifest)
        save(self.root / 'summary.json', {'historical': True})
        self.results = {}
        for model in subject.MODELS:
            result = {'state': 'complete', 'model_id': model, 'model_spec': {}, 'runtime_config': {},
                      'network_attempts': [], 'manifest_sha256': file_digest(self.root / 'manifest.json'), 'chunks': {}}
            for s in samples:
                for size, parts in s['partitions'].items():
                    text = {'old': 'ki red blue', 'english': 'ninety five paisa',
                            'native': 'यह text', 'omission': 'red blue green'}[s['id']]
                    if model == 'trelis' and s['id'] == 'omission' and size == '30':
                        text = 'नीला'
                    result['chunks'][parts[0]['id']] = {'status': 'ok', 'text': text,
                                                       'audio_sha256': parts[0]['audio_sha256']}
            self.results[model] = result
            save(self.root / 'results' / f'{model}.json', result)
        self.review = {'kind': 'chunk_size_human_review', 'version': 1, 'reviews': {}}
        for sid, ref in [('old', ''), ('english', 'ninety paisa'), ('native', 'यह text'),
                         ('omission', 'red blue green')]:
            self.review['reviews'][sid] = {'reference': ref, 'notes': '<script>untrusted</script>',
                                          'reviewed': bool(ref), 'reference_provenance': None,
                                          'draft_checks': {'apex:15': 'looks_complete', 'trelis:15': 'looks_complete'} if ref else {}}
        self.bind()

    def bind(self):
        self.review['binding'] = {
            'manifest_sha256': file_digest(self.root / 'manifest.json'),
            'summary_sha256': file_digest(self.root / 'summary.json'),
            'audio_sha256': {s['id']: s['audio_sha256'] for s in self.manifest['samples']},
            'model_results_sha256': {m: file_digest(self.root / 'results' / f'{m}.json') for m in subject.MODELS}}
        self.save_review()

    def save_review(self):
        save(self.source, self.review)

    def test_merge_retains_blank_prior_and_preserves_raw_revision(self):
        out = subject.build_analysis(self.root, self.source)
        samples = {s['id']: s for s in out['samples']}
        self.assertEqual(samples['old']['reference'], 'ki red blue')
        self.assertEqual(samples['old']['reference_source'], 'prior_checked_reference')
        self.assertEqual(samples['old']['reference_scope_note'], 'Boundary assumption')
        self.assertEqual(samples['english']['reference'], 'ninety paisa')
        self.assertEqual(samples['english']['previous_reference'], 'old words')
        self.assertEqual(samples['english']['notes'], '<script>untrusted</script>')
        self.assertEqual(samples['english']['draft_checks'], self.review['reviews']['english']['draft_checks'])
        self.assertNotIn('apex:30', samples['english']['draft_checks'])

    def test_fixed_panels_keep_script_variant_bad_prediction_and_sensitivity(self):
        out = subject.build_analysis(self.root, self.source)
        self.assertEqual(out['models']['apex']['sample_ids'], ['old', 'english', 'omission'])
        self.assertEqual(out['models']['trelis']['sample_ids'], ['english', 'native', 'omission'])
        self.assertEqual(out['models']['trelis']['script_stable_sample_ids'], ['english', 'native'])
        self.assertEqual(out['models']['apex']['boundary_certain_sample_ids'], ['english', 'omission'])
        rows = out['models']['trelis']['rows']
        self.assertEqual([r['aggregate']['reference_words'] for r in rows], [7, 7])
        self.assertEqual([r['script_stable_aggregate']['reference_words'] for r in rows], [4, 4])
        omission = next(s for s in out['samples'] if s['id'] == 'omission')['drafts']['trelis']['30']
        self.assertEqual(omission['text'], 'नीला')
        self.assertTrue(omission['script_surface_warning'])
        self.assertEqual(omission['metrics']['deletions'], 2)
        native = next(s for s in out['samples'] if s['id'] == 'native')
        self.assertIsNone(native['drafts']['apex']['15']['metrics'])
        self.assertFalse(out['trelis_romanization'])
        self.assertFalse(out['release_qualified'])
        self.assertFalse(out['held_out'])

    def test_unreviewed_draft_does_not_replace_gold(self):
        self.review['reviews']['old']['reference'] = 'unchecked change'
        self.save_review()
        sample = subject.build_analysis(self.root, self.source)['samples'][0]
        self.assertEqual(sample['reference'], 'ki red blue')
        self.assertEqual(sample['unreviewed_reference_draft'], 'unchecked change')

    def test_invalid_review_rejected(self):
        original = copy.deepcopy(self.review)
        for mutation in [lambda r: r['binding'].update(summary_sha256='wrong'),
                         lambda r: r['reviews'].update(unknown=r['reviews']['old']),
                         lambda r: r['reviews']['english'].update(reference=''),
                         lambda r: r['reviews']['english'].update(reviewed='true'),
                         lambda r: r['reviews']['english']['draft_checks'].update({'apex:45': 'looks_complete'}),
                         lambda r: r['reviews']['english']['draft_checks'].update({'apex:15': 'approved'})]:
            self.review = copy.deepcopy(original)
            mutation(self.review)
            self.save_review()
            with self.assertRaises(ValueError):
                subject.build_analysis(self.root, self.source)

    def test_audio_mutation_rejected(self):
        (self.root / 'english.wav').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            subject.build_analysis(self.root, self.source)

    def test_failed_prediction_rejected_even_with_matching_review_hash(self):
        self.results['trelis']['chunks']['omission:30:1']['status'] = 'error'
        save(self.root / 'results/trelis.json', self.results['trelis'])
        self.bind()
        with self.assertRaises(ValueError):
            subject.build_analysis(self.root, self.source)

    def test_intake_copies_exact_source_preserves_history_and_refuses_overwrite(self):
        paths = [self.root / 'manifest.json', self.root / 'summary.json', self.source,
                 *[self.root / 'results' / f'{m}.json' for m in subject.MODELS]]
        before = {p: file_digest(p) for p in paths}
        output = self.root / 'reviewed-new'
        def stub(root, target, analysis):
            target.write_text('synthetic render', encoding='utf-8')
        with patch.dict(sys.modules, {'render_chunk_corrections': SimpleNamespace(render=stub)}):
            subject.ingest(self.root, self.source, output)
            with self.assertRaises(ValueError):
                subject.ingest(self.root, self.source, output)
        self.assertEqual(before, {p: file_digest(p) for p in paths})
        self.assertEqual(file_digest(output / 'source-corrections.json'), before[self.source])
        self.assertTrue((output / 'effective-references.json').exists())
        with self.assertRaises(ValueError):
            subject.ingest(self.root, self.source, self.root.parent / 'escape')


if __name__ == '__main__':
    unittest.main()
