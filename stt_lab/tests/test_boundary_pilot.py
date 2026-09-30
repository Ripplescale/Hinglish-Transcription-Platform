import importlib.util
from pathlib import Path
import sys
import unittest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
import boundary_pilot as pilot


class BoundaryPilotTests(unittest.TestCase):
    def test_phase_preserves_full_frames_and_duration_cap(self):
        for seconds in (0.7,5,15.2,25.0373125,60,60.2):
            frames=round(seconds*16000)
            for size in (15,20,30):
                cuts=pilot.shifted_windows(frames,size)
                self.assertEqual(cuts[0][0],0)
                self.assertEqual(cuts[-1][1],frames)
                self.assertEqual(sum(b-a for a,b in cuts),frames)
                self.assertTrue(all(0<b-a<=size*16000 for a,b in cuts))
                self.assertTrue(all(cuts[i][1]==cuts[i+1][0] for i in range(len(cuts)-1)))

    def test_phase_moves_internal_cuts(self):
        self.assertEqual(pilot.shifted_windows(60*16000,15),[(0,80000),(80000,320000),(320000,560000),(560000,800000),(800000,960000)])

    def test_selection_excludes_paced_and_prior_audio(self):
        exclusions=[(30,1230),(1244,1269),(1344,1405)]
        start=pilot.fresh_start(1478,exclusions)
        self.assertEqual(start,1280)
        self.assertFalse(any(start<end+2 and start+60>begin-2 for begin,end in exclusions))

    def test_no_available_fresh_minute_fails(self):
        with self.assertRaises(ValueError):pilot.fresh_start(100,[(0,100)])

    def test_bad_phase_rejected(self):
        for value in (-1,0,15,30):
            with self.assertRaises(ValueError):pilot.shifted_windows(16000,15,value)


if __name__=='__main__':unittest.main()
