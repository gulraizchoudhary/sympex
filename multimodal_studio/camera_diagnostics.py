"""Run independently of the GUI: python camera_diagnostics.py"""
import datetime
import pathlib
import platform
import subprocess
import traceback

report_path = pathlib.Path('camera_diagnostics.txt').resolve()
lines = []
def log(s):
    s = str(s)
    print(s, flush=True)
    lines.append(s)

log(f'Date: {datetime.datetime.now().isoformat()}')
log(f'Python: {platform.python_version()} / {platform.platform()}')
try:
    import cv2
    log(f'OpenCV: {cv2.__version__}')
    log(f'OpenCV path: {cv2.__file__}')
    log(f'FFMPEG={cv2.getBuildInformation().count("FFMPEG:                      YES") > 0}')
    if platform.system() == 'Windows':
        log('Windows camera PnP entries:')
        command = ['powershell', '-NoProfile', '-Command',
          "Get-PnpDevice -Class Camera -ErrorAction SilentlyContinue | Select-Object FriendlyName,Status,InstanceId | Format-List | Out-String -Width 220"]
        try:
            output = subprocess.run(command, capture_output=True, text=True, timeout=15)
            log(output.stdout.strip() or '(none returned)')
            if output.stderr.strip(): log('PowerShell stderr: '+output.stderr.strip())
        except Exception as exc: log(f'PnP query unavailable: {exc!r}')
    from devices.camera_preview import discover_cameras
    log('Testing video indices 0..9 (DirectShow, Media Foundation, default).')
    found = discover_cameras(10, report=log)
    log(f'Usable camera devices: {found!r}')
except Exception:
    log(traceback.format_exc())
finally:
    report_path.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(f'\nDiagnostic report saved to {report_path}', flush=True)
