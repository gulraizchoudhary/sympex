"""Windows-aware camera discovery and preview; does not record video.

Author: Gulraiz
"""
import sys
import threading
import time
from contextlib import contextmanager
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


_DISCOVERY_LOG_LOCK = threading.RLock()


@contextmanager
def _quiet_opencv_discovery(cv2):
    """Silence expected missing-index warnings *only* while scanning.

    OpenCV logging is process-global. This context always restores its former
    level; do not use it around actual preview or recording operations.
    """
    with _DISCOVERY_LOG_LOCK:
        getter = getattr(cv2, 'getLogLevel', None)
        setter = getattr(cv2, 'setLogLevel', None)
        old_level = None
        if callable(getter) and callable(setter):
            try:
                old_level = getter()
                setter(0)  # LOG_LEVEL_SILENT; only for expected discovery failures
            except Exception:
                old_level = None
        try:
            yield
        finally:
            if old_level is not None:
                try:
                    setter(old_level)
                except Exception:
                    pass


def _windows_camera_indices(max_index, report=None):
    """Enumerate DirectShow capture devices when pygrabber is available.

    Device enumeration is optional. OpenCV's index mapping may differ across
    DSHOW and MSMF, so even an enumerated candidate must pass a read test.
    """
    if sys.platform != 'win32':
        return None
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
    except (ImportError, OSError, RuntimeError, AttributeError) as exc:
        if report:
            report(f'Windows camera enumeration unavailable ({type(exc).__name__}); using bounded index probe')
        return None
    except Exception as exc:
        if report:
            report(f'Windows camera enumeration failed ({type(exc).__name__}); using bounded index probe')
        return None
    if report:
        report(f'Windows DirectShow enumeration: {len(names)} capture device(s)')
    return list(range(min(len(names), max_index)))


def _probe_camera(index, cv2, backend, attempts=3):
    """Short, single-backend discovery probe, always releasing its handle."""
    cap = None
    try:
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            return None
        for _ in range(attempts):
            ok, frame = cap.read()
            if ok and frame is not None and getattr(frame, 'size', 0):
                return frame
            time.sleep(.025)
    except Exception:
        pass
    finally:
        if cap is not None:
            cap.release()
    return None


def discover_cameras(max_index=10, report=None):
    """List only accessible cameras without flooding the OpenCV console.

    Windows: enumerate DirectShow devices if pygrabber is installed; otherwise
    probe indices with a stop after two consecutive misses. Use one
    backend per candidate instead of trying DSHOW, MSMF, and CAP_ANY each time.
    Use camera_diagnostics.py for deliberately exhaustive troubleshooting.
    """
    import cv2
    max_index = max(0, int(max_index))
    if not max_index:
        return []
    indices = _windows_camera_indices(max_index, report=report)
    enumerated = indices is not None
    if not enumerated:
        indices = range(max_index)
    backends = camera_backends(cv2)
    backend_name, backend = backends[0]
    found = []
    with _quiet_opencv_discovery(cv2):
        for candidate_name, candidate_backend in ([backends[0]] +
                ([backends[1]] if sys.platform == 'win32' and len(backends) > 1 else [])):
            if found:
                break  # No need to try a second backend when DSHOW works.
            consecutive_misses = 0
            for index in indices:
                frame = _probe_camera(index, cv2, candidate_backend)
                if frame is None:
                    if not enumerated:
                        consecutive_misses += 1
                        if consecutive_misses >= 2:
                            if report:
                                report(f'Camera scan stopped after consecutive unavailable indices (last={index})')
                            break
                    elif report:
                        report(f'Enumerated camera index {index} cannot deliver frames via {candidate_name}')
                    continue
                consecutive_misses = 0
                found.append((index, f'Camera {index} · {frame.shape[1]}×{frame.shape[0]} ({candidate_name})'))
    if report:
        report(f'Camera discovery: {len(found)} usable camera(s)')
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
