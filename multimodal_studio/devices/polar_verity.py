# Author: Gulraiz
"""Optional Polar Verity Sense recorder. Alternative to H10, never simultaneous.

The default capture mode is BLE HR + 55 Hz four-channel PPG; ACC is optional.
PPI is deliberately not mixed into SDK/PPG mode.
"""
import csv
import threading
from collections import deque
from core import StreamInfo, StreamOutlet, local_clock
from devices.polar import PolarRecorder


class PolarVerityRecorder(PolarRecorder):
    def __init__(self, bus):
        super().__init__(bus)
        self.name = 'Polar Verity Sense'
        self.ready_events = {name: threading.Event() for name in ('hr', 'ppg', 'acc')}
        self.ppg_preview = deque(maxlen=1800)
        self.ppg_timed = deque(maxlen=3000)
        self.sample_counts = {'hr': 0, 'ppg': 0, 'acc': 0}

    @staticmethod
    async def scan_async():
        from bleak import BleakScanner
        return await BleakScanner.find_device_by_filter(
            lambda bd, ad: bool(bd.name and
                ('Polar Sense' in bd.name or 'Verity Sense' in bd.name)), timeout=8)

    def start(self, folder, ble_device, ppg=True, acc=True):
        if ble_device is None:
            raise ValueError('Scan/select a Polar Verity Sense first')
        self.sample_counts = {'hr': 0, 'ppg': 0, 'acc': 0}
        self.ppg_preview.clear()
        self.ppg_timed.clear()
        self._want_ppg, self._want_acc = bool(ppg), bool(acc)
        # Reuse tested H10 BLE thread, stop, file-lock, and callbacks.
        super().start(folder, ble_device, ecg=False, acc=acc)

    def _file(self, name, header):
        # Preserve the original H10 recorder and file naming.
        fd = open(self.folder / f'polar_verity_{name}.csv', 'w', newline='', encoding='utf-8')
        writer = csv.writer(fd)
        writer.writerow(header)
        self.files[name], self.writers[name] = fd, writer

    def wait_ready(self, ppg=True, acc=True, timeout=45, cancel=None):
        import time
        required = ['hr'] + (['ppg'] if ppg else []) + (['acc'] if acc else [])
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise RuntimeError('Verity Sense readiness cancelled')
            if self.failed or (self.future and self.future.done() and self.future.exception()):
                raise RuntimeError('Verity Sense startup failed; inspect Activity log')
            if all(self.ready_events[key].is_set() for key in required):
                self.report('READY', 'Live Verity samples: ' + ', '.join(required))
                return
            time.sleep(.1)
        missing = [key for key in required if not self.ready_events[key].is_set()]
        raise TimeoutError('Verity Sense not ready: no live ' + ', '.join(missing) + ' samples')

    def _outlet(self, name, kind, labels):
        info = StreamInfo(f'IMS_PolarVerity_{name.upper()}', kind, len(labels), 0, 'float32',
                          f'IMS_PolarVerity_{self.lsl_instance_id}_{name.upper()}')
        info.desc().append_child_value('manufacturer', 'Polar')
        info.desc().append_child_value('device_model', 'Verity Sense')
        info.desc().append_child_value('recording_system', 'IMMSS')
        channels = info.desc().append_child('channels')
        for label,unit in labels:
            ch=channels.append_child('channel')
            ch.append_child_value('label',label)
            ch.append_child_value('unit',unit)
        self.outlets[name] = StreamOutlet(info)

    async def _connect(self, ecg, acc):
        from polar_python import PolarDevice
        self.device = PolarDevice(self.ble_device)
        try:
            await self.device.connect()
            if self.stop_requested: return
            with self.file_lock:
                self._file('hr', ['lsl_timestamp','bpm'])
                self._outlet('hr', 'HeartRate', [('BPM','beats/min')])
                if self._want_ppg:
                    self._file('ppg', ['lsl_estimate','device_timestamp','ppg_1','ppg_2','ppg_3','ambient'])
                    self._outlet('ppg', 'PPG', [(f'PPG_{n}','device units') for n in range(1,4)]+[('Ambient','device units')])
                if acc:
                    self._file('acc', ['lsl_estimate','device_timestamp','x','y','z'])
                    self._outlet('acc', 'Accelerometer', [(f'ACC_{axis}','mg') for axis in ('X','Y','Z')])
            await self.device.start_hr_stream(hr_callback=self._hr)
            if self._want_ppg and not self.stop_requested:
                await self.device.start_ppg_stream(ppg_callback=self._ppg, sample_rate=55,
                                                   resolution=22, channels=4)
            if acc and not self.stop_requested:
                await self.device.start_acc_stream(acc_callback=self._acc, sample_rate=52,
                                                   resolution=16, range=8, channels=3)
            if not self.stop_requested:
                self.report('RECORDING', 'Verity Sense HR/PPG/ACC acquisition started')
        finally:
            if self.stop_requested and self.device:
                await self.device.disconnect()

    def _hr(self, data):
        # Unlike ECG-RR, optical PPI should not be labeled RR or written to an RR file.
        try:
            now = local_clock()
            self.last_packet = now
            value = float(data.heartrate)
            self.last_hr = value
            self.hr_preview.append(value)
            self.hr_timed.append((now,value))
            self.sample_counts['hr'] += 1
            self.ready_events['hr'].set()
            self._write('hr', [[now,value]])
            self.outlets['hr'].push_sample([value],timestamp=now)
        except Exception as exc:
            self.report('ERROR', f'Verity HR callback: {exc!r}')

    def _ppg(self, data):
        try:
            samples = list(getattr(data,'data',[]) or [])
            if not samples: return
            now = local_clock()
            stamp = getattr(data,'timestamp','')
            rows=[]
            for i,item in enumerate(samples):
                vals = list(item)
                if len(vals)!=4:
                    raise ValueError('Expected four PPG channels from Verity Sense')
                t = now - (len(samples)-1-i)/55.0
                vals=[float(v) for v in vals]
                rows.append([t,stamp,*vals])
                self.ppg_preview.append(vals[0]);self.ppg_timed.append((t,vals[0]))
                self.outlets['ppg'].push_sample(vals,timestamp=t)
            self.sample_counts['ppg'] += len(rows)
            self.last_packet = now
            self.ready_events['ppg'].set()
            self._write('ppg',rows)
        except Exception as exc:
            self.report('ERROR', f'Verity PPG callback: {exc!r}')

    def _acc(self, data):
        # Polar's Verity Sense accelerometer streams at 52 Hz (H10 uses 25 Hz).
        try:
            samples=list(getattr(data,'data',[]) or [])
            if not samples:return
            now=local_clock();stamp=getattr(data,'timestamp','')
            rows=[]
            for i,item in enumerate(samples):
                t=now-(len(samples)-1-i)/52.0
                x,y,z=(float(v) for v in item[:3])
                rows.append([t,stamp,x,y,z]);self.acc_preview.append((x,y,z))
                self.outlets['acc'].push_sample([x,y,z],timestamp=t)
            self.sample_counts['acc'] += len(rows)
            self.last_packet=now
            self.ready_events['acc'].set()
            self._write('acc', rows)
        except Exception as exc:
            self.report('ERROR', f'Verity ACC callback: {exc!r}')
