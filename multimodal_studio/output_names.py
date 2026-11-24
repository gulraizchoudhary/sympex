"""Portable, collision-resistant recording filenames for legacy and SYMPEX sessions."""
from pathlib import Path
import re

_STUDY_SESSION = re.compile(r"^S\d{8}T\d{6}_[a-f0-9]{8}$")
_SESSION_BLOCKS = {}
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")


def checked_token(value, field):
    value = str(value).strip()
    if not _TOKEN.fullmatch(value) or value in ('.', '..'):
        raise ValueError(f'{field} must contain 1–40 letters, digits, _ or - (starting with a letter or digit)')
    return value


def session_prefix(folder):
    folder = Path(folder)
    # SYMPEX_2027 / CPH / P001 / V01_Training / S20270210T093000_a1b2c3d4
    if (_STUDY_SESSION.fullmatch(folder.name) and
            re.fullmatch(r'V\d{2}_[A-Za-z0-9_-]+', folder.parent.name) and
            folder.parent.parent.name and folder.parent.parent.parent.name):
        centre = folder.parent.parent.parent.name
        participant = folder.parent.parent.name
        visit = folder.parent.name.split('_', 1)[0]
        # Study + centre and participant are kept in the path; filename carries
        # centre, participant, visit, block, and immutable short session ID.
        block = _read_block(folder)
        if all(_TOKEN.fullmatch(x) for x in (centre,participant,visit,block)):
            return f'{centre}_{participant}_{visit}_{block}_S{folder.name.rsplit("_",1)[1]}__'
    if folder.name.startswith('Session_') and folder.parent.name.startswith('Participant_'):
        participant = folder.parent.name.removeprefix('Participant_')
        return f'{participant}__{folder.name}__'
    return ''


def _read_block(folder):
    # Block is part of the session context, but not inferred from an untrusted
    # file. A fixed B01 is used only for legacy study folders lacking a context.
    key = str(folder.resolve())
    if key in _SESSION_BLOCKS:
        return _SESSION_BLOCKS[key]
    # Existing study sessions can recover their block from any named file.
    files = list(folder.glob('*__config.json')) if folder.exists() else []
    if len(files) == 1:
        match = re.search(r'_((?:B)\d{2})_S[a-f0-9]{8}__config\.json$', files[0].name)
        if match:
            return match.group(1)
    return 'B01'


def register_study_block(folder, block):
    _SESSION_BLOCKS[str(Path(folder).resolve())] = checked_token(block, 'block')


def unregister_study_block(folder):
    _SESSION_BLOCKS.pop(str(Path(folder).resolve()), None)


def session_file(folder, basename):
    return Path(folder) / (session_prefix(folder) + str(basename))


def find_session_file(folder, basename):
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
