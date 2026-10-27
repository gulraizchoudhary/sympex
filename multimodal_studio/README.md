# Multimodal Studio — Interactive Experiment Recorder

A modular Tkinter implementation consistent with the previous Polar H10 + microphone application. Python 3.10+ recommended. Works with Windows USB audio devices, the `polar-python` BLE library and LSL broadcasters.

## Install

```bash
python -m pip install -r requirements.txt
python app.py
```

Start with **no devices selected**. Select only those used in the experiment. Refresh audio endpoints and choose laptop microphone and/or the Ultramic; scan Polar before enabling it; scan/select the SeeTrue LSL stream on the dedicated tab. Each selected device attempts to start independently. Session data are written to `../Participant_Data` by default, configurable in the UI.

## Modules

- `core.py`: timestamps, markers, session lifecycle, event logs.
- `devices/audio.py`: independent WAV + block timestamp acquisition for ordinary microphones and Ultramic 384K PRO.
- `devices/polar.py`: Polar BLE HR/RR with optional ECG and accelerometer, CSV and LSL outlets. Follows original project's HR → ECG → ACC startup sequence. Samples' device clock is not yet calibrated to LSL.
- `devices/seetrue.py`: LSL discovery, reception and loss detection; preserves broadcast channel order.
- `devices/camera_recorder.py`: independent camera MP4 recording and per-frame LSL host timestamps.
- `app.py`: redesigned dark dashboard with a device-selection sidebar, context-sensitive settings, session overview, dedicated SeeTrue discovery, camera selection and preview panels, session timer and live activity/errors.

## Updated interface walkthrough

1. Enter participant ID and select the recording folder in the top bar.
2. Check only the required devices in the left sidebar; select a device name to show its settings.
3. Set audio input / sample rate, scan Polar if applicable, and discover SeeTrue LSL streams in the separate tab.
4. Press **START SESSION**. The Activity tab shows individual device startup and failure messages.
5. Use the marker field below the workspace to add synchronized events; **STOP** saves the session without requiring all devices to be healthy.

The dashboard does not claim a camera preview exists until camera capture modules are implemented.

## Important limitations

1. Each selected camera saves MP4 video and a frame timestamp CSV. Automatic reconnect is not implemented.
2. A failed device will not stop the session or other recorders. Audio streams that stop are flagged, but **not auto-restarted**; SeeTrue's `StreamInlet` has recovery enabled but repeated disconnects may still require intervention; Polar reconnection is manual in this version.
3. Audio timing CSV values are *estimates* in the LSL local clock, based on PortAudio ADC time. This is not validated microsecond or frame/sample accuracy. A USB sound card can have an independent clock and drift. Test timing with a shared physical signal.
4. Ultramic support assumes the driver exposes 384 kHz mono PCM through PortAudio; the selected Windows host API might not support it. Try WASAPI/exclusive-capable drivers/configuration if needed. Check input format before experiments.
5. The audio write queue is bounded; disk stalls or overflow may lose data. Events are logged, but dropped samples are not reconstructed, so alignment after dropout needs QC. WAV file growth is ~2.76 GB/hour at 384 kHz mono 16-bit. Rotate files or use RF64 for lengthy sessions.
6. SeeTrue fields depend entirely on what the transmitting software publishes; this receiver cannot add missing pupil, gaze, or confidence channels. Check LSL channel labels and manufacturer stream configuration.
7. Device selection locks while a session is active. Unselected devices are never started. Enabled camera slots are included in recordings.
8. The original application's more extensive plots and interval markers are not yet ported; the app has a shared event marker button and event/quality logs.

**Test each driver and stream with a short session before research recording.**

## Selected-device Live Monitoring

Open the **Live monitoring** tab. Only enabled devices are displayed. Each audio
recorder shows a downsampled signal preview and RMS level; Polar H10 displays ECG
(or HR/ACC fallback), and SeeTrue plots its first two numeric LSL channels.
The monitoring display only uses actual samples collected during recording and
is not an independent pre-recording preview. The SeeTrue channel order is
source-defined: inspect the SeeTrue LSL tab to confirm which channels represent
gaze coordinates. Selected camera video is displayed in the Cameras tab.

Monitoring is intentionally decimated to reduce UI load; the WAV/CSV recording
paths are independent of the display. Error and dropped statuses remain visible
on individual panels and in the event log. These changes have been syntax-checked,
but were not tested on connected hardware.

## Preview mode and five-second monitors

Select sensors, scan and configure their inputs, then click **PREVIEW**. The enabled sensor modules run in temporary test acquisition, with continuously scrolling five-second charts under **Live monitoring**. Press **STOP PREVIEW** to finish the test and delete its temporary WAV/CSV files. Preview does **not** create a participant session. Start Session automatically stops preview first; there is a short interruption while devices are restarted. Recording uses the same displays while saving into the participant session folder. The charts use acquired data, not simulated signals. Camera Test preview is also available from the Cameras tab; experiment Preview does not retain camera video.

**Caution:** Preview currently uses the normal recorder implementations and writes temporary files before discarding them. This can cause temporary disk usage (especially 384 kHz audio). LSL channel graphs are generic unless channel mapping is verified. Device clocks and sample loss require hardware validation before scientific analysis.

### Audio monitor axes and diagnostics
The 5-second rolling **horizontal** time axis now runs from 0 to 5 seconds, oldest to newest. The **vertical** audio amplitude axis is fixed at −1 to +1 for normalized 16-bit PCM. The monitor reports peak and RMS amplitude and flags missing samples or extremely low signal. A nearly flat waveform with samples received may indicate muted input, incorrect Windows microphone selection, low gain, or a quiet acoustic environment. Device recording and original WAV signal are not amplified or changed by the display.


### 5-second monitor correction
Audio display samples are now downsampled by **sample rate**, targeting approximately 300 points per second, instead of taking 160 samples per PortAudio callback. That avoids a short preview buffer when callbacks are small. On Preview/Record startup, the graph begins filling at **0 seconds**, reaches **5 seconds**, and then continuously scrolls a five-second window with the newest data at 5 seconds. The x-axis always spans 0–5 seconds. Peak 0.0000 and RMS 0.0000 with captured audio samples means **digital silence**, not an x-axis error; check device routing, microphone permissions, gain, and the resulting WAV file.

## Audited 5-second live monitor (2026-10-23)

- The horizontal scale is **5 → 0 seconds ago** (newest sample at x=0, oldest at x=5).
- Window positions come from audio sample timestamps, not the number of points kept in memory. A partial history is not stretched to fill five seconds. After five seconds of uninterrupted acquisition, samples can cover the complete window.
- Audio preview data are normalized to [-1,1] for display. The actual WAV data remain 16-bit PCM. The preview is decimated, and should not be used for quantitative ultrasound-frequency analysis.
- The microphone remains silent if selected device returns all-zero audio; timeline fixes do not fix muted hardware. Diagnose Peak/RMS and the actual WAV separately.
- Device acquisition uses background threads. Camera video is captured using OpenCV and saved independently per camera.

Run deterministic software-only checks (no device attached):

    python -m unittest discover -s tests -v

The audio callback unit tests use mocked PortAudio/LSL and synthetic waveforms; they are not evidence that any physical microphone, BLE Polar H10 or SeeTrue stream operates on your computer. The full Tk GUI is not launched by the tests. Test microphone selection, SeeTrue channel mapping, Polar streaming, writer resilience, USB scheduling, and timing accuracy with real hardware before study acquisition.


## LSL selective channel recording

In **SeeTrue · LSL**, press **Discover LSL streams**, select the broadcaster,
and use the scrollable **Channels to RECORD** checklist to choose channels.
**Select all** and **Clear selection** are available. At least one channel is required.
Selections are locked during Preview and Record.

Only the checked channels are written to `seetrue_lsl.csv`. The two
timestamp columns are always included. CSV headers include the original
1-based LSL channel index. `seetrue_channels.json` contains the exact
zero-based and one-based index/label-to-CSV-column mapping.
`config.json` also contains the chosen zero-based channel indices.
The live plot uses the first two selected numeric channels; the full raw
LSL sample remains temporarily available to the recorder for monitoring.
If broadcaster channel order changes after discovery, rediscover and
verify mappings before starting the experiment.

## Persistent event/error logging (updated)

All recorder events emitted through `EventBus` are visible in **Activity & errors**.
They are also written persistently to `logs/application.log`, including **Preview**, device discovery,
startup validation errors, warnings/cautions, dropped devices and shutdown messages. When a formal
experiment is running, these events are additionally saved to the session's `events.log`.
Python unhandled exceptions in the main thread and background threads are also reported through
the event bus (and retain standard traceback output on stderr). Standard output from arbitrary
third-party libraries is not intercepted. Application warnings should always use
`bus.emit(device_name, 'WARNING', message)` rather than `print(...)`.

The Polar H10 recorder now initializes its `future` before validation so a failed
Preview start does not cause an additional `AttributeError` during stop. The stop
path also skips BLE disconnect when connection was never attempted.

Run automated tests with `python -m unittest discover -s tests -v`.

## Camera selection and live preview

Install `opencv-python` and `Pillow` from requirements. In **3 camera previews**, press **Scan available cameras**, select a camera index from each slot's dropdown, and press **Test preview**. Up to three *different* devices can be previewed at once; duplicate devices are refused while already in use. The live preview runs on a separate thread, and camera errors are sent to the central application log. **Video is not recorded or saved by this release.** On Windows camera indices are probed with DirectShow, on other platforms the default OpenCV backend. Devices that are already occupied by another application may not appear. Camera selection cannot be rescanned during an experiment session.

## Camera recording (new)

1. In **Cameras**, click **Scan available cameras**, select the desired camera for slot 1/2/3, and use **Test preview** to verify the live picture.
2. Enable the corresponding **Camera 1/2/3** device checkbox in the sidebar (only enabled slots record).
3. Click **Start Session**. The application first releases standalone test-preview handles, then opens the same chosen indices for independent MP4 recording. The Cameras tab continues displaying recording frames.
4. Click **STOP** to finalize video files. Each enabled camera creates `camera_N.mp4`, `camera_N_frames.csv` and `camera_N_metadata.json` inside the experiment session.
5. Preview mode does not preserve camera video. The camera-specific **Test preview** buttons are for camera verification only.

**Timing caveats:** `lsl_capture_time_estimate` comes from the computer's LSL clock just after `VideoCapture.read()` and is *not* a hardware exposure timestamp. Frame CSV entries correspond to frames passed to the encoder; OpenCV's fixed-fps MP4 container may not preserve variable real acquisition intervals. For scientific synchronization and playback, use frame timestamps in the CSV and validate latency against a visible synchronized event. Video fps is based on the reported camera fps when credible, otherwise 25 fps. The program does not support hardware trigger sync, camera reconnection, or guaranteed capture throughput.

**Troubleshooting:** Two enabled slots cannot use the same camera index. If one camera fails, its error goes to `logs/application.log` and session `events.log`; other sensors continue. If the camera cannot be opened, its video may be absent or incomplete. Verify playback and CSV row counts before experimental use.

## Unified microphone selector (2026-10 update)
The former Microphone and Ultramic controls have been combined into one optional Microphone device. The input dropdown lists PortAudio's Windows endpoint names with their host API and device index (WASAPI entries are prioritized). The same input selection is used for Preview and Record. Selecting an endpoint whose reported name contains `Ultramic` sets 384000 Hz by default; other inputs use their reported default rate when present in the dropdown. Recording validates the sample rate before starting. Windows Device Manager driver names are not guaranteed to be identical to PortAudio audio endpoint names; check the backend and index if several copies of a device appear. The recording file is `microphone.wav` for any selected audio endpoint.

### Windows Device Manager hardware names (updated)
The Microphone configuration panel now shows the names returned by Windows `Win32_SoundDevice` (the Sound, video and game controllers hardware list) next to the recording endpoint selector. It also includes a button to show the full list. An exact, unambiguous text match is prefixed to the corresponding audio endpoint entry. **A driver/hardware name is not itself a PortAudio capture endpoint**: if Windows reports a generic endpoint such as `Microphone (USB Audio Device)`, the application cannot safely establish which branded hardware name corresponds to that endpoint using names alone. In that case the hardware name remains visible separately, but the endpoint is not renamed based on a guess. For full physical-device-to-endpoint mapping on all hardware, Windows Core Audio endpoint device-container/association metadata is needed. Recording always uses the actual verified PortAudio device index.
