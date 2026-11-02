import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from devices.seetrue import SeeTrueRecorder


class FakeBus:
    def __init__(self): self.events=[]
    def emit(self,*args): self.events.append(args)


class FakeInfo:
    def __init__(self, uid):self._uid=uid
    def name(self):return 'Duplicate Name'
    def uid(self):return self._uid
    def source_id(self):return self._uid
    def hostname(self):return 'host-A'
    def channel_count(self):return 3
    def nominal_srate(self):return 100.0
    def desc(self):return FakeDesc()

class FakeDesc:
    def child(self,name):return self
    def child_value(self,name):return ''
    def next_sibling(self):return self

class FakeInlet:
    def __init__(self,info,**kwargs):self._info=info;self.count=0
    def info(self,timeout=5):return self._info
    def pull_sample(self,timeout=.25):
        self.count+=1
        if self.count==1:return [12,23,34],1234.5
        raise StopIteration('Stop fake source')
    def close_stream(self):pass

class MultiLSLTests(unittest.TestCase):
    def test_independent_duplicate_name_outlets_and_channels(self):
        with tempfile.TemporaryDirectory() as d:
            for n,uid,channels in [(1,'unique-A',[0,2]),(2,'unique-B',[1])]:
                r=SeeTrueRecorder(FakeBus(),name=f'Network LSL {n:02d}',filename_prefix=f'network_lsl_{n:02d}')
                with patch('devices.seetrue.resolve_streams',return_value=[FakeInfo(uid)]),\
                     patch('devices.seetrue.StreamInlet',FakeInlet):
                    r._run(Path(d),uid,channels,5)
            a=Path(d)/'network_lsl_01_lsl.csv'
            b=Path(d)/'network_lsl_02_lsl.csv'
            self.assertTrue(a.exists() and b.exists())
            with a.open(newline='') as f:
                self.assertEqual(len(next(csv.reader(f))),4)
            with b.open(newline='') as f:
                self.assertEqual(len(next(csv.reader(f))),3)
            meta=json.loads((Path(d)/'network_lsl_02_channels.json').read_text())
            self.assertEqual(meta['uid'],'unique-B')
            self.assertEqual(meta['saved_channels'][0]['index_zero_based'],1)

if __name__=='__main__':unittest.main()
