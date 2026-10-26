import csv
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

from devices.camera_recorder import CameraRecorder

class Bus:
    def __init__(self): self.events=[]
    def emit(self, *args): self.events.append(args)

class FakeCapture:
    def __init__(self, fail=False):
        self.fail=fail;self.count=0;self.released=False
    def get(self, _): return 25.0
    def read(self):
        time.sleep(.004)
        self.count+=1
        return (not self.fail, np.zeros((60,80,3), dtype=np.uint8) if not self.fail else None)
    def release(self): self.released=True

class FakeWriter:
    def __init__(self):self.frames=0;self.released=False
    def isOpened(self):return True
    def write(self,frame):self.frames+=1
    def release(self):self.released=True

class FakeCV2:
    CAP_PROP_FPS=5
    COLOR_BGR2RGB=1
    @staticmethod
    def VideoWriter_fourcc(*args):return 1234
    @staticmethod
    def cvtColor(frame,_):return frame.copy()
    @staticmethod
    def resize(frame,size):return np.zeros((size[1],size[0],3),dtype=np.uint8)
    def __init__(self):self.writer=FakeWriter()
    def VideoWriter(self,*args):return self.writer

class CameraRecordingTests(unittest.TestCase):
    def test_recording_writes_timestamped_frames_and_preview(self):
        bus=Bus();camera=CameraRecorder(2,bus);fake=FakeCV2();cap=FakeCapture()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict('sys.modules', {'cv2':fake}), patch('devices.camera_recorder.open_camera',return_value=(cap,np.zeros((60,80,3),dtype=np.uint8),'Default')):
                camera.start(tmp,index=4)
                time.sleep(.12)
                frame,stamp,error=camera.snapshot()
                self.assertIsNotNone(frame)
                self.assertGreater(stamp,0)
                self.assertIsNone(error)
                camera.stop()
            self.assertTrue(cap.released)
            self.assertTrue(fake.writer.released)
            self.assertGreater(camera.frames_written,1)
            with open(Path(tmp)/'camera_2_frames.csv',newline='') as file:
                rows=list(csv.DictReader(file))
            self.assertEqual(len(rows),camera.frames_written)
            self.assertEqual([int(r['frame_index']) for r in rows],list(range(camera.frames_written)))
            meta=json.loads((Path(tmp)/'camera_2_metadata.json').read_text())
            self.assertEqual(meta['device_index'],4)
    def test_camera_failure_is_logged_and_stop_safe(self):
        bus=Bus();camera=CameraRecorder(1,bus)
        with tempfile.TemporaryDirectory() as tmp:
            with patch('devices.camera_recorder.open_camera',return_value=(None,None,None)):
                with self.assertRaises(RuntimeError):camera.start(tmp,index=0)
                camera.stop()
        self.assertTrue(any(s=='ERROR' for _,s,_ in bus.events))

if __name__=='__main__': unittest.main()
