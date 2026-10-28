import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core import EventBus, SessionManager, RecorderBase
import core
class StubStreamInfo:
    def __init__(self,*args): pass
class StubOutlet:
    def __init__(self,*args): pass
    def push_sample(self,*args,**kwargs): pass

class FakePolar(RecorderBase):
    def __init__(self,bus, fail=False):
        super().__init__('Polar H10',bus)
        self.fail=fail
        self.ready_called=False
        self.started=False
    def start(self,folder,**kwargs):
        self.started=True
        Path(folder,'polar_hr.csv').write_text('lsl_timestamp,bpm\n1,65\n')
    def wait_ready(self,**kwargs):
        self.ready_called=True
        if self.fail: raise TimeoutError('not ready')
    def stop(self): self.started=False

class FakeAudio(RecorderBase):
    def __init__(self,bus): super().__init__('Microphone',bus); self.started=False
    def start(self,folder,**kwargs):
        self.started=True
        Path(folder,'microphone.wav').write_bytes(b'example')
    def stop(self): self.started=False

class SessionTests(unittest.TestCase):
    def test_ready_before_session_and_sidecar(self):
        with patch.object(core,"StreamInfo",StubStreamInfo), patch.object(core,"StreamOutlet",StubOutlet), tempfile.TemporaryDirectory() as root:
            bus=EventBus(Path(root)/'logs'); session=SessionManager(bus)
            polar=FakePolar(bus); mic=FakeAudio(bus)
            session.start(root,'P01',{'Polar H10':(polar,{'ble_device':'fake','ecg':False,'acc':False}),'Microphone':(mic,{})})
            self.assertTrue(polar.ready_called)
            session.stop()
            folder=session.folder
            manifest=json.loads((folder/'session_manifest.json').read_text())
            self.assertEqual(manifest['status'],'COMPLETED')
            for name in ['microphone.wav','polar_hr.csv','markers.csv','config.json']:
                d=json.loads((folder/(name+'.session.json')).read_text())
                self.assertEqual(d['session_id'],manifest['session_id'])
            with (folder/'markers.csv').open() as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(rows[0]['marker'],'RECORDING_START')
            self.assertEqual(rows[0]['session_id'],manifest['session_id'])
            bus.close()
    def test_failure_aborts_before_start_marker(self):
        with patch.object(core,"StreamInfo",StubStreamInfo), patch.object(core,"StreamOutlet",StubOutlet), tempfile.TemporaryDirectory() as root:
            bus=EventBus(Path(root)/'logs'); session=SessionManager(bus)
            polar=FakePolar(bus,fail=True); mic=FakeAudio(bus)
            with self.assertRaises(TimeoutError):
                session.start(root,'P01',{'Polar H10':(polar,{'ble_device':'fake'}),'Microphone':(mic,{})})
            self.assertFalse(mic.started)
            self.assertFalse((session.folder/'markers.csv').exists())
            self.assertEqual(json.loads((session.folder/'session_manifest.json').read_text())['status'],'ABORTED')
            bus.close()

if __name__=='__main__':unittest.main()
