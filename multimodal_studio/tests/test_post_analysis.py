import tempfile
import unittest
from pathlib import Path
from post_analysis import PostExperimentTab
from analysis_core import find_streams, analyze

class PostAnalysisTests(unittest.TestCase):
    def test_threshold_flags(self):
        report={'reference':{'start_from_marker':True,'end_from_marker':True},
                'alignment':[{'stream':'Camera 1','first_vs_session_start_ms':750,'last_vs_session_stop_ms':-900}],
                'streams':[{'stream':'Camera 1','interval_jitter_mad_ms':15,'large_gap_count':2,'nonpositive_intervals':0,'warnings':[]}],
                'event_offsets':[]}
        lim=dict(start_ms=500,stop_ms=500,jitter_ms=10,gaps=0,nonpositive=0,event_offset_ms=20,event_drift_ppm=100)
        f=PostExperimentTab._evaluate(report,lim)
        self.assertTrue(any(x['metric']=='Late start' for x in f))
        self.assertTrue(any(x['metric']=='Early stop' for x in f))
        self.assertTrue(any(x['metric']=='Timestamp jitter MAD' for x in f))
        self.assertTrue(any(x['metric']=='Uncalibrated offset' for x in f))

if __name__=='__main__':unittest.main()
