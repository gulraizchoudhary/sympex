"""Optional Windows camera discovery and preview. This module does not record video."""
import sys
import threading
import time
from devices.camera_controls import normalize_settings, apply_camera_settings


def camera_backends(cv2):
    """Try DirectShow and Media Foundation independently, then OpenCV default."""
    candidates = ([('DirectShow', cv2.CAP_DSHOW),
                   ('Media Foundation', cv2.CAP_MSMF)] if sys.platform == 'win32' else [])
    candidates.append(('Default', cv2.CAP_ANY))
    return candidates


def open_camera(index, *, report=None, attempts=30, settings=None):
    """Return (capture, frame, backend) or (None,None,None); log failure reasons."""
    import cv2
    for name, backend in camera_backends(cv2):
        cap = None
        try:
            cap = cv2.VideoCapture(index, backend)
            if not cap.isOpened():
                if report: report(f'index {index} / {name}: could not open')
                cap.release()
                continue
            # Some Windows webcams need warm-up and/or a conservative format.
            # DirectShow/MSMF enumerate different indexes on some systems.
            formats = ([('requested', (int(settings['width']), int(settings['height'])))]
                       if settings else []) + [('default', None), ('640x480', (640, 480)),
                       ('640x480 MJPG', (640, 480, 'MJPG'))]
            for format_label, setting in formats:
                if setting:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, setting[0])
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, setting[1])
                    if len(setting) == 3:
                        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*setting[2]))
                if settings:
                    apply_camera_settings(cap, cv2, settings, report=report)
                for _ in range(attempts):
                    ok, frame = cap.read()
                    if ok and frame is not None and getattr(frame, 'size', 0):
                        if report: report(f'index {index} / {name}: frames OK ({format_label}, {frame.shape[1]}x{frame.shape[0]})')
                        return cap, frame, name
                    time.sleep(.07)
            if report: report(f'index {index} / {name}: device opened, but no valid frames with tested formats')
        except Exception as exc:
            if report: report(f'index {index} / {name}: {type(exc).__name__}: {exc}')
        if cap is not None:
            cap.release()
    return None, None, None


def discover_cameras(max_index=10, report=None):
    """Find working camera indices. Windows Device Manager entries may not be capture inputs."""
    found = []
    for index in range(max_index):
        cap, frame, backend = open_camera(index, report=report)
        if cap is not None:
            found.append((index, f'Camera {index} · {frame.shape[1]}×{frame.shape[0]} ({backend})'))
            if report: report(f'index {index}: available via {backend}')
            cap.release()
    return found


class CameraPreview:
    """Background VideoCapture; UI requests latest RGB image safely."""
    def __init__(self, slot, bus):
        self.slot, self.bus = slot, bus
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.last_rgb = None
        self.last_time = 0
        self.error = None
        self.device_index = None

    def start(self, index, settings=None):
        if self.thread and self.thread.is_alive():
            raise RuntimeError('Stop this camera preview first')
        self.device_index = index
        self.settings = normalize_settings(settings) if settings is not None else None
        self.last_rgb, self.last_time, self.error = None, 0, None
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name=f'CameraPreview{self.slot}', daemon=True)
        self.thread.start()

    def _run(self):
        import cv2
        cap = None
        try:
            cap, first, backend = open_camera(
                self.device_index,
                report=lambda msg: self.bus.emit(f'Camera {self.slot}', 'INFO', msg),
                settings=self.settings,
            )
            if cap is None:
                raise RuntimeError(f'Camera {self.device_index} unavailable via DirectShow, Media Foundation, or default backend')
            failures = 0
            self.bus.emit(f'Camera {self.slot}', 'PREVIEW', f'Opened camera {self.device_index} via {backend}')
            def publish(frame):
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                with self.lock:
                    self.last_rgb = rgb
                    self.last_time = time.monotonic()
            publish(first)
            while not self.stop_event.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    failures += 1
                    if failures >= 15:
                        raise RuntimeError('Camera stopped delivering frames')
                    self.stop_event.wait(0.04)
                    continue
                failures = 0
                publish(frame)
        except Exception as exc:
            self.error = str(exc)
            self.bus.emit(f'Camera {self.slot}', 'ERROR', f'Camera preview failed: {exc!r}')
        finally:
            if cap is not None:
                cap.release()
            self.bus.emit(f'Camera {self.slot}', 'INFO', 'Preview capture stopped')

    def snapshot(self):
        with self.lock:
            return self.last_rgb, self.last_time, self.error

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                self.bus.emit(f'Camera {self.slot}', 'WARNING', 'Preview thread has not exited yet')
            else:
                self.thread = None
