# Author: Gulraiz
"""Timestamped JPEG frame sequences and matching LSL frame-index telemetry.

The timestamp is taken after OpenCV frame retrieval, not at sensor exposure.
Image files are not streamed over LSL: only the committed frame index and timing.
"""
from __future__ import annotations

import csv
import hashlib
import json
import queue
import threading
import time
import uuid
from pathlib import Path

from pylsl import StreamInfo, StreamOutlet, local_clock

from core import RecorderBase
from devices.camera_controls import normalize_settings
from devices.camera_preview import open_camera
from output_names import session_file


class CameraRecorder(RecorderBase):
    JPEG_QUALITY = 85
    MAX_PENDING_FRAMES = 32

    def __init__(self, slot, bus):
        super().__init__(f'Camera {slot}', bus)
        self.slot = slot
        self.thread = None
        self.writer_thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.last_rgb = None
        self.last_time = 0.0
        self.error = None
        self.device_index = None
        self.frames_written = 0
        self.frames_captured = 0
        self.frames_dropped = 0
        self.lsl_publish_errors = 0
        self.ready = threading.Event()
        self.lsl_outlet = None
        self.frame_queue = None
        self.actual_width = 0
        self.actual_height = 0
        self.actual_capture_fps = 0.0
        self.capture_duration_seconds = 0.0
        self.frame_folder = None
        self.frame_index_path = None
        self.started_monotonic = None
        self.ended_monotonic = None

    def start(self, folder, index, fps=30.0, camera_settings=None):
        if (self.thread and self.thread.is_alive()) or (self.writer_thread and self.writer_thread.is_alive()):
            raise RuntimeError('Camera is already active')
        self.device_index = int(index)
        self.camera_settings = normalize_settings(camera_settings)
        self.error = None
        self.failed = False
        self.frames_written = self.frames_captured = self.frames_dropped = 0
        self.lsl_publish_errors = 0
        self.lsl_outlet = None  # Never reuse a previous recording outlet.
        self.stop_event.clear()
        self.ready.clear()
        self.frame_queue = queue.Queue(maxsize=self.MAX_PENDING_FRAMES)
        self.started_monotonic = self.ended_monotonic = None
        self.capture_duration_seconds = 0.0
        with self.lock:
            self.last_rgb = None
            self.last_time = 0.0
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._run, args=(folder,), name=f'CameraCapture{self.slot}', daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout=15):
            self.report('WARNING', 'Camera initialization still pending; check status/logs')
        if self.error:
            raise RuntimeError(self.error)

    def _report_error(self, reason):
        self.error = str(reason)
        self.failed = True
        self.report('ERROR', f'Camera {self.slot} recording interrupted: {reason}')
        self.stop_event.set()

    def _make_outlet(self, frame_folder):
        try:
            info = StreamInfo(f'IMS_Camera{self.slot}_Frames', 'VideoFrames', 4, 0,
                              'double64', f'IMS_Camera{self.slot}_{uuid.uuid4().hex}')
            desc = info.desc()
            desc.append_child_value('recording_system', 'IMMSS')
            desc.append_child_value('camera_index', str(self.device_index))
            desc.append_child_value('frame_folder', frame_folder.name)
            desc.append_child_value('frame_file_pattern', '%08d.jpg')
            desc.append_child_value('timestamp_semantics', 'host-side clock immediately after cap.read; not exposure time')
            desc.append_child_value('frame_index_semantics', 'zero-based, successfully saved JPEG frames')
            channels = desc.append_child('channels')
            for label, unit in [('FrameNumber', 'index'), ('Width', 'pixels'),
                                ('Height', 'pixels'), ('MonotonicCaptureTime', 'seconds')]:
                channel = channels.append_child('channel')
                channel.append_child_value('label', label)
                channel.append_child_value('unit', unit)
            self.lsl_outlet = StreamOutlet(info)
        except Exception as exc:
            self.lsl_outlet = None
            self.report('WARNING', f'Camera frame-number LSL stream unavailable: {exc!r}')

    def _write_frames(self, folder, width, height, backend, raw_width, raw_height):
        import cv2
        csv_file = None
        image_digest = hashlib.sha256()
        total_jpeg_bytes = 0
        try:
            self.frame_folder = session_file(folder, f'camera_{self.slot}_frames')
            self.frame_folder.mkdir(parents=True, exist_ok=True)
            self.frame_index_path = session_file(folder, f'camera_{self.slot}_frames.csv')
            self._make_outlet(self.frame_folder)
            csv_file = self.frame_index_path.open('w', newline='', encoding='utf-8')
            output = csv.writer(csv_file)
            output.writerow(['frame_index', 'lsl_capture_time_estimate', 'monotonic_capture_time',
                             'width', 'height', 'filename'])
            while True:
                try:
                    item = self.frame_queue.get(timeout=.1)
                except queue.Empty:
                    if self.stop_event.is_set() and (not self.thread or not self.thread.is_alive()):
                        break
                    continue
                if item is None:
                    break
                frame, lsl_ts, mono_ts = item
                # Only successful disk writes become numbered frames or LSL samples.
                try:
                    ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, self.JPEG_QUALITY])
                    if not ok:
                        raise RuntimeError('JPEG encoder failed')
                    n = self.frames_written
                    filename = f'{n:08d}.jpg'
                    target = self.frame_folder / filename
                    payload = encoded.tobytes()
                    with target.open('xb') as file:
                        file.write(payload)
                    # Ordered digest of filenames and exact bytes, no re-reading at finalization.
                    image_digest.update(filename.encode('ascii') + b'\0')
                    image_digest.update(len(payload).to_bytes(8, 'big'))
                    image_digest.update(payload)
                    total_jpeg_bytes += len(payload)
                    output.writerow([n, f'{lsl_ts:.9f}', f'{mono_ts:.9f}', width, height, filename])
                    self.frames_written += 1
                    if self.lsl_outlet is not None:
                        try:
                            self.lsl_outlet.push_sample([float(n), float(width), float(height), float(mono_ts)], timestamp=lsl_ts)
                        except Exception as exc:
                            self.lsl_publish_errors += 1
                            if self.lsl_publish_errors == 1:
                                self.report('WARNING', f'Camera LSL frame publication failed: {exc!r}')
                            self.lsl_outlet = None
                    if self.frames_written % 30 == 0:
                        csv_file.flush()
                except Exception as exc:
                    self._report_error(f'JPEG/frame-index write failed: {exc}')
                    break
        except Exception as exc:
            self._report_error(f'Camera writer initialization failed: {exc}')
        finally:
            if csv_file is not None:
                csv_file.close()
            duration = max(0.0, (self.ended_monotonic or time.monotonic()) - (self.started_monotonic or time.monotonic()))
            self.capture_duration_seconds = duration
            self.actual_capture_fps = self.frames_captured / duration if duration else 0.0
            metadata = {
                'device_index': self.device_index, 'backend': backend,
                'width': width, 'height': height, 'raw_camera_width': raw_width, 'raw_camera_height': raw_height,
                'requested_fps': self.camera_settings['fps'], 'actual_capture_fps_estimate': self.actual_capture_fps,
                'frames_captured': self.frames_captured, 'frames_written': self.frames_written,
                'capture_duration_seconds': duration,
                'frames_dropped_writer_queue': self.frames_dropped,
                'lsl_publish_errors': self.lsl_publish_errors,
                'frame_folder': self.frame_folder.name if self.frame_folder else None,
                'image_format': 'JPEG', 'jpeg_quality': self.JPEG_QUALITY,
                'total_jpeg_bytes': total_jpeg_bytes,
                'ordered_frame_sha256': image_digest.hexdigest(),
                'timestamp_semantics': 'host-side timestamp following cap.read, not hardware exposure time',
                'requested_settings': self.camera_settings,
            }
            try:
                session_file(folder, f'camera_{self.slot}_metadata.json').write_text(
                    json.dumps(metadata, indent=2), encoding='utf-8')
            except Exception as exc:
                self._report_error(f'Cannot finalize camera metadata: {exc}')
            self.lsl_outlet = None

    def _run(self, folder):
        import cv2
        cap = None
        try:
            cap, first, backend = open_camera(self.device_index,
                report=lambda msg: self.report('INFO', msg), attempts=12, settings=self.camera_settings)
            if cap is None:
                raise RuntimeError(f'Cannot open camera index {self.device_index}')
            raw_h, raw_w = first.shape[:2]
            width = self.camera_settings['width']
            height = self.camera_settings['height']
            self.actual_width, self.actual_height = width, height
            if (raw_w, raw_h) != (width, height):
                self.report('WARNING', f'Camera returned {raw_w}x{raw_h}; resizing stored JPEGs to {width}x{height}')
            self.started_monotonic = time.monotonic()
            self.writer_thread = threading.Thread(target=self._write_frames,
                args=(folder, width, height, backend, raw_w, raw_h),
                name=f'CameraJPEGWriter{self.slot}', daemon=True)
            self.writer_thread.start()
            self.active = True
            self.ready.set()
            self.report('RECORDING', f'Camera {self.slot}: JPEG sequence requested {width}x{height} at {self.camera_settings["fps"]:g} fps')
            consecutive_failures = 0
            period = 1.0 / self.camera_settings['fps']
            last_accepted = float('-inf')
            frame = first
            while not self.stop_event.is_set():
                if frame is None:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        consecutive_failures += 1
                        if consecutive_failures >= 15:
                            raise RuntimeError('15 consecutive frame read failures')
                        self.stop_event.wait(.04)
                        continue
                consecutive_failures = 0
                lsl_ts = local_clock()
                mono_ts = time.monotonic()
                # A fast camera is throttled to requested rate; a slow camera is never duplicated.
                if mono_ts - last_accepted >= period * .98:
                    last_accepted = mono_ts
                    if frame.shape[1] != width or frame.shape[0] != height:
                        frame = cv2.resize(frame, (width, height))
                    self.frames_captured += 1
                    try:
                        self.frame_queue.put_nowait((frame, lsl_ts, mono_ts))
                    except queue.Full:
                        self.frames_dropped += 1
                        if self.frames_dropped == 1:
                            self.report('WARNING', 'Camera JPEG writer cannot keep up; frames are being dropped')
                    # Preview is kept at or below VGA.
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    with self.lock:
                        self.last_rgb = rgb
                        self.last_time = mono_ts
                frame = None
            self.ended_monotonic = time.monotonic()
        except Exception as exc:
            self._report_error(str(exc))
        finally:
            self.ended_monotonic = self.ended_monotonic or time.monotonic()
            if cap is not None:
                cap.release()
            # Allow pending encoded frames to flush before stop returns.
            if self.writer_thread is not None:
                try:
                    self.frame_queue.put(None, timeout=5)
                except queue.Full:
                    self._report_error('JPEG writer queue could not drain')
            self.ready.set()
            self.active = False

    def snapshot(self):
        with self.lock:
            return self.last_rgb, self.last_time, self.error

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=12)
            if self.thread.is_alive():
                self._report_error('Capture thread did not stop within 12s')
            else:
                self.thread = None
        if self.writer_thread is not None:
            self.writer_thread.join(timeout=30)
            if self.writer_thread.is_alive():
                self._report_error('JPEG writer did not flush within 30s')
            else:
                self.writer_thread = None
        self.active = bool((self.thread and self.thread.is_alive()) or
                           (self.writer_thread and self.writer_thread.is_alive()))
        if not self.active:
            self.lsl_outlet = None  # Writer normally released it in its finally block.
            self.report('INFO', f'Camera {self.slot} stopped: {self.frames_written} JPEG frames; {self.frames_dropped} queue drops')
