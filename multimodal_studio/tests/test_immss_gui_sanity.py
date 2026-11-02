import unittest
import queue
from pathlib import Path
from unittest.mock import MagicMock
import sys
import types
try:
    import bleak
    import polar_python
except ImportError:
    fake = types.ModuleType('devices.polar')
    fake.PolarRecorder = type('PolarRecorder', (), {})
    sys.modules.setdefault('devices.polar',fake)
from app import App

class Var:
    def __init__(self,value=None): self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value
class Combo:
    def __init__(self,index=-1):self.index=index;self.value='';self.options=[]
    def current(self, value=None):
        if value is None:return self.index
        self.index=value
    def configure(self,**kwargs):
        if 'values' in kwargs:self.options=kwargs['values']
    def set(self,value):self.value=value;self.index=-1
class FakeBus:
    def __init__(self):self.events=queue.Queue();self.logged=[]
    def emit(self,*args):self.logged.append(args)

class GuiSanity(unittest.TestCase):
    def test_brand_and_compatibility(self):
        root=Path(__file__).resolve().parents[1]
        app=(root/'app.py').read_text()
        core=(root/'core.py').read_text()
        self.assertIn('Intelligent Multimodal Microsurgery Studio (IMMSS)',app)
        self.assertIn('IMS_Experiment_Markers',core)
    def test_selected_network_counts_on_dashboard(self):
        a=App.__new__(App)
        a.enabled={'Microphone':Var(False),'Polar H10':Var(False)}
        a.network_selections={'a':{'enabled':True},'b':{'enabled':True},'c':{'enabled':False}}
        a.count_label=Var();a.session_status=Var();a.session=MagicMock(active=False)
        a.preview_active=False
        a._update_selection()
        self.assertEqual(a.count_label.get(),'2 selected')
        self.assertEqual(a.session_status.get(),'Ready to record')
    def test_camera_rescan_keeps_distinct_indices(self):
        a=App.__new__(App)
        a.camera_choices=[(0,'A'),(3,'B'),(7,'C')]
        a.camera_selects={1:Combo(1),2:Combo(2),3:Combo(0)}
        a.enabled={f'Camera {i}':Var(True) for i in range(1,4)}
        a.status={f'Camera {i}':Var() for i in range(1,4)}
        a._start_results=queue.Queue();a.bus=FakeBus()
        a.bus.events.put(('CAMERA_SCAN','DONE',[(7,'C'),(0,'A'),(3,'B')]))
        a._camera_scanning=True
        a._log=lambda x:None
        a.session=MagicMock(active=False,recorders={})
        a.preview_active=False;a.started_at=None
        a._update_selection=lambda:None;a._draw_monitors=lambda:None
        a.root=MagicMock()
        a.poll()
        self.assertEqual([a.camera_choices[a.camera_selects[i].current()][0] for i in (1,2,3)],[3,7,0])
    def test_missing_camera_removed_without_wrong_assignment(self):
        a=App.__new__(App)
        a.camera_choices=[(0,'A'),(3,'B')]
        a.camera_selects={1:Combo(1),2:Combo(0)}
        a.enabled={'Camera 1':Var(True),'Camera 2':Var(True)}
        a.status={'Camera 1':Var(),'Camera 2':Var()}
        a._start_results=queue.Queue();a.bus=FakeBus()
        a.bus.events.put(('CAMERA_SCAN','DONE',[(0,'A')]))
        a._log=lambda x:None;a.session=MagicMock(active=False,recorders={});a.preview_active=False
        a.started_at=None;a._update_selection=lambda:None;a._draw_monitors=lambda:None;a.root=MagicMock()
        a.poll()
        self.assertEqual(a.camera_selects[1].current(),-1)
        self.assertEqual(a.camera_selects[2].current(),0)
        self.assertEqual(a.status['Camera 1'].get(),'Choose a detected camera')
    def test_preview_stops_only_started_devices(self):
        a=App.__new__(App)
        failed=MagicMock(); good=MagicMock()
        a.preview_active=True
        a.preview_devices={'failed':(failed,{}),'good':(good,{})}
        a.preview_started={'good'}
        a.camera_previews={}
        a.preview_tmp=None
        a.preview_btn=MagicMock();a.status_top=MagicMock();a.session_status=Var()
        a._lock=lambda locked:None
        a.stop_preview()
        failed.stop.assert_not_called();good.stop.assert_called_once()
        self.assertFalse(a.preview_active)

if __name__=='__main__':unittest.main()
