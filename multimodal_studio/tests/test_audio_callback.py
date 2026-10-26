import unittest, sys, types
import numpy as np
# Stubs are ONLY for simulation; real capture still requires the project dependencies.
if 'sounddevice' not in sys.modules:
    sys.modules['sounddevice']=types.ModuleType('sounddevice')
if 'pylsl' not in sys.modules:
    pylsl=types.ModuleType('pylsl');pylsl.local_clock=lambda:100.0
    pylsl.StreamInfo=type('StreamInfo',(),{});pylsl.StreamOutlet=type('StreamOutlet',(),{})
    pylsl.StreamInlet=type('StreamInlet',(),{})
    pylsl.resolve_streams=lambda *a,**k: []
    sys.modules['pylsl']=pylsl
from devices.audio import AudioRecorder

class Bus:
    def __init__(self): self.entries=[]
    def emit(self,*args):self.entries.append(args)

class Timing:
    currentTime=10.0
    inputBufferAdcTime=9.99

class Status:
    input_overflow=False
    def __bool__(self):return False

class CallbackTest(unittest.TestCase):
    def test_rate_samples_timing_queue_and_silence(self):
        r=AudioRecorder('Microphone',Bus())
        r._rate=48000
        import queue
        r.q=queue.Queue()
        for i in range(500):
            r._callback(np.zeros((480,1),dtype=np.int16),480,Timing(),Status())
        self.assertEqual(r.total_frames_received,240000)
        self.assertEqual(r.q.qsize(),500)
        self.assertEqual(r.level,0)
        snap=r.snapshot_preview()
        self.assertGreater(len(snap),1400)
        self.assertLess(len(snap),3000)
        self.assertEqual(snap[0][1],0.0)
    def test_waveform_nonzero_and_timestamp_spacing(self):
        r=AudioRecorder('Ultramic',Bus());r._rate=384000
        import queue
        r.q=queue.Queue()
        sample=np.ones((3840,1),dtype=np.int16)*10000
        r._callback(sample,3840,Timing(),Status())
        snap=r.snapshot_preview()
        self.assertTrue(all(abs(v-10000/32768)<1e-5 for t,v in snap))
        self.assertGreater(r.level,.3)
        self.assertAlmostEqual(snap[1][0]-snap[0][0],1/300,delta=1e-7)

if __name__=='__main__':unittest.main()
