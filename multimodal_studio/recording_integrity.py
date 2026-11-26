# Author: Gulraiz
"""Live device checks and per-channel integrity summaries (no extra output files).

These checks verify acquisition, not eye-tracker calibration, video exposure
synchrony, sensor accuracy, or alignment of hardware clocks.
"""
import time
from pathlib import Path
from output_names import session_file


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def assess_device(name, recorder, folder, stage='preflight'):
    """Produce a JSON-serializable, conservative status for selected hardware."""
    failed = bool(getattr(recorder, 'failed', False))
    active = bool(getattr(recorder, 'active', False))
    stats = {}
    problems = []
    if failed:
        problems.append('recorder reported an acquisition error or dropped data')
    if stage == 'preflight' and not active:
        problems.append('recorder is not active')
    if stage == 'final' and active:
        problems.append('recorder still active after stop (possible incomplete file)')

    if hasattr(recorder, 'frames_written'):
        stats['frames_written'] = recorder.frames_written
        stats['frames_captured'] = getattr(recorder, 'frames_captured', recorder.frames_written)
        stats['frames_dropped_writer_queue'] = getattr(recorder, 'frames_dropped', 0)
        stats['lsl_publish_errors'] = getattr(recorder, 'lsl_publish_errors', 0)
        stats['actual_capture_fps_estimate'] = getattr(recorder, 'actual_capture_fps', 0.0)
        stats['capture_duration_seconds'] = getattr(recorder, 'capture_duration_seconds', 0.0)
        if (stage == 'final' and stats['capture_duration_seconds'] >= 5 and
                stats['actual_capture_fps_estimate'] < 0.9 * getattr(recorder, 'camera_settings', {}).get('fps', 30)):
            problems.append('actual camera capture rate below 90% of configured FPS')
        if not _positive(recorder.frames_written):
            problems.append('no camera frames written')
        if getattr(recorder, 'error', None):
            problems.append(f'camera error: {recorder.error}')
        if stats['frames_dropped_writer_queue']:
            problems.append('camera JPEG writer queue dropped frames')
        if stats['lsl_publish_errors']:
            problems.append('camera LSL frame notifications failed')
        if stage == 'final':
            index = session_file(folder, f'camera_{recorder.slot}_frames.csv')
            frame_folder = session_file(folder, f'camera_{recorder.slot}_frames')
            stats['frame_index_exists'] = index.is_file() and index.stat().st_size > 0
            stats['frame_directory_exists'] = frame_folder.is_dir()
            stats['jpg_count'] = (sum(1 for _ in frame_folder.glob('*.jpg'))
                                  if frame_folder.is_dir() else 0)
            if not stats['frame_index_exists']:
                problems.append('camera frame timestamp index missing or empty')
            if stats['jpg_count'] != recorder.frames_written:
                problems.append('JPEG frame file count differs from saved frame count')
    elif hasattr(recorder, 'total_frames_received'):
        stats['frames_received'] = recorder.total_frames_received
        stats['samples_saved'] = getattr(recorder, 'count', 0)
        stats['queue_drops'] = getattr(recorder, 'lost_blocks', 0)
        stats['lsl_drops'] = getattr(recorder, 'lsl_drops', 0)
        stats['callback_errors'] = getattr(recorder, 'callback_errors', 0)
        stats['writer_failed'] = bool(getattr(recorder, '_writer_failed', False))
        if not _positive(recorder.total_frames_received):
            problems.append('no microphone callbacks received')
        if stage == 'final' and not _positive(recorder.count):
            problems.append('no audio samples saved')
        if stats['queue_drops'] or stats['callback_errors'] or stats['writer_failed']:
            problems.append('audio data loss or writer error')
    elif hasattr(recorder, 'samples'):
        stats['samples_saved'] = recorder.samples
        stats['stream_interruptions'] = getattr(recorder, 'stream_interruptions', 0)
        if stats['stream_interruptions']:
            problems.append('network LSL stream interrupted')
        if not _positive(recorder.samples):
            problems.append('no network LSL samples received')
    elif hasattr(recorder, 'ready_events'):
        stats['received_channels'] = sorted(k for k,v in recorder.ready_events.items() if v.is_set())
        if 'hr' not in stats['received_channels']:
            problems.append('no Polar HR packets received')
        if hasattr(recorder, 'sample_counts'):
            stats['samples_by_channel'] = dict(recorder.sample_counts)
            required = ['hr']
            if getattr(recorder, '_want_ppg', False):
                required.append('ppg')
            if getattr(recorder, '_want_acc', False):
                required.append('acc')
            if stage == 'final':
                for channel in required:
                    if not _positive(recorder.sample_counts.get(channel, 0)):
                        problems.append(f'no Verity {channel.upper()} samples saved')
    # Unknown/custom recorders: only generic active/error status can be checked.
    ok = not problems
    return {'ok':ok,'ready':ok if stage=='preflight' else None,
            'status':'PASS' if ok else 'FAIL', 'stage':stage,
            'message':'; '.join(problems) if problems else 'live data confirmed' if stage=='preflight' else 'no detected acquisition errors',
            'metrics':stats}


def await_live_data(name, recorder, folder, timeout=8.0):
    deadline = time.monotonic() + max(0, float(timeout))
    result = assess_device(name, recorder, folder, stage='preflight')
    while not result['ready'] and time.monotonic() < deadline:
        if getattr(recorder, 'failed', False) or getattr(recorder, 'error', None):
            break
        if not getattr(recorder, 'active', False) and getattr(recorder, 'ready', None) is not None:
            ready_event = recorder.ready
            if hasattr(ready_event, 'is_set') and ready_event.is_set():
                break
        time.sleep(0.1)
        result = assess_device(name, recorder, folder, stage='preflight')
    return result


def summarize_integrity(readiness, final):
    issues = [name for name, result in final.items() if not result.get('ok')]
    issues += [name for name, result in readiness.items() if not result.get('ready') and name not in issues]
    return {'overall':'PASS' if not issues else 'PARTIAL',
            'devices': {name: {'preflight':readiness.get(name), 'final':final.get(name)}
                        for name in sorted(set(readiness) | set(final))},
            'devices_with_issues':issues,
            'note':'Software-level checks only; SeeTrue Station handles calibration. Hardware synchronization must be validated separately.'}
