import sys
import time
import types
import unittest
import numpy as np
from unittest.mock import patch
from devices.camera_preview import discover_cameras, CameraPreview

class DummyCap:
    def __init__(self, index, backend):
        self.index=index; self.released=False
    def isOpened(self): return self.index==1
    def read(self): return (True,np.zeros((50,80,3),dtype=np.uint8))
    def release(self): self.released=True

class Bus:
    def __init__(self): self.items=[]
    def emit(self,*args): self.items.append(args)

class Tests(unittest.TestCase):
    def test_discovery_with_mock_capture(self):
        fake=types.SimpleNamespace(CAP_ANY=0,CAP_DSHOW=1,VideoCapture=DummyCap)
        with patch.dict(sys.modules, {'cv2':fake}):
            self.assertEqual(discover_cameras(3),[(1,'Camera 1 · 80×50 (Default)')])
    def test_capture_and_stop(self):
        fake=types.SimpleNamespace(CAP_ANY=0,CAP_DSHOW=1,VideoCapture=DummyCap,COLOR_BGR2RGB=1,cvtColor=lambda arr,code:arr.copy())
        bus=Bus(); recorder=CameraPreview(2,bus)
        with patch.dict(sys.modules, {'cv2':fake}):
            recorder.start(1)
            for _ in range(100):
                frame,stamp,err=recorder.snapshot()
                if frame is not None:break
                time.sleep(.005)
            self.assertIsNotNone(frame)
            self.assertEqual(frame.shape,(50,80,3))
            recorder.stop()
            self.assertIsNone(recorder.thread)
    def test_camera_error_reported(self):
        fake=types.SimpleNamespace(CAP_ANY=0,CAP_DSHOW=1,VideoCapture=DummyCap)
        bus=Bus();recorder=CameraPreview(3,bus)
        with patch.dict(sys.modules, {'cv2':fake}):
            recorder.start(2)
            recorder.thread.join(1)
            self.assertIsNotNone(recorder.error)
            self.assertTrue(any(x[1]=='ERROR' for x in bus.items))
            recorder.stop()

if __name__=='__main__': unittest.main()
