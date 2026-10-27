"""Windows PnP sound hardware names; not interchangeable with PortAudio indices.

Win32_SoundDevice displays names associated with Sound, video and game controllers.
PortAudio enumerates recording endpoints. Their IDs are not directly interchangeable.
"""
import json
import re
import subprocess
import sys


def device_manager_sound_names(timeout=8):
    """Return Windows Sound/video/game controller names, or [] outside Windows.

    Uses the system PowerShell executable without shell string interpolation.
    """
    if sys.platform != 'win32':
        return []
    command = (
        "Get-CimInstance Win32_SoundDevice | "
        "Select-Object -ExpandProperty Name | ConvertTo-Json -Compress"
    )
    cp = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        check=True, text=True, capture_output=True, timeout=timeout,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    raw = cp.stdout.strip()
    if not raw:
        return []
    names = json.loads(raw)
    if isinstance(names, str):
        names = [names]
    return sorted(set(str(v).strip() for v in names if str(v).strip()), key=str.casefold)


def _norm(s):
    return ' '.join(re.findall(r'[a-z0-9]+', s.casefold()))


def safe_hardware_match(endpoint_name, hardware_names):
    """Return a name ONLY when a single non-generic PnP name appears in endpoint name.

    Do not guess a connection between 'USB Audio Device' and a branded microphone.
    """
    ep = _norm(endpoint_name)
    generic = {'usb audio device', 'high definition audio device', 'audio device', 'usb audio'}
    matches = []
    for name in hardware_names:
        normalized = _norm(name)
        if len(normalized) >= 7 and normalized not in generic and (
            normalized == ep or f' {normalized} ' in f' {ep} '
        ):
            matches.append(name)
    return matches[0] if len(matches) == 1 else None
