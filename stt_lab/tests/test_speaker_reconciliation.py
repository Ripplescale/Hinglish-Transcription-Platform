import copy
import unittest
from sttbench.speaker_reconciliation import reconcile_speakers


def turn(start, end, label):
    return {'start_seconds': start, 'end_seconds': end, 'model_speaker_label': label}


def window(start=0, end=20, identifier='window-one'):
    return {'id': identifier, 'start_seconds': start, 'end_seconds': end, 'text': 'Original words remain unchanged'}


class SpeakerReconciliationTests(unittest.TestCase):
    def test_multispeaker_window_never_assigns_all_words_to_dominant_speaker(self):
        segments = [window()]
        original = copy.deepcopy(segments)
        result = reconcile_speakers('session', [turn(0, 18, 'A'), turn(18, 20, 'B')], segments)
        assignment = result['segment_assignments']['window-one']
        self.assertIsNone(assignment['speaker_id'])
        self.assertEqual(len(assignment['speaker_candidates']), 2)
        self.assertFalse(assignment['has_overlap'])
        self.assertEqual(segments, original)

    def test_overlap_preserved_but_window_edges_do_not_count_as_overlap(self):
        result = reconcile_speakers('session', [turn(0, 12, 'A'), turn(10, 20, 'B')], [window(), window(20, 30, 'silence')])
        self.assertTrue(result['segment_assignments']['window-one']['has_overlap'])
        self.assertEqual(result['segment_assignments']['silence']['speaker_candidates'], [])

    def test_permuted_model_labels_keep_stable_ids_and_manual_names(self):
        prior = reconcile_speakers('session', [turn(0, 10, 'A'), turn(10, 20, 'B')], [window()])
        prior['speakers'][0]['display_name'] = 'Vivek'
        snapshot = copy.deepcopy(prior)
        result = reconcile_speakers('session', [turn(.1, 10, 'X'), turn(10, 19.9, 'Y')], [window()], prior)
        self.assertEqual(result['turns'][0]['speaker_id'], prior['turns'][0]['speaker_id'])
        self.assertEqual(result['speakers'][0]['display_name'], 'Vivek')
        self.assertEqual(prior, snapshot)

    def test_ambiguous_split_does_not_silently_reassign_person_name(self):
        prior = reconcile_speakers('session', [turn(0, 20, 'A')], [window()])
        prior['speakers'][0]['display_name'] = 'Mira'
        result = reconcile_speakers('session', [turn(0, 10, 'X'), turn(10, 20, 'Y')], [window()], prior)
        self.assertFalse(result['speakers'][0]['active'])
        self.assertEqual(result['speakers'][0]['display_name'], 'Mira')
        self.assertTrue(result['reconciliation']['requires_review'])
        self.assertNotIn(prior['speakers'][0]['id'], result['segment_assignments']['window-one']['speaker_candidates'])

    def test_cross_recording_names_are_rejected(self):
        previous = reconcile_speakers('other-session', [turn(0, 20, 'A')], [window()])
        with self.assertRaisesRegex(ValueError, 'another recording'):
            reconcile_speakers('session', [turn(0, 20, 'A')], [window()], previous)

    def test_invalid_and_duplicate_windows_fail_closed(self):
        with self.assertRaises(ValueError):
            reconcile_speakers('session', [turn(float('nan'), 2, 'A')], [window()])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            reconcile_speakers('session', [turn(0, 20, 'A')], [window(), window()])


if __name__ == '__main__':
    unittest.main()
