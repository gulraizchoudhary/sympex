"""Pair completed LabRecorder XDF files with IMMSS sessions.

Author: Gulraiz

LabRecorder is operated independently; this module never starts or stops it.
No XDF is selected by 'newest file' heuristics. Auto-pairing requires the
immutable session UUID (or its short ID) in the basename. Manual selection
is available for the default LabRecorder filenames.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from output_names import find_session_file, session_file, session_prefix


class XDFPairingError(RuntimeError):
    """An XDF cannot be safely associated with the intended session."""


def _atomic_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('w', encoding='utf-8') as fp:
            json.dump(obj, fp, indent=2, ensure_ascii=False, default=str)
            fp.flush()
            os.fsync(fp.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def expected_xdf_name(session_folder: Path) -> str:
    return session_prefix(session_folder) + 'lsl.xdf'


def _session_id(session_folder: Path) -> str:
    metadata_path = find_session_file(session_folder, 'config.json')
    if not metadata_path.is_file():
        raise XDFPairingError('Session metadata.json is missing')
    data = json.loads(metadata_path.read_text(encoding='utf-8'))
    session_id = data.get('session_id')
    if not isinstance(session_id, str) or len(session_id) < 8:
        raise XDFPairingError('Session metadata has no valid session_id')
    return session_id


def find_matching_xdf(staging_folder: Path, session_folder: Path) -> Path | None:
    """Never guess by modification time; require exactly one ID-labelled XDF."""
    staging_folder = Path(staging_folder).expanduser()
    if not staging_folder.is_dir():
        return None
    sid = _session_id(session_folder).lower()
    token = sid[:8]
    matches = [p for p in staging_folder.glob('*.xdf') if p.is_file() and
               (sid in p.stem.lower() or token in p.stem.lower())]
    if len(matches) > 1:
        raise XDFPairingError('Multiple XDF files match this session; select the correct one manually')
    return matches[0] if matches else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for data in iter(lambda: file.read(4 * 1024 * 1024), b''):
            digest.update(data)
    return digest.hexdigest()


def _file_stable(path: Path, pause: float) -> int:
    try:
        before = path.stat()
        if before.st_size < 8:
            raise XDFPairingError('XDF is empty or too small')
        if pause:
            time.sleep(pause)
        after = path.stat()
    except FileNotFoundError as exc:
        raise XDFPairingError('XDF file disappeared during verification') from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise XDFPairingError('XDF file is still changing; stop LabRecorder and retry')
    if time.time() - after.st_mtime < max(pause, 1.0):
        raise XDFPairingError('XDF was modified too recently; ensure LabRecorder has finished')
    with path.open('rb') as file:
        if file.read(4) != b'XDF:':
            raise XDFPairingError('File does not have a valid XDF magic header')
    return after.st_size


def import_xdf(session_folder: Path, source_path: Path, *, stability_seconds: float = 1.0,
               explicit_selection: bool = False) -> dict:
    """Copy, verify, atomically publish, then delete source; never overwrite.

    Manual selection is an explicit operator assertion of pairing. The session
    is required to be stopped and its manifest finalized. No need to load XDF
    sample arrays (which can consume many gigabytes of RAM).
    """
    session_folder = Path(session_folder).resolve()
    source = Path(source_path).expanduser().resolve(strict=True)
    if source.suffix.lower() != '.xdf' or not source.is_file():
        raise XDFPairingError('Select a completed .xdf file')
    manifest_path = find_session_file(session_folder, 'session_manifest.json')
    meta_path = find_session_file(session_folder, 'config.json')
    if not manifest_path.is_file() or not meta_path.is_file():
        raise XDFPairingError('Stop and finalize the IMMSS session before importing an XDF')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('status') not in ('COMPLETED', 'PARTIAL', 'ABORTED'):
        raise XDFPairingError('Session has not finished')
    sid = _session_id(session_folder)
    if not explicit_selection and sid.lower() not in source.stem.lower() and sid[:8].lower() not in source.stem.lower():
        raise XDFPairingError('XDF filename does not identify this session')
    expected = expected_xdf_name(session_folder)
    target_dir = session_folder / 'lsl'
    target = target_dir / expected
    if source == target or target.exists():
        raise XDFPairingError('XDF already exists at the session destination; refusing to overwrite')
    size = _file_stable(source, stability_seconds)
    source_before_hash = source.stat()
    source_hash = _sha256(source)
    source_after_hash = source.stat()
    if ((source_before_hash.st_size, source_before_hash.st_mtime_ns) !=
            (source_after_hash.st_size, source_after_hash.st_mtime_ns)):
        raise XDFPairingError('Source changed while hashing; stop LabRecorder and retry')
    target_dir.mkdir(parents=True, exist_ok=True)
    pending = target_dir / ('.' + expected + '.' + uuid.uuid4().hex + '.partial')
    try:
        with source.open('rb') as inp, pending.open('xb') as out:
            shutil.copyfileobj(inp, out, length=4 * 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        if pending.stat().st_size != size or _sha256(pending) != source_hash:
            raise XDFPairingError('XDF transfer verification failed; original retained')
        # Protect against an active LabRecorder, even if its writing paused during the initial check.
        again = source.stat()
        if ((again.st_size, again.st_mtime_ns) !=
                (source_after_hash.st_size, source_after_hash.st_mtime_ns)):
            raise XDFPairingError('Source XDF changed during transfer; original retained')
        pending.rename(target)
    finally:
        pending.unlink(missing_ok=True)
    relative = target.relative_to(session_folder).as_posix()
    record = {
        'status': 'verified_copy', 'source_path': str(source), 'relative_path': relative,
        'file_size_bytes': size, 'sha256': source_hash,
        'pairing_method': 'operator_selected' if explicit_selection else 'session_id_filename',
        'verified_wall_time': datetime.now(timezone.utc).isoformat(),
        'session_id': sid,
        'xdf_structure_validated': False,
    }
    # Record provenance BEFORE deleting the only original. If metadata updating
    # fails, the verified destination and original remain for operator recovery.
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    meta['labrecorder_xdf'] = record
    _atomic_json(meta_path, meta)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['labrecorder_xdf'] = record
    if isinstance(manifest.get('config'), dict):
        manifest['config']['labrecorder_xdf'] = record
    # metadata.json was updated; refresh its manifest digest instead of leaving
    # stale integrity information after XDF import.
    for entry in manifest.get('files', []):
        if entry.get('relative_path') == meta_path.relative_to(session_folder).as_posix():
            entry['size_bytes'] = meta_path.stat().st_size
            entry['sha256'] = _sha256(meta_path)
    manifest.setdefault('files', []).append({
        'session_id': sid, 'relative_path': relative, 'original_filename': target.name,
        'file_role': 'xdf', 'size_bytes': size, 'sha256': source_hash,
        'status': 'IMPORTED_VERIFIED',
    })
    _atomic_json(manifest_path, manifest)
    # Deleting the source is safe only if it has remained unchanged since copying.
    # Retain it on any uncertainty instead of risking loss of the sole recording.
    source_removed = False
    try:
        final_state = source.stat()
        if ((final_state.st_size, final_state.st_mtime_ns) ==
                (source_after_hash.st_size, source_after_hash.st_mtime_ns)):
            source.unlink()
            source_removed = True
    except OSError:
        pass
    return {**record, 'source_removed': source_removed}
