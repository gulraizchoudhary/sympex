"""Stable, portable session identity for local recording files.

New sessions use Participant_<id>/Session_<timestamp>_<random>/ and prefix
all session outputs with both identities. Legacy/non-session directories keep
original names so previews, tests and old recordings remain supported.
"""
from pathlib import Path


def session_prefix(folder):
    folder = Path(folder)
    if folder.name.startswith('Session_') and folder.parent.name.startswith('Participant_'):
        participant = folder.parent.name.removeprefix('Participant_')
        return f'{participant}__{folder.name}__'
    return ''


def session_file(folder, basename):
    folder = Path(folder)
    return folder / (session_prefix(folder) + str(basename))


def find_session_file(folder, basename):
    """Resolve both new names and legacy names; do not silently select ambiguity."""
    folder = Path(folder)
    expected = session_file(folder, basename)
    if expected.exists():
        return expected
    old = folder / basename
    if old.exists():
        return old
    matches = list(folder.glob(f'*__{basename}')) if folder.exists() else []
    if len(matches) == 1:
        return matches[0]
    return expected
