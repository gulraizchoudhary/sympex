import csv
import hashlib
import uuid
import json
import logging
from logging.handlers import RotatingFileHandler
import sys
import traceback
import threading
from datetime import datetime
from pathlib import Path
from pylsl import StreamInfo, StreamOutlet, local_clock
from output_names import session_file, checked_token, register_study_block, unregister_study_block

class EventBus:
    """Centralized, thread-safe event delivery and persistent logging.

    Application events use a bounded rotating log. A separate per-session event
    log is optional because it duplicates the application log and markers.csv.
    """
    def __init__(self, log_directory=None, session_event_log=False):
        import queue
        self.events = queue.Queue()
        self.logger = logging.getLogger(f'multimodal.{id(self)}')
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self.handler = None
        self.session_event_log = session_event_log
        directory = Path(log_directory) if log_directory else Path(__file__).resolve().parent / 'logs'
        directory.mkdir(parents=True, exist_ok=True)
        self.application_log = directory / 'application.log'
        self.application_handler = RotatingFileHandler(self.application_log, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
        self.application_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        self.logger.addHandler(self.application_handler)

    def open_session(self, folder):
        self.close_session()
        if not self.session_event_log:
            return
        self.handler = logging.FileHandler(session_file(folder, 'events.log'), encoding='utf-8')
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
        self.marker_source_id = f'IMS_ExperimentMarkers_{uuid.uuid4().hex}'
        marker_info = StreamInfo('IMS_Experiment_Markers', 'Markers', 1, 0, 'string', self.marker_source_id)
        if hasattr(marker_info, 'desc'):
            marker_info.desc().append_child_value('recording_system', 'Intelligent Multimodal Microsurgery Studio (IMMSS)')
            marker_info.desc().append_child('channels').append_child('channel').append_child_value('label', 'EventMarker')
        self.marker_outlet = StreamOutlet(marker_info)
        self.lock = threading.RLock()
        self.session_id = None
        self._session_config = None
        self.study = None

    def start(self, root, participant, selected, readiness_timeout=45, study=None):
        """Start Polar first and require live packets before recording marker.

        Other selected devices remain independent after session start. A missing
        selected Polar is treated as a preflight failure, never silent success.
        """
        if self.active:
            return self.folder
        self.session_id = uuid.uuid4().hex
        self.study = None
        if study is not None:
            centre = checked_token(study['centre'].upper(), 'centre')
            participant_code = checked_token(participant.upper(), 'participant')
            if not participant_code.startswith('P'):
                participant_code = 'P' + participant_code
            visit_number = int(study['visit'])
            block_number = int(study['block'])
            if not (1 <= visit_number <= 99 and 1 <= block_number <= 99):
                raise ValueError('Visit and block must be between 1 and 99')
            phase = checked_token(study['phase'], 'phase')
            visit_code, block_code = f'V{visit_number:02d}', f'B{block_number:02d}'
            root_path = Path(root).expanduser().resolve()
            study_root = root_path if root_path.name == 'SYMPEX_2027' else root_path / 'SYMPEX_2027'
            name = datetime.now().strftime('S%Y%m%dT%H%M%S') + '_' + self.session_id[:8]
            self.folder = study_root / centre / participant_code / f'{visit_code}_{phase}' / name
            self.study = {'study_id':'SYMPEX_2027','centre':centre,'participant':participant_code,
                          'visit':visit_code,'phase':phase,'block':block_code}
            safe_id = participant_code
        else:
            name = datetime.now().strftime('Session_%Y%m%d_%H%M%S_%f') + '_' + self.session_id[:8]
            safe_id = ''.join(c if c.isalnum() or c in '-_' else '_' for c in participant.strip()) or 'Unknown'
            self.folder = Path(root).expanduser().resolve() / f'Participant_{safe_id}' / name
        self.folder.mkdir(parents=True, exist_ok=False)
        if self.study:
            register_study_block(self.folder, self.study['block'])
        self.bus.open_session(self.folder)
        self._session_config = {
            'schema_version': 4,
            'file_naming': 'centre_participant_visit_block_shortsession__original_filename' if self.study else 'participant__session__original_filename',
            'study': self.study,
            'session_id': self.session_id,
            'participant': safe_id,
            'devices': list(selected),
            'settings': {k:v[1] for k,v in selected.items()},
            'started_wall_time': datetime.now().astimezone().isoformat(),
            'file_metadata': 'File identities, hashes and sizes are consolidated in session_manifest.json; no per-file sidecars.',
        }
        (session_file(self.folder, 'config.json')).write_text(json.dumps(self._session_config, indent=2, default=str), encoding='utf-8')
        self.recorders = {}
        try:
            polar = selected.get('Polar H10')
            if polar:
                recorder, settings = polar
                self.bus.emit('Polar H10', 'INFO', 'Preflight: connect and await live HR/ECG/ACC as selected before session marker')
                self.recorders['Polar H10'] = recorder
                recorder.start(self.folder, **settings)
                recorder.wait_ready(ecg=settings.get('ecg', True), acc=settings.get('acc', True), timeout=readiness_timeout)
            # Start the other devices only after Polar readiness has been proven.
            for dev_name, (recorder, settings) in selected.items():
                if dev_name == 'Polar H10':
                    continue
                self.recorders[dev_name] = recorder
                try:
                    recorder.start(self.folder, **settings)
                except Exception as exc:
                    recorder.report('ERROR', f'start failed: {exc!r}')
            self.mark_file = open(session_file(self.folder, 'markers.csv'), 'w', newline='', encoding='utf-8')
            self.mark_writer = csv.writer(self.mark_file)
            self.mark_writer.writerow(['lsl_time', 'marker', 'session_id'])
            self.active = True
            self.marker('RECORDING_START')
            self.bus.emit('Session', 'RECORDING', f'{self.session_id} | {self.folder}')
            return self.folder
        except Exception as exc:
            self.bus.emit('Session','ERROR', f'Preflight/start aborted (no RECORDING_START marker): {exc!r}')
            for rec in self.recorders.values():
                try: rec.stop()
                except Exception as stop_exc: self.bus.emit('Session','WARNING',f'Cleanup: {stop_exc!r}')
            if self.mark_file:
                self.mark_file.close()
            self.mark_file = self.mark_writer = None
            self.active = False
            self._write_manifest(status='ABORTED', reason=str(exc))
            self.bus.close_session()
            unregister_study_block(self.folder)
            raise

    def _write_manifest(self, status, reason=None):
        if self.folder is None:
            return
        items = []
        for path in sorted(self.folder.iterdir()):
            if not path.is_file() or path == session_file(self.folder, 'session_manifest.json'):
                continue
            # Hash files by streaming chunks (can include multi-GB WAV/MP4).
            digest = hashlib.sha256()
            with path.open('rb') as infile:
                for chunk in iter(lambda: infile.read(1024*1024), b''):
                    digest.update(chunk)
            details = {'session_id': self.session_id, 'participant': self._session_config['participant'], 'study': self.study,
                       'session_folder': self.folder.name, 'original_filename': path.name,
                       'file_role': path.suffix.lower().lstrip('.'), 'size_bytes': path.stat().st_size,
                       'sha256': digest.hexdigest(), 'status': status,
                       'created_wall_time': self._session_config['started_wall_time']}
            items.append(details)
        manifest = {'schema_version': 4, 'config': self._session_config,
                    'session_id':self.session_id,'participant':self._session_config['participant'],'study':self.study,
                    'session_folder':self.folder.name,'status':status,
                    'reason':reason,'files':items,
                    'finalized_wall_time':datetime.now().astimezone().isoformat()}
        (session_file(self.folder, 'session_manifest.json')).write_text(json.dumps(manifest,indent=2),encoding='utf-8')

    def marker(self, label):
        with self.lock:
            if not self.active:
                return
            ts = local_clock()
            self.mark_writer.writerow([f'{ts:.9f}', label, self.session_id])
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
        try:
            self._write_manifest('COMPLETED')
        except Exception as exc:
            self.bus.emit('Session', 'ERROR', f'Manifest finalization failed: {exc!r}')
        self.bus.close_session()
