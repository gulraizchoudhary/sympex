"""SYMPEX visit/block recovery from existing configs and timestamped markers."""
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from output_names import checked_token

DEFAULT_MAX = {'Training': 1, 'EndTraining': 1, 'Retention': 1, 'Refresher': 2}


def validate_max_blocks(value):
    n = int(value)
    if not 1 <= n <= 5:
        raise ValueError('Maximum blocks must be between 1 and 5')
    return n


def visit_folder(root, centre, participant, visit):
    centre = checked_token(str(centre).upper(), 'centre')
    participant = checked_token(str(participant).upper(), 'participant')
    if not participant.startswith('P'):
        participant = 'P' + participant
    v = int(visit)
    if not 1 <= v <= 99:
        raise ValueError('Visit number must be between 1 and 99')
    base = Path(root).expanduser().resolve()
    if base.name != 'SYMPEX_2027':
        base = base / 'SYMPEX_2027'
    return base / centre / participant, f'V{v:02d}'


@dataclass(frozen=True)
class BlockState:
    current: int
    maximum: int
    completed: frozenset
    existing: frozenset
    configured: bool

    @property
    def can_advance(self):
        return self.current in self.completed and self.current < self.maximum

    @property
    def can_start(self):
        return self.current not in self.completed and self.current <= self.maximum


def _session_files(folder, suffix):
    found = list(folder.glob(f'*__{suffix}')) + list(folder.glob(suffix))
    return found[0] if len(found) == 1 else None


def load_block_state(root, centre, participant, visit, requested_max=None):
    parent, visit_code = visit_folder(root, centre, participant, visit)
    completed, existing, limits = set(), set(), set()
    if parent.exists():
        for phase_dir in sorted(parent.glob(f'{visit_code}_*')):
            if not phase_dir.is_dir():
                continue
            for session_dir in sorted(phase_dir.iterdir()):
                if not session_dir.is_dir():
                    continue
                config_path = _session_files(session_dir, 'config.json')
                if config_path is None:
                    continue
                try:
                    data = json.loads(config_path.read_text(encoding='utf-8'))
                    study = data.get('study') or {}
                    stored_max = study.get('max_blocks')
                    if stored_max is not None:
                        limits.add(validate_max_blocks(stored_max))
                    marker_path = _session_files(session_dir, 'markers.csv')
                    has_block_markers = False
                    if marker_path is not None:
                        with marker_path.open(newline='', encoding='utf-8') as stream:
                            for row in csv.DictReader(stream):
                                event = row.get('marker', '')
                                if event.startswith('B') and len(event) >= 7 and event[1:3].isdigit():
                                    number = int(event[1:3])
                                    if not 1 <= number <= 5:
                                        continue
                                    if event in (f'B{number:02d}_START', f'B{number:02d}_END', f'B{number:02d}_INTERRUPTED'):
                                        has_block_markers = True
                                        existing.add(number)
                                        if event.endswith('_END'):
                                            completed.add(number)
                    # Legacy IMMSS recorded a separate session per block.
                    if not has_block_markers and study.get('block'):
                        number = int(str(study['block']).removeprefix('B'))
                        if 1 <= number <= 5:
                            existing.add(number)
                            manifest_path = _session_files(session_dir, 'session_manifest.json')
                            if manifest_path:
                                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                                if manifest.get('status') == 'COMPLETED':
                                    completed.add(number)
                except (OSError, ValueError, TypeError, KeyError, csv.Error):
                    continue
    if len(limits) > 1:
        raise ValueError('Conflicting maximum block counts in the saved visit')
    saved_max = next(iter(limits)) if limits else None
    if requested_max is not None:
        requested_max = validate_max_blocks(requested_max)
        if saved_max is not None and requested_max != saved_max:
            raise ValueError(f'Visit maximum already fixed at {saved_max}; cannot change to {requested_max}')
    maximum = saved_max or requested_max or 1
    if existing and max(existing) > maximum:
        raise ValueError('Existing block exceeds visit maximum')
    # Always select first incomplete block; interrupted attempts are retried.
    current = next((n for n in range(1, maximum + 1) if n not in completed), maximum)
    return BlockState(current, maximum, frozenset(completed), frozenset(existing), saved_max is not None)


def next_block(state):
    if not state.can_advance:
        raise ValueError('The block must be finished and below the maximum')
    return state.current + 1
