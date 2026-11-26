# -*- coding: utf-8 -*-
"""
Updated on Wed Nov 26 09:35:00 2026

@author: Gulraiz
"""

import csv
import hashlib
import socket
import uuid
import json
import logging
from logging.handlers import RotatingFileHandler
import sys
import traceback
import threading
import time
from datetime import datetime
from pathlib import Path
from pylsl import StreamInfo, StreamOutlet, local_clock
from output_names import session_file, checked_token, unregister_study_block
from block_management import load_block_state, validate_max_blocks
from recording_integrity import assess_device, await_live_data, summarize_integrity

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
        self.current_block = 1
        self.block_running = False
        self.block_ended = False
        self.completed_blocks = set()
        self.metadata_outlet = None
        self.device_readiness = {}
        self.device_integrity = {}

    def start(self, root, participant, selected, readiness_timeout=45, study=None):
        """Start Polar first and require live packets before recording marker.

        Other selected devices remain independent after session start. A missing
        selected Polar is treated as a preflight failure, never silent success.
        """
        if self.active:
            return self.folder
        if 'Polar H10' in selected and 'Polar Verity Sense' in selected:
            raise ValueError('Polar H10 and Verity Sense are mutually exclusive')
        self.session_id = uuid.uuid4().hex
        self.metadata_outlet = None
        self.device_readiness = {}
        self.device_integrity = {}
        self.study = None
        self.current_block = 1
        self.block_running = False
        self.block_ended = False
        self.completed_blocks = set()
        if study is not None:
            centre = checked_token(study['centre'].upper(), 'centre')
            participant_code = checked_token(participant.upper(), 'participant')
            if not participant_code.startswith('P'):
                participant_code = 'P' + participant_code
            visit_number = int(study['visit'])
            if not 1 <= visit_number <= 99:
                raise ValueError('Visit must be between 1 and 99')
            phase = checked_token(study['phase'], 'phase')
            maximum = validate_max_blocks(study.get('max_trials', study.get('max_blocks', 5)))
            previous = load_block_state(root, centre, participant_code, visit_number, maximum)
            if not previous.can_start:
                raise ValueError('All visit trials are complete; cannot start another recording')
            self.current_block = previous.current
            self.completed_blocks = set(previous.completed)
            self.block_running = False
            self.block_ended = False
            visit_code = f'V{visit_number:02d}'
            root_path = Path(root).expanduser().resolve()
            study_root = root_path if root_path.name == 'SYMPEX_2027' else root_path / 'SYMPEX_2027'
            name = datetime.now().strftime('S%Y%m%dT%H%M%S') + '_' + self.session_id[:8]
            self.folder = study_root / centre / participant_code / f'{visit_code}_{phase}' / name
            self.study = {'study_id':'SYMPEX_2027','centre':centre,'participant':participant_code,
                          'visit':visit_code,'phase':phase,'max_blocks':maximum,
                          'recording_scope':'visit','max_trials':maximum,
                          'training_session':visit_number, 'trial_unit':'one_tympanostomy_procedure',
                          'first_trial':f'T{self.current_block:02d}'}
            safe_id = participant_code
        else:
            name = datetime.now().strftime('Session_%Y%m%d_%H%M%S_%f') + '_' + self.session_id[:8]
            safe_id = ''.join(c if c.isalnum() or c in '-_' else '_' for c in participant.strip()) or 'Unknown'
            self.folder = Path(root).expanduser().resolve() / f'Participant_{safe_id}' / name
        self.folder.mkdir(parents=True, exist_ok=False)
        self.bus.open_session(self.folder)
        # Portable, visit-scoped organization record. Paths are relative to the
        # study root so neither the LSL broadcast nor exported metadata leaks
        # the recording computer's absolute path or Windows username.
        computer_name = socket.gethostname()
        data_organization = None
        if self.study:
            recording_prefix = f'{centre}_{participant_code}_{visit_code}_S{self.session_id[:8]}__'
            data_organization = {
                'schema_version': 1,
                'computer_name': computer_name,
                'study_root': 'SYMPEX_2027',
                'centre': centre,
                'participant': participant_code,
                'visit': visit_code,
                'phase': phase,
                'session_folder': name,
                'relative_session_path': '/'.join((centre, participant_code, f'{visit_code}_{phase}', name)),
                'filename_prefix': recording_prefix,
                'recording_scope': 'visit',
                'trial_storage': 'timestamped_events_not_separate_recordings',
                'max_trials': maximum,
                'metadata_levels': ['participant', 'training_session', 'training_trial'],
                'metadata_file': recording_prefix + 'metadata.json',
                'events_file': recording_prefix + 'events.csv',
                'manifest_file': recording_prefix + 'session_manifest.json',
                'modality_folders': {
                    'physiology': 'physiology/',
                    'eye_tracker': 'eye_tracker/',
                    'video': 'video/',
                    'audio': 'audio/',
                },
                'folder_creation': 'lazy_when_enabled_or_data_generated',
                'enabled_devices': list(selected),
            }
        polar_selection = next((n for n in ('Polar H10', 'Polar Verity Sense') if n in selected), None)
        polar_options = selected[polar_selection][1] if polar_selection else None
        polar_ble = polar_options.get('ble_device') if polar_options else None
        polar_channels = []
        if polar_selection:
            polar_channels.append('hr')
            polar_channels.extend(['ecg'] if polar_selection == 'Polar H10' and polar_options.get('ecg') else [])
            polar_channels.extend(['ppg'] if polar_selection == 'Polar Verity Sense' and polar_options.get('ppg') else [])
            if polar_options.get('acc'):
                polar_channels.append('acc')
        physiological_sensor = {
            'model': polar_selection,
            'device_identifier': str(getattr(polar_ble, 'address', '') or getattr(polar_ble, 'name', '') or polar_ble) if polar_ble else None,
            'requested_channels': polar_channels,
            'selection_policy': 'H10_or_Verity_exclusive',
        }
        if data_organization is not None:
            data_organization['physiological_sensor'] = physiological_sensor
        self._session_config = {
            'schema_version': 7,
            'physiological_sensor': physiological_sensor,
            'file_naming': 'centre_participant_visit_shortsession__original_filename' if self.study else 'participant__session__original_filename',
            'data_layout': 'modality_folders_v1' if self.study else 'flat_legacy',
            'data_organization': data_organization,
            'metadata_broadcast': {'protocol': 'LSL', 'stream_name': 'IMS_Session_Metadata', 'type': 'SessionMetadata', 'format': 'JSON', 'scope': 'local_network'} if self.study else None,
            'study': self.study,
            'session_id': self.session_id,
            'computer_name': computer_name,
            'participant': safe_id,
            'devices': list(selected),
            'participant_metadata': {'participant_id': safe_id, 'centre': self.study['centre'] if self.study else None},
            'training_session_metadata': {'visit_id': self.study['visit'], 'training_session': self.study['training_session'], 'phase': self.study['phase']} if self.study else None,
            'training_trials': [],
            'settings': {k:v[1] for k,v in selected.items()},
            'started_wall_time': datetime.now().astimezone().isoformat(),
            'file_metadata': 'File identities, hashes and sizes are consolidated in session_manifest.json; no per-file sidecars.',
        }
        (session_file(self.folder, 'config.json')).write_text(json.dumps(self._session_config, indent=2, default=str), encoding='utf-8')
        if self.study:
            self._broadcast_session_metadata()
        self.recorders = {}
        try:
            polar_name = next((name for name in ('Polar H10','Polar Verity Sense') if name in selected), None)
            if polar_name:
                recorder, settings = selected[polar_name]
                self.bus.emit(polar_name, 'INFO', 'Preflight: connect and await live physiological packets')
                self.recorders[polar_name] = recorder
                recorder.start(self.folder, **settings)
                if polar_name == 'Polar H10':
                    recorder.wait_ready(ecg=settings.get('ecg', True), acc=settings.get('acc', True), timeout=readiness_timeout)
                else:
                    recorder.wait_ready(ppg=settings.get('ppg', True), acc=settings.get('acc', True), timeout=readiness_timeout)
            # Start the other devices only after Polar readiness has been proven.
            for dev_name, (recorder, settings) in selected.items():
                if dev_name in ('Polar H10','Polar Verity Sense'):
                    continue
                self.recorders[dev_name] = recorder
                recorder.start(self.folder, **settings)
            # Preflight checks inspect *live acquisition*, not just device selection.
            # SeeTrue calibration remains entirely in SeeTrue Station.
            for dev_name, recorder in self.recorders.items():
                if dev_name in ('Polar H10','Polar Verity Sense'):
                    result = assess_device(dev_name, recorder, self.folder, stage='preflight')
                else:
                    result = await_live_data(dev_name, recorder, self.folder, timeout=8.0)
                self.device_readiness[dev_name] = result
                self.bus.emit(dev_name, 'READY' if result['ready'] else 'ERROR', result['message'])
                if not result['ready']:
                    raise RuntimeError(f'{dev_name} preflight failed: {result["message"]}')
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
            self.device_integrity = {name: assess_device(name, rec, self.folder, stage='final')
                                     for name, rec in self.recorders.items()}
            self._write_manifest(status='ABORTED', reason=str(exc))
            self.metadata_outlet = None
            self.bus.close_session()
            unregister_study_block(self.folder)
            raise

    def _broadcast_session_metadata(self):
        """Advertise study organization over LSL, matching metadata.json.

        The JSON is both in the stream descriptor (available to later LSL
        discoverers) and emitted as one sample for already-connected listeners.
        A network/broadcast failure must not discard the local session files.
        """
        payload = json.dumps(self._session_config['data_organization'], ensure_ascii=True, sort_keys=True)
        try:
            info = StreamInfo('IMS_Session_Metadata', 'SessionMetadata', 1, 0,
                              'string', f'IMS_SessionMetadata_{self.session_id}')
            if hasattr(info, 'desc'):
                descriptor = info.desc()
                descriptor.append_child_value('recording_system', 'IMMSS')
                descriptor.append_child_value('session_id', self.session_id)
                descriptor.append_child_value('computer_name', self._session_config['computer_name'])
                descriptor.append_child_value('data_organization_json', payload)
            self.metadata_outlet = StreamOutlet(info)
            self.metadata_outlet.push_sample([payload], timestamp=local_clock())
            self.bus.emit('Session Metadata', 'INFO', 'Published data organization via IMS_Session_Metadata (LSL)')
        except Exception as exc:
            self.metadata_outlet = None
            self.bus.emit('Session Metadata', 'WARNING', f'LSL organization broadcast failed: {exc!r}; metadata.json retained')

    def _trial_records(self):
        """Summarize trial markers from the existing event log; no extra files."""
        if not self.study or self.folder is None:
            return []
        from output_names import find_session_file
        event_file = find_session_file(self.folder, 'markers.csv')
        rows = {}
        if not event_file.is_file():
            return []
        with event_file.open(encoding='utf-8', newline='') as stream:
            for marker in csv.DictReader(stream):
                label = marker.get('marker', '')
                if len(label) < 7 or label[0] not in 'TB' or not label[1:3].isdigit():
                    continue
                index = int(label[1:3])
                if not 1 <= index <= self.study['max_trials']:
                    continue
                action = label[4:]
                if action not in ('START', 'END', 'INTERRUPTED'):
                    continue
                record = rows.setdefault(index, {
                    'trial_id': f'T{index:02d}', 'trial_number': index,
                    'participant_id': self.study['participant'],
                    'training_session': self.study['training_session'],
                    'visit_id': self.study['visit'], 'start_lsl_time': None,
                    'end_lsl_time': None, 'status': 'incomplete'})
                if action == 'START':
                    record['start_lsl_time'] = marker.get('lsl_time')
                    record['status'] = 'started'
                elif action == 'END':
                    record['end_lsl_time'] = marker.get('lsl_time')
                    record['status'] = 'completed'
                else:
                    record['end_lsl_time'] = marker.get('lsl_time')
                    record['status'] = 'interrupted'
        return [rows[i] for i in sorted(rows)]

    def _write_manifest(self, status, reason=None):
        if self.folder is None:
            return
        items = []
        for path in sorted(self.folder.rglob('*')):
            if not path.is_file() or path == session_file(self.folder, 'session_manifest.json'):
                continue
            # Hash files by streaming chunks (can include multi-GB WAV/MP4).
            digest = hashlib.sha256()
            with path.open('rb') as infile:
                for chunk in iter(lambda: infile.read(1024*1024), b''):
                    digest.update(chunk)
            details = {'session_id': self.session_id, 'participant': self._session_config['participant'], 'study': self.study,
                       'session_folder': self.folder.name, 'relative_path': path.relative_to(self.folder).as_posix(), 'original_filename': path.name,
                       'file_role': path.suffix.lower().lstrip('.'), 'size_bytes': path.stat().st_size,
                       'sha256': digest.hexdigest(), 'status': status,
                       'created_wall_time': self._session_config['started_wall_time']}
            items.append(details)
        manifest = {'schema_version': 7, 'config': self._session_config,
                    'session_id':self.session_id,'participant':self._session_config['participant'],'study':self.study,
                    'session_folder':self.folder.name,'status':status,
                    'reason':reason,'files':items,
                    'trial_records': self._trial_records(),
                    'trials': {'completed':sorted(self.completed_blocks),
                               'current':self.current_block if self.study else None,
                               'active':self.block_running if self.study else False,
                               'visit_complete':bool(self.study and len(self.completed_blocks) == self.study['max_trials'])},
                    'device_readiness': self.device_readiness,
                    'recording_integrity': summarize_integrity(self.device_readiness, self.device_integrity),
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

    def start_block(self):
        with self.lock:
            if not self.active or not self.study:
                raise RuntimeError('Start a SYMPEX recording before a trial')
            if self.block_running or self.block_ended:
                raise ValueError('Current trial already started or ended')
            if self.current_block in self.completed_blocks:
                raise ValueError('Current trial is already completed')
            if self.current_block > self.study['max_trials']:
                raise ValueError('Maximum number of trials reached')
            self.marker(f'T{self.current_block:02d}_START')
            self.block_running = True
            return self.current_block

    def end_block(self):
        with self.lock:
            if not self.active or not self.block_running:
                raise ValueError('No active trial to end')
            self.marker(f'T{self.current_block:02d}_END')
            self.block_running = False
            self.block_ended = True
            self.completed_blocks.add(self.current_block)
            return self.current_block

    def next_block(self):
        with self.lock:
            if not self.active or not self.study or not self.block_ended:
                raise ValueError('End the current trial before advancing')
            if self.current_block >= self.study['max_trials']:
                raise ValueError('Maximum number of trials reached')
            self.current_block += 1
            self.block_ended = False
            self.marker(f'T{self.current_block:02d}_READY')
            return self.current_block

    def stop(self):
        if not self.active:
            return
        if self.study and self.block_running:
            self.marker(f'T{self.current_block:02d}_INTERRUPTED')
            self.block_running = False
        self.marker('RECORDING_STOP')
        for name, recorder in list(self.recorders.items()):
            try:
                recorder.stop()
            except Exception as exc:
                recorder.report('ERROR', f'stop failed: {exc!r}')
        self.active = False
        self.mark_file.close()
        self.mark_file = None
        self.device_integrity = {name: assess_device(name, rec, self.folder, stage='final')
                                 for name, rec in self.recorders.items()}
        summary = summarize_integrity(self.device_readiness, self.device_integrity)
        # A normal user stop is not evidence that every selected channel succeeded.
        status = 'COMPLETED' if summary['overall'] == 'PASS' else 'PARTIAL'
        for name, result in self.device_integrity.items():
            self.bus.emit(name, 'INFO' if result['ok'] else 'WARNING',
                          f'Integrity: {result["message"]}')
        self.bus.emit('Session', 'STOPPED', f'{self.folder} | integrity={summary["overall"]}')
        try:
            if self.study:
                self._session_config['training_trials'] = self._trial_records()
                session_file(self.folder, 'config.json').write_text(
                    json.dumps(self._session_config, indent=2, default=str), encoding='utf-8')
            self._write_manifest(status)
        except Exception as exc:
            self.bus.emit('Session', 'ERROR', f'Manifest finalization failed: {exc!r}')
        self.metadata_outlet = None
        self.bus.close_session()
