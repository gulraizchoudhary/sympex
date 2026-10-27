import csv
from collections import deque
import queue
import threading
import time
from pathlib import Path
import sounddevice as sd
import soundfile as sf
from pylsl import local_clock
from core import RecorderBase


def available_inputs():
    """Return actual PortAudio indices with useful Windows endpoint and backend names.

    WASAPI is prioritized because Windows generally exposes more descriptive endpoint
    names there. Never infer a PortAudio device index from Windows Device Manager IDs.
    """
    out = []
    hosts = sd.query_hostapis()
    for idx, d in enumerate(sd.query_devices()):
        if d['max_input_channels'] <= 0:
            continue
        host = hosts[d['hostapi']]['name']
        name = d['name'].strip()
        out.append((idx, f"{name}  —  {host}  (input #{idx})"))
    out.sort(key=lambda pair: (0 if 'WASAPI' in pair[1] else 1,
                               0 if 'ultramic' in pair[1].lower() else 1,
                               pair[1].casefold()))
    return out


class AudioRecorder(RecorderBase):
    def __init__(self, name, bus):
        super().__init__(name, bus)
        self.stream = None
        self.worker = None
        self.q = None
        self.count = 0
        self.lost_blocks = 0
        self.last_adc_ts = None
        self.preview = deque(maxlen=2500)
        self.preview_timed = deque(maxlen=10000)  # ~33s at 300 display points/s
        self.level = 0.0
        self.preview_lock = threading.Lock()
        self.last_callback = None
        self.callback_errors = 0
        self.total_frames_received = 0
        self._prev_status = None
        self._writer_failed = False

    def start(self, folder, device, sample_rate, channels=1):
        # A 384 kHz Ultramic input must use a compatible endpoint. Windows
        # DirectSound commonly rejects it; the user verified WASAPI Exclusive.
        device_info = sd.query_devices(device, 'input')
        backend = sd.query_hostapis(device_info['hostapi'])['name']
        high_rate = int(sample_rate) >= 384000
        if high_rate and 'WASAPI' not in backend.upper():
            raise ValueError(
                f'{sample_rate} Hz requires the WASAPI input endpoint for this '
                f'Ultramic setup. Selected: {device_info["name"]} ({backend}). '
                'Refresh the microphone list and select its WASAPI entry.'
            )
        extra_settings = (
            sd.WasapiSettings(exclusive=True)
            if high_rate and 'WASAPI' in backend.upper()
            else None
        )
        self._extra_settings = extra_settings
        self.report(
            'INFO',
            f'Opening microphone: {device_info["name"]} | index={device} | '
            f'backend={backend} | rate={sample_rate} Hz | channels={channels} | '
            f'WASAPI exclusive={extra_settings is not None}'
        )
        sd.check_input_settings(
            device=device, samplerate=sample_rate, channels=channels,
            dtype='int16', extra_settings=extra_settings
        )
        self._rate=sample_rate
        with self.preview_lock:
            self.preview.clear()
            self.preview_timed.clear()
        self.q = queue.Queue(maxsize=512)
        self.count = self.lost_blocks = 0
        self.total_frames_received = 0
        self.last_callback = None
        self.callback_errors = 0
        self._writer_failed = False
        self.failed = False
        self.active = True
        stem = self.name.lower().replace(' ', '_')
        self.worker = threading.Thread(target=self._writer, args=(Path(folder), stem, sample_rate, channels), daemon=False)
        self.worker.start()
        try:
            self.stream = sd.InputStream(
                device=device, samplerate=sample_rate, channels=channels,
                dtype='int16', blocksize=0, latency='high',
                extra_settings=self._extra_settings, callback=self._callback
            )
            self.stream.start()
            self.report('RECORDING', f'device={device}, rate={sample_rate} Hz, channels={channels}')
        except Exception:
            self.active = False
            self.q.put(None)
            self.worker.join()
            raise

    def _callback(self, data, frames, timing, status):
        # Estimate first sample LSL time using PortAudio's clock relationship.
        t = local_clock() - (timing.currentTime - timing.inputBufferAdcTime)
        if status:
            self.report('DROPPED' if status.input_overflow else 'WARNING', f'PortAudio: {status}')
        # Lightweight UI preview; never send the full high-rate stream to Tk.
        import numpy as np
        stride = max(1, int(self._rate / 300))  # rate-based, not callback-size-based
        down = data[:, 0][::stride].astype('float32') / 32768.0
        # Preview follows local LSL time. Some host drivers report ADC timestamps
        # with unusable offsets: retain original t for the recording metadata,
        # but use arrival-based times for display if the offset is implausible.
        display_t = t
        display_now = local_clock()
        block_duration = frames / self._rate
        if not (0 <= display_now - t <= 5.0 + block_duration):
            display_t = display_now - block_duration
        times = display_t + np.arange(len(down), dtype=np.float64) * (stride / self._rate)
        with self.preview_lock:
            self.preview.extend(down.tolist())
            self.preview_timed.extend(zip(times.tolist(), down.tolist()))
            self.level = float(np.sqrt(np.mean((data[:, 0].astype(np.float64) / 32768.0)**2))) if frames else 0.0
            self.last_callback = time.monotonic()
            self.total_frames_received += frames
        try:
            self.q.put_nowait((data.copy(), frames, t, int(bool(status.input_overflow))))
        except queue.Full:
            self.lost_blocks += 1
            self.report('DROPPED', 'Audio writing queue full; samples lost')

    def _writer(self, folder, stem, rate, channels):
        index = 0
        try:
            with sf.SoundFile(folder / f'{stem}.wav', 'w', samplerate=rate, channels=channels, subtype='PCM_16', format='WAV') as wav, open(folder / f'{stem}_timestamps.csv', 'w', newline='', encoding='utf-8') as fd:
                writer = csv.writer(fd)
                writer.writerow(['file_sample_index', 'frames', 'lsl_estimated_first_sample', 'overflow'])
                while True:
                    item = self.q.get()
                    if item is None:
                        break
                    samples, frames, first_ts, overflow = item
                    wav.write(samples)
                    writer.writerow([index, frames, f'{first_ts:.9f}', overflow])
                    index += frames
                    self.count = index
        except Exception as exc:
            self._writer_failed = True
            self.report('ERROR', f'audio writer failed: {exc!r}')
            self.active = False

    def snapshot_preview(self):
        with self.preview_lock:
            return list(self.preview_timed)

    def health(self):
        if self.active and self.last_callback is not None and time.monotonic() - self.last_callback > 3:
            if not self.failed:
                self.report('DROPPED', 'No microphone callbacks for more than 3 seconds')
        if self.active and self.stream is not None and not self.stream.active:
            self.report('DROPPED', 'Audio stream became inactive')
            self.active = False

    def stop(self):
        if self.stream:
            try:
                self.stream.stop()
            except Exception as exc:
                self.report('ERROR', f'Audio stop: {exc!r}')
            finally:
                try: self.stream.close()
                except Exception as exc: self.report('ERROR', f'Audio close: {exc!r}')
                self.stream = None
        if self.q and self.worker:
            try:
                self.q.put(None, timeout=2)
            except queue.Full:
                self.report('ERROR', 'Writer queue stuck; stop sentinel could not be queued')
            self.worker.join(timeout=15)
            if self.worker.is_alive():
                self.report('ERROR', 'Audio writer did not finish within timeout')
        self.active = False
        self.report('STOPPED', f'{self.count} samples saved; queue drops={self.lost_blocks}')
