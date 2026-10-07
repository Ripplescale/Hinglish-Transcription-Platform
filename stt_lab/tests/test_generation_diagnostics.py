"""Generation diagnostics must distinguish prompt length, EOS and exhaustion."""
import unittest

import numpy as np
from sttbench.runtime.api import _GenerationObserver


class GenerationDiagnosticsTests(unittest.TestCase):
    def test_cap_counts_generated_tokens_without_prompt_and_never_stops(self):
        observer = _GenerationObserver(3, 99)
        for ids in ([10, 11, 12, 1], [10, 11, 12, 1, 2], [10, 11, 12, 1, 2, 3]):
            sequence = np.array([ids])
            before = sequence.copy()
            self.assertFalse(observer(sequence, None))
            np.testing.assert_array_equal(sequence, before)
        report = observer.diagnostics('जो ' * 40)
        self.assertEqual(report['generated_token_count'], 3)
        self.assertTrue(report['token_cap_reached'])
        self.assertFalse(report['ended_with_eos'])
        self.assertGreater(report['reference_text_zlib_ratio'], 2.4)
        self.assertIsNone(report['no_speech_probability'])

    def test_natural_eos_at_the_limit_is_not_exhaustion(self):
        observer = _GenerationObserver(2, [98, 99])
        observer(np.array([[10, 1]]), None)
        observer(np.array([[10, 1, 98]]), None)
        self.assertTrue(observer.ended_with_eos)
        self.assertFalse(observer.token_cap_reached)

    def test_new_internal_generate_preserves_prior_cap_evidence(self):
        observer = _GenerationObserver(2, 99)
        observer(np.array([[10, 1]]), None)
        observer(np.array([[10, 1, 2]]), None)
        observer(np.array([[10, 99]]), None)
        self.assertEqual(observer.generation_calls_observed, 2)
        self.assertTrue(observer.token_cap_reached)
        self.assertTrue(observer.ended_with_eos)

    def test_missing_observation_is_unavailable_not_fabricated_probability(self):
        report = _GenerationObserver(440, 99).diagnostics('')
        self.assertIsNone(report['generated_token_count'])
        self.assertIsNone(report['ended_with_eos'])
        self.assertFalse(report['token_cap_reached'])


if __name__ == '__main__':
    unittest.main()
