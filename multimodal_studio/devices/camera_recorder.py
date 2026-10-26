"""Independent video capture + MP4 writer for a user-selected camera.

Video timestamps are host LSL-clock acquisition estimates, not hardware exposure time.
"""
import csv
import json
import threading
import time
from pathlib import Path
from pylsl import local_clock
from core import RecorderBase
from devices.camera_preview import open_camera


class CameraRecorder(RecorderBase):
    def __init__(self, slot, bus):
        super().__init__(f'Camera {slot}', bus)
        self.slot = slot
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.last_rgb = None
        self.last_time = 0.0
        self.error = None
        self.device_index = None
        self.frames_written = 0
        self.ready = threading.Event()

    def start(self, folder, index, fps=25.0):
        if self.thread and self.thread.is_alive():
            raise RuntimeError('Camera is already active')
        self.device_index = int(index)
        self.error = None
        self.failed = False
        self.frames_written = 0
        self.stop_event.clear()
        self.ready.clear()
        with self.lock:
            self.last_rgb = None
            self.last_time = 0.0
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        self.thread = threading.Thread(target=self._run, args=(path, float(fps)),
                                       name=f'CameraRecorder{self.slot}', daemon=True)
        self.thread.start()
        # Wait for capture/writer initialization, not for the whole recording.
        if not self.ready.wait(timeout=15):
            self.report('WARNING', 'Camera initialization still pending; check status/logs')
        if self.error:
            raise RuntimeError(self.error)

    def _run(self, folder, requested_fps):
        import cv2
        cap = None
        writer = None
        frames_csv = None
        try:
            cap, first, backend = open_camera(
                self.device_index,
                report=lambda msg: self.report('INFO', msg),
                attempts=12,
            )
            if cap is None:
                raise RuntimeError(f'Cannot open camera index {self.device_index}')
            h, w = first.shape[:2]
            device_fps = cap.get(cv2.CAP_PROP_FPS)
            fps = float(device_fps) if device_fps and 1 <= device_fps <= 120 else requested_fps
            if fps <= 0:
                fps = 25.0
            name = f'camera_{self.slot}'
            video_path = folder / f'{name}.mp4'
            writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*'mp4v'), fps, (w,h))
            if not writer.isOpened():
                raise RuntimeError(f'Could not create MP4 writer: {video_path}')
            frames_csv = open(folder / f'{name}_frames.csv', 'w', newline='', encoding='utf-8')
            csv_writer = csv.writer(frames_csv)
            csv_writer.writerow(['frame_index','lsl_capture_time_estimate','monotonic_capture_time','width','height'])
            (folder / f'{name}_metadata.json').write_text(json.dumps({
                'device_index':self.device_index,'backend':backend,'width':w,'height':h,
                'writer_fps':fps,'timestamp_semantics':'host-side timestamp after cap.read, not exposure timestamp',
                'video':video_path.name},indent=2),encoding='utf-8')
            self.active = True
            self.report('RECORDING',f'Video capture started: {video_path.name}; {w}x{h}, writer {fps:g} fps ({backend})')
            self.ready.set()
            failures = 0
            frame = first
            while not self.stop_event.is_set():
                if frame is None:
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        failures += 1
                        if failures >= 15:
                            raise RuntimeError('15 consecutive frame read failures; camera disconnected or stalled')
                        self.stop_event.wait(.04)
                        continue
                failures = 0
                capture_lsl = local_clock()
                capture_mono = time.monotonic()
                if frame.shape[:2] != (h,w):
                    self.report('WARNING', f'Unexpected frame dimension {frame.shape[:2]}; frame skipped')
                else:
                    writer.write(frame)
                    csv_writer.writerow([self.frames_written, f'{capture_lsl:.9f}', f'{capture_mono:.9f}', w, h])
                    self.frames_written += 1
                    # Downsize *preview* only to bound CPU/memory usage.
                    rgb = cv2.cvtColor(cv2.resize(frame,(min(w,640),round(h*min(w,640)/w))),cv2.COLOR_BGR2RGB)
                    with self.lock:
                        self.last_rgb=rgb
                        self.last_time=capture_mono
                frame=None
                if self.frames_written % 125 == 0:
                    frames_csv.flush()
        except Exception as exc:
            self.error = str(exc)
            self.report('ERROR',f'Video recording interrupted: {exc!r}; other devices continue')
        finally:
            self.ready.set()
            self.active = False
            if frames_csv is not None:
                try: frames_csv.close()
                except Exception as exc: self.report('ERROR',f'Frame CSV close failed: {exc!r}')
            if writer is not None:
                writer.release()
            if cap is not None:
                cap.release()
            self.report('INFO',f'Camera stopped; {self.frames_written} frames saved')

    def snapshot(self):
        with self.lock:
            return self.last_rgb, self.last_time, self.error

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=8)
            if self.thread.is_alive():
                self.report('WARNING','Camera thread did not stop within 8s; file may not be finalized yet')
            else:
                self.thread=None
        self.active=False
