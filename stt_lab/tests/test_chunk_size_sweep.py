import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sttbench.manifest import write_json

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
spec=importlib.util.spec_from_file_location('chunk_sweep_test',Path(__file__).resolve().parents[1]/'tools/chunk_size_sweep.py')
sweep=importlib.util.module_from_spec(spec);spec.loader.exec_module(sweep)


class ChunkSizeTests(unittest.TestCase):
    def test_all_samples_covered_once_with_no_oversized_chunk(self):
        for count in (1600,16000,401600,480000,960000):
            for size in sweep.SIZES:
                cuts=sweep.windows(count,size)
                self.assertEqual((cuts[0][0],cuts[-1][1]),(0,count))
                self.assertEqual(sum(end-start for start,end in cuts),count)
                self.assertTrue(all(0<end-start<=size*16000 for start,end in cuts))
                self.assertTrue(all(cuts[i][1]==cuts[i+1][0] for i in range(len(cuts)-1)))

    def test_tiny_tail_is_preserved_and_rebalanced(self):
        self.assertEqual(sweep.windows(401600,5)[-2:],[(320000,360800),(360800,401600)])

    def test_empty_or_negative_window_rejected(self):
        for frames,size in ((0,5),(100,0),(100,-1)):
            with self.assertRaises(ValueError):sweep.windows(frames,size)

    def test_json_save_retries_windows_lock_without_erasing_previous_result(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'result.json';path.write_text('{"old": true}')
            replace=Path.replace;calls=[]
            def locked_once(src,dst):
                calls.append(True)
                if len(calls)==1:
                    self.assertEqual(json.loads(path.read_text()),{'old':True})
                    raise PermissionError('temporary Windows file lock')
                return replace(src,dst)
            with patch.object(Path,'replace',locked_once),patch('sttbench.manifest.time.sleep') as sleep:
                write_json(path,{'new':True})
            self.assertEqual(json.loads(path.read_text()),{'new':True})
            self.assertEqual(len(calls),2);sleep.assert_called_once()

    def test_persistent_lock_fails_and_preserves_previous_result(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'result.json';path.write_text('{"old": true}')
            with patch.object(Path,'replace',side_effect=PermissionError('locked')) as replace,patch('sttbench.manifest.time.sleep'):
                with self.assertRaises(PermissionError):write_json(path,{'new':True})
            self.assertEqual(replace.call_count,12)
            self.assertEqual(json.loads(path.read_text()),{'old':True})


if __name__=='__main__':unittest.main()
