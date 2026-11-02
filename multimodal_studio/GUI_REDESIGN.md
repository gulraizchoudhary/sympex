# IMS device-oriented interface

The app now opens on **Experiment dashboard**, with dedicated tabs for **Polar H10**, **Microphone**, **Cameras**, **Network LSL**, **Live monitoring**, **Post-experiment analysis** and **Logs & diagnostics**.

- Selecting any sidebar device navigates directly to its dedicated tab. Each camera selects its own camera dropdown.
- Polar, microphone and each camera have **Include in experiment** checkboxes within their respective tabs; these share the existing sidebar BooleanVars so there are no divergent selections.
- Dashboard shows selected devices and shortcuts to their controls.
- The existing Preview, Start Session, Stop and marker controls are unchanged, as are WAV/MP4/CSV/LSL formats, device modules, channel selections, error logging and analysis.
- The Network LSL Manager continues to enable streams per selected outlet, independently of the five local device checkboxes.
- Camera controls continue to use OpenCV and depend on driver support; the video format and achieved frame timing must be validated on the physical cameras.

## Validation

All 29 pre-existing unit tests passed with Python compilation. Graphical smoke testing was attempted under a virtual display but the container lacks the `sounddevice` runtime dependency, so the full GUI could not be launched here. Run GUI and hardware checks on your Windows acquisition computer.

## Install and start

`python -m pip install -r requirements.txt`

`python app.py`

## Operational smoke test on Windows

1. Select Microphone, check Include, choose the working WASAPI endpoint and preview.
2. Select Polar, check Include, scan, and verify the preflight before recording.
3. Select Camera 1, check Include, test preview and requested focus/exposure settings.
4. Select Network LSL, choose multiple sources and channels.
5. Open Dashboard and check selections; start a short recording; verify the session files and logs.
