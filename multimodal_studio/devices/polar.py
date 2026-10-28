import asyncio
import csv
from collections import deque
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from bleak import BleakScanner
from polar_python import PolarDevice
from pylsl import StreamInfo, StreamOutlet, local_clock
from core import RecorderBase

class PolarRecorder(RecorderBase):
    """Uses the polar-python sequence from the earlier working H10 project."""
    def __init__(self, bus):
        super().__init__('Polar H10', bus)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._loop_main, daemon=True)
        self.thread.start()
        self.device = None
        self.future = None  # Remains None when validation fails before connection.
        self.stop_requested = True
        self.ble_device = None
        self.files = {}
        self.writers = {}
        self.file_lock = threading.RLock()
        self.outlets = {}
        self.last_hr = None
        self.hr_preview = deque(maxlen=180)
        self.ecg_preview = deque(maxlen=1800)
        self.ecg_timed = deque(maxlen=3000)
        self.hr_timed = deque(maxlen=200)
        self.acc_preview = deque(maxlen=1000)
        self.last_packet = None
        self.ready_events = {key: threading.Event() for key in ('hr','ecg','acc')}

    def wait_ready(self, ecg=True, acc=True, timeout=45, cancel=None):
        """Wait for real packets, not merely a successful BLE connection."""
        required = ['hr'] + (['ecg'] if ecg else []) + (['acc'] if acc else [])
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cancel is not None and cancel.is_set():
                raise RuntimeError('Polar readiness cancelled')
            if self.failed or (self.future and self.future.done() and self.future.exception()):
                raise RuntimeError('Polar stream startup failed; inspect Activity log')
            if all(self.ready_events[key].is_set() for key in required):
                self.report('READY', f'Live samples confirmed: {", ".join(required)}')
                return
            time.sleep(0.10)
        missing = [key for key in required if not self.ready_events[key].is_set()]
        raise TimeoutError('Polar H10 not ready: no live '+', '.join(missing)+' samples within '+str(timeout)+' seconds')

    def _loop_main(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    @staticmethod
    async def scan_async():
        dev = await BleakScanner.find_device_by_filter(lambda bd, ad: bool(bd.name and 'Polar H10' in bd.name), timeout=8)
        return dev

    def scan(self):
        return self.submit(self.scan_async())

    def start(self, folder, ble_device, ecg=True, acc=True):
        if ble_device is None:
            raise ValueError('Scan/select a Polar H10 first')
        self.ble_device = ble_device
        for event in self.ready_events.values(): event.clear()
        self.failed = False
        self.last_packet = None
        self.folder = Path(folder)
        self.stop_requested = False
        self.future = self.submit(self._connect(ecg, acc))
        self.active = True
        self.future.add_done_callback(self._connect_done)
        self.report('CONNECTING', str(ble_device))

    def _connect_done(self, future):
        try:
            future.result()
        except Exception as exc:
            self.active = False
            self.report('ERROR', f'Polar connection/stream failed: {exc!r}')

    def _file(self, name, header):
        fd = open(self.folder / f'polar_{name}.csv', 'w', newline='', encoding='utf-8')
        writer = csv.writer(fd)
        writer.writerow(header)
        self.files[name], self.writers[name] = fd, writer

    async def _connect(self, ecg, acc):
        self.device = PolarDevice(self.ble_device)
        try:
            await self.device.connect()
            if self.stop_requested:
                return
            with self.file_lock:
                self._file('hr', ['lsl_timestamp','bpm'])
                self._file('rr', ['lsl_timestamp','rr_ms'])
                if ecg: self._file('ecg', ['lsl_estimate','device_timestamp','value'])
                if acc: self._file('acc', ['lsl_estimate','device_timestamp','x','y','z'])
                for name, count in [('hr',1),('rr',1),('ecg',1),('acc',3)]:
                    if name in self.files:
                        info = StreamInfo('PolarH10_'+name.upper(), name.upper(), count, 0, 'float32', 'polarh10_'+name)
                        self.outlets[name] = StreamOutlet(info)
            await self.device.start_hr_stream(hr_callback=self._hr)
            await asyncio.sleep(1)
            if ecg and not self.stop_requested:
                try:
                    await self.device.start_ecg_stream(self._ecg, 130, 14)
                except Exception as exc: self.report('ERROR', f'ECG unavailable: {exc!r}')
            await asyncio.sleep(1)
            if acc and not self.stop_requested:
                try:
                    await self.device.start_acc_stream(self._acc, 25, 16, 2)
                except Exception as exc: self.report('ERROR', f'ACC unavailable: {exc!r}')
            if not self.stop_requested:
                self.report('RECORDING', 'Polar HR/RR streaming; requested optional streams attempted')
        except Exception:
            raise
        finally:
            if self.stop_requested and self.device:
                await self.device.disconnect()

    def _write(self, name, rows):
        with self.file_lock:
            if name not in self.writers or self.stop_requested:
                return
            try:
                self.writers[name].writerows(rows)
                self.files[name].flush()
            except Exception as exc:
                self.report('ERROR', f'CSV writer {name}: {exc!r}')

    def _hr(self, data):
        try:
            now = local_clock()
            self.last_packet = now
            self.ready_events["hr"].set()
            value = float(data.heartrate)
            self.last_hr = value
            self.hr_preview.append(value)
            self.hr_timed.append((now,value))
            self._write('hr', [[now, value]])
            self.outlets['hr'].push_sample([value], timestamp=now)
            rr = list(getattr(data, 'rr_intervals', []) or [])
            elapsed = 0.0
            rows = []
            for interval in reversed(rr):
                t = now - elapsed
                rows.append([t, float(interval)])
                elapsed += float(interval)/1000
            for t, value in reversed(rows):
                self._write('rr', [[t, value]])
                self.outlets['rr'].push_sample([value], timestamp=t)
        except Exception as exc: self.report('ERROR', f'HR callback: {exc!r}')

    def _ecg(self, data):
        try:
            samples = list(getattr(data, 'data', []) or [])
            now = local_clock()
            self.last_packet = now
            if samples: self.ready_events["ecg"].set()
            stamp = getattr(data, 'timestamp', '')
            rows = []
            for i, sample in enumerate(samples):
                t = now - (len(samples)-1-i)/130
                rows.append([t, stamp, float(sample)])
                self.ecg_preview.append(float(sample))
                self.ecg_timed.append((t,float(sample)))
                if 'ecg' in self.outlets: self.outlets['ecg'].push_sample([float(sample)], timestamp=t)
            self._write('ecg', rows)
        except Exception as exc: self.report('ERROR', f'ECG callback: {exc!r}')

    def _acc(self, data):
        try:
            samples = list(getattr(data, 'data', []) or [])
            now = local_clock()
            self.last_packet = now
            if samples: self.ready_events["acc"].set()
            stamp = getattr(data, 'timestamp', '')
            rows=[]
            for i, item in enumerate(samples):
                t = now - (len(samples)-1-i)/25
                x,y,z = (float(v) for v in item[:3])
                rows.append([t,stamp,x,y,z])
                self.acc_preview.append((x,y,z))
                if 'acc' in self.outlets: self.outlets['acc'].push_sample([x,y,z], timestamp=t)
            self._write('acc',rows)
        except Exception as exc: self.report('ERROR', f'ACC callback: {exc!r}')

    def health(self):
        if self.active and self.last_packet and local_clock()-self.last_packet > 6 and not self.failed:
            self.report('DROPPED', 'No BLE packets for six seconds; other devices continue')

    async def _disconnect(self):
        if self.device is not None:
            await self.device.disconnect()
            self.device = None

    def stop(self):
        self.stop_requested = True
        if self.future and not self.future.done():
            try: self.future.result(timeout=5)
            except Exception: pass
        # A validation failure never opened a BLE connection; do not disconnect.
        if self.future is not None or self.device is not None:
            try:
                self.submit(self._disconnect()).result(timeout=5)
            except Exception as exc: self.report('ERROR', f'BLE disconnect: {exc!r}')
        with self.file_lock:
            for fd in self.files.values(): fd.close()
            self.files.clear()
            self.writers.clear()
        self.outlets.clear()
        self.future = None
        self.active = False
        self.report('STOPPED', 'Polar finished')

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=3)
