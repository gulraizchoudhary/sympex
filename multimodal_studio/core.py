import csv
import json
import logging
import sys
import traceback
import threading
from datetime import datetime
from pathlib import Path
from pylsl import StreamInfo, StreamOutlet, local_clock

class EventBus:
    """Centralized, thread-safe event delivery and persistent logging.

    Application events are always written to logs/application.log, including Preview
    and startup failures. Formal experiment events are ALSO written to events.log.
    """
    def __init__(self, log_directory=None):
        import queue
        self.events = queue.Queue()
        self.logger = logging.getLogger(f'multimodal.{id(self)}')
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self.handler = None
        directory = Path(log_directory) if log_directory else Path(__file__).resolve().parent / 'logs'
        directory.mkdir(parents=True, exist_ok=True)
        self.application_log = directory / 'application.log'
        self.application_handler = logging.FileHandler(self.application_log, encoding='utf-8')
        self.application_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        self.logger.addHandler(self.application_handler)

    def open_session(self, folder):
        self.close_session()
        self.handler = logging.FileHandler(Path(folder) / 'events.log', encoding='utf-8')
        self.handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        self.logger.addHandler(self.handler)

    def close_session(self):
        if self.handler is not None:
            self.logger.removeHandler(self.handler)
            self.handler.close()
            self.handler = None

    def emit(self, name, status, message):
        line = f'[{name}] {status}: {message}'
        level = logging.ERROR if status == 'ERROR' else (
            logging.WARNING if status in ('WARNING', 'CAUTION', 'DROPPED') else logging.INFO)
        self.logger.log(level, line)
        self.events.put((name, status, message))

    def install_exception_hooks(self):
        """Report otherwise-unhandled main/background-thread errors."""
        original_sys_hook = sys.excepthook
        original_thread_hook = threading.excepthook

        def system_hook(tp, value, tb):
            self.emit('Python', 'ERROR', ''.join(traceback.format_exception(tp, value, tb)).strip())
            original_sys_hook(tp, value, tb)

        def thread_hook(args):
            self.emit('Thread', 'ERROR', ''.join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback)).strip())
            original_thread_hook(args)

        sys.excepthook = system_hook
        threading.excepthook = thread_hook

    def close(self):
        self.close_session()
        self.logger.removeHandler(self.application_handler)
        self.application_handler.close()

class RecorderBase:
    def __init__(self, name, bus):
        self.name, self.bus = name, bus
        self.active = False
        self.failed = False

    def report(self, status, message):
        if status in ('ERROR', 'DROPPED'):
            self.failed = True
        self.bus.emit(self.name, status, message)

    def start(self, folder, **kwargs):
        raise NotImplementedError

    def stop(self):
        self.active = False

class SessionManager:
    def __init__(self, bus):
        self.bus = bus
        self.active = False
        self.recorders = {}
        self.folder = None
        self.mark_file = None
        self.mark_writer = None
        self.marker_outlet = StreamOutlet(StreamInfo('ExperimentMarkers', 'Markers', 1, 0, 'string', 'experiment_markers'))
        self.lock = threading.RLock()

    def start(self, root, participant, selected):
        if self.active:
            return
        name = datetime.now().strftime('Session_%Y%m%d_%H%M%S_%f')
        safe_id = ''.join(c if c.isalnum() or c in '-_' else '_' for c in participant.strip()) or 'Unknown'
        self.folder = Path(root).expanduser().resolve() / f'Participant_{safe_id}' / name
        self.folder.mkdir(parents=True, exist_ok=False)
        self.bus.open_session(self.folder)
        self.mark_file = open(self.folder / 'markers.csv', 'w', newline='', encoding='utf-8')
        self.mark_writer = csv.writer(self.mark_file)
        self.mark_writer.writerow(['lsl_time', 'marker'])
        (self.folder / 'config.json').write_text(json.dumps({'participant': safe_id, 'devices': list(selected), 'settings': {k:v[1] for k,v in selected.items()}, 'started_wall_time': datetime.now().isoformat()}, indent=2, default=str), encoding='utf-8')
        self.active = True
        self.recorders = {}
        for name, (recorder, settings) in selected.items():
            self.recorders[name] = recorder
            try:
                recorder.start(self.folder, **settings)
            except Exception as exc:
                recorder.report('ERROR', f'start failed: {exc!r}')
        self.marker('RECORDING_START')
        self.bus.emit('Session', 'RECORDING', str(self.folder))
        return self.folder

    def marker(self, label):
        with self.lock:
            if not self.active:
                return
            ts = local_clock()
            self.mark_writer.writerow([f'{ts:.9f}', label])
            self.mark_file.flush()
            self.marker_outlet.push_sample([label], timestamp=ts)

    def stop(self):
        if not self.active:
            return
        self.marker('RECORDING_STOP')
        for name, recorder in list(self.recorders.items()):
            try:
                recorder.stop()
            except Exception as exc:
                recorder.report('ERROR', f'stop failed: {exc!r}')
        self.active = False
        self.mark_file.close()
        self.mark_file = None
        self.bus.emit('Session', 'STOPPED', str(self.folder))
        self.bus.close_session()
