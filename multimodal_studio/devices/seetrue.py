import csv
import json
from collections import deque
import threading
import time
from pathlib import Path
from pylsl import StreamInlet, resolve_streams, local_clock
from core import RecorderBase

class SeeTrueRecorder(RecorderBase):
    def __init__(self, bus):
        super().__init__('SeeTrue', bus)
        self.worker = None
        self.stop_event = threading.Event()
        self.last_sample = None
        self.last_seen = None
        self.channels = []
        self.samples = 0
        self.selected_indices = []
        self.preview_timed = deque(maxlen=1000)
        self.preview = deque(maxlen=800)

    @staticmethod
    def channel_labels(info):
        labels = []
        channel = info.desc().child('channels').child('channel')
        for i in range(info.channel_count()):
            label = channel.child_value('label') if channel else ''
            labels.append(label or f'channel_{i+1}')
            if channel:
                channel = channel.next_sibling()
        return labels

    @staticmethod
    def validate_selection(indices, count):
        if not indices or len(set(indices)) != len(indices):
            raise ValueError('Select at least one unique LSL channel.')
        if any(not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= count for i in indices):
            raise ValueError('Selected channel indices do not match the LSL stream.')
        return sorted(indices)

    @staticmethod
    def scan(timeout=2.0):
        result = []
        for stream in resolve_streams(wait_time=timeout):
            try:
                inlet = StreamInlet(stream, recover=True)
                try:
                    info = inlet.info(timeout=2)
                    labels = SeeTrueRecorder.channel_labels(info)
                finally:
                    inlet.close_stream()
            except Exception:
                labels = [f'channel_{i+1}' for i in range(stream.channel_count())]
            result.append({'name': stream.name(), 'type': stream.type(),
                           'source_id': stream.source_id(), 'channels': stream.channel_count(),
                           'uid': stream.uid(), 'labels': labels})
        return result

    def start(self, folder, uid, channel_indices=None, timeout_seconds=5):
        self.stop_event.clear()
        self.failed = False
        self.active = True
        self.last_sample = self.last_seen = None
        self.samples = 0
        self.worker = threading.Thread(target=self._run, args=(Path(folder), uid, channel_indices, timeout_seconds), daemon=False)
        self.worker.start()

    def _run(self, folder, uid, channel_indices, timeout_seconds):
        inlet = None
        try:
            streams = resolve_streams(wait_time=3.0)
            match = next((s for s in streams if s.uid() == uid), None)
            if match is None:
                raise RuntimeError('Selected SeeTrue LSL stream is no longer available')
            inlet = StreamInlet(match, max_buflen=30, recover=True)
            info = inlet.info(timeout=5)
            self.channels = self.channel_labels(info)
            selected = self.validate_selection(
                list(range(info.channel_count())) if channel_indices is None else channel_indices,
                info.channel_count(),
            )
            self.selected_indices = selected
            headers = [f'ch_{i+1:03d}_{self.channels[i]}' for i in selected]
            # Store the exact mapping from saved columns to original LSL indices.
            (folder / 'seetrue_channels.json').write_text(json.dumps({
                'stream_name': info.name(), 'source_id': info.source_id(),
                'channel_count': info.channel_count(),
                'saved_channels': [{'index_zero_based': i, 'index_one_based': i+1,
                                    'label': self.channels[i], 'csv_column': headers[k]}
                                   for k, i in enumerate(selected)]
            }, indent=2), encoding='utf-8')
            with open(folder / 'seetrue_lsl.csv', 'w', newline='', encoding='utf-8') as fd:
                writer = csv.writer(fd)
                writer.writerow(['lsl_timestamp', 'local_receive_clock'] + headers)
                self.report('RECORDING', f'LSL stream={info.name()}, channels={self.channels}')
                while not self.stop_event.is_set():
                    sample, stamp = inlet.pull_sample(timeout=0.25)
                    if sample is not None:
                        now = local_clock()
                        writer.writerow([f'{stamp:.9f}', f'{now:.9f}'] + [sample[i] for i in selected])
                        self.samples += 1
                        was_failed = self.failed
                        self.last_sample = sample
                        self.preview.append(list(sample))
                        self.preview_timed.append((now,list(sample)))
                        self.last_seen = time.monotonic()
                        if was_failed:
                            self.failed = False
                            self.report('RECOVERED', 'SeeTrue samples resumed')
                    elif self.last_seen is not None and time.monotonic() - self.last_seen > timeout_seconds and not self.failed:
                        self.report('DROPPED', f'No LSL samples for {timeout_seconds} seconds; waiting for recovery')
        except Exception as exc:
            self.report('ERROR', f'LSL acquisition: {exc!r}')
        finally:
            if inlet:
                inlet.close_stream()
            self.active = False
            self.report('STOPPED', f'{self.samples} samples saved')

    def stop(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=7)
            if self.worker.is_alive():
                self.report('ERROR', 'LSL worker did not stop promptly')
        self.active = False
