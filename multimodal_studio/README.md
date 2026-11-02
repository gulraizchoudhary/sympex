# Intelligent Multimodal Microsurgery Studio (IMMSS) — Microsurgical Multimodal Research Platform

Intelligent Multimodal Microsurgery Studio (IMMSS) is a modular Tkinter implementation consistent with the previous Polar H10 + microphone application. Python 3.10+ recommended. Works with Windows USB audio devices, the `polar-python` BLE library and LSL broadcasters.

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

## Post-experiment analysis (integrated)

Use the **Post-experiment analysis** tab in `app.py` after stopping an experiment.
Browse to a `Participant_*/Session_*` folder, click **Scan**, select timestamped
streams and optional channels, then tune warning thresholds and click **Analyze**.
The tab displays a sortable warning table and interactive matplotlib charts (pan,
zoom, save) for coverage, sample-interval regularity, and matched-event offsets.
Reports are saved under `SESSION/analysis_reports/TIMESTAMP/` (JSON, CSV, PNG,
summary.txt); source data is read-only.

**Important:** start/stop differences are coverage, not physical sensor latency;
clock drift vs nominal rate is not inter-device drift. For true relative offset/drift,
provide an optional CSV containing `stream,event,time_lsl` with repeated physical
synchronization events detected in two or more streams. Use exact discovered
stream names (CSV filename stems). Warnings are heuristic; gap counts from chunked
or event-based sources may not correspond to lost physical samples. Camera MP4
fixed-FPS timing can disagree with capture timestamps.

The tab blocks analysis while the current session is active to avoid reading
partially written recording files. It runs analysis in a worker thread.
Dependencies include pandas for offline analysis.

### Expanded interactive Post-Experiment Analysis plots

After **Analyze selected streams**, use the graph dropdown to switch among nine views:
Coverage, Inter-sample intervals, Jitter distribution, Large gaps timeline,
Nominal-rate drift, Reception lag, Pairwise overlap, Camera timing, and
Event offsets. Use the Matplotlib toolbar to zoom, pan and save figures.

**Interpretation:** Jitter residuals are referenced to nominal sample cadence
(or median cadence if unknown). Extreme residuals are clipped **only in the
histogram presentation**, not in saved data/metrics. Gap points are cadence
anomalies and do not prove actual lost packets. Nominal-rate drift is a
sample-index/timebase check, not calibrated inter-device drift. Reception lag
requires timestamp clock-domain consistency. Event offsets require matched
physical anchors. Camera timing compares the video writer's nominal duration
to the frame timestamp span and is not a camera exposure offset measurement.

## Polar preflight and session file identity (2026-10-28)

If Polar H10 is selected, **Start Session** now connects it first and waits for
actual HR packets, and ECG/ACC packets when those options are checked, before
starting the other acquisition modules and writing `RECORDING_START`.
The wait runs on a worker thread; the user interface remains responsive.
If live samples do not arrive within 45 seconds, session start is aborted
and logged. Unselected Polar is never required. A BLE dropout *after* a
successful session start is still logged without stopping other devices.

Every completed session now has a random `session_id` in `config.json`, the
`markers.csv` session_id column, and `session_manifest.json`. Each generated
file also has a matching `<original name>.session.json` sidecar carrying the
session_id, participant, session folder, byte size, SHA-256 checksum and name.
The original media/CSV filenames have deliberately been preserved for
compatibility with the existing Post-Experiment Analysis tab. **Keep the sidecar
with its original file** when transferring files between folders; WAV and MP4
formats are not renamed or re-encoded. Hashing large media on finalization
can require extra disk activity and time.

The readiness preflight does not guarantee sample-level alignment. The Polar
CSV may contain setup samples before the formal start marker, and the
optional streams must still be checked for gaps and delayed startup.


## Multiple network LSL streams (IMS update)

The **Network LSL streams** tab discovers all currently visible LSL outlets on the local host and compatible network. It is **not necessary** to combine every device into a single LSL stream. Each physical sensor may publish several streams (e.g., ECG, ACC, markers). Streams with identical names are displayed separately using the LSL **UID**, source ID and host name. A name alone must not be used to identify an outlet.

1. Start the publishers on the sensor computers, and confirm LSL discovery is allowed by firewall/network configuration.
2. Open **Network LSL streams**, then click **Discover network streams**.
3. Click a row and choose **Enable stream**. Repeat for as many outlets as desired.
4. Select only the channel checkboxes needed for that outlet.
5. Click **PREVIEW** to test, or **START SESSION** to record each selected outlet separately.
6. Files `network_lsl_XX_lsl.csv` and `network_lsl_XX_channels.json` are created for each chosen outlet, alongside session manifest metadata.

The existing dedicated SeeTrue tab is retained for compatibility. **Do not select the same LSL UID in SeeTrue and Network LSL simultaneously.** LSL `RECORDING_START` markers are published separately through `ExperimentMarkers`, which is a single marker stream shared by IMS (not a container for all sensor samples). Outlets may have different channel counts, rates, and native clocks. The CSV preserves original source timestamps and local receive clock; physical synchronization still requires appropriate timestamp-correction/validation.

Stream indices in `network_lsl_XX` reflect the current discovery order, which may change across scans or sessions; use the `uid` and `source_id` recorded in `_channels.json` and the session manifest to identify an outlet unambiguously. Sample silence/no data is reported to the event log, and other outlets continue independently.

**Limitations:** LSL multicast may not cross all network segments/VLANs; antivirus/firewalls and Wi-Fi isolation can prevent discovery. This version supports per-stream CSV capture and channel selection, but does not yet display individual plots for each network outlet in Live Monitoring. Data from newly started worker threads may begin after the session start marker; the post-experiment analyzer should still check start coverage.


## Unified Network LSL Manager (October 2026)

The separate SeeTrue acquisition tab and sidebar checkbox were removed. SeeTrue and all other external LSL sources are selected through **Network LSL streams**. Discover outlets, enable multiple outlets, and choose channels independently for each. Each selected stream has its own CSV and channel metadata JSON. Old SeeTrue CSV files remain readable; the legacy `devices.seetrue.SeeTrueRecorder` class is retained as an implementation detail for compatibility.

IMS-created outlet names identify their content: `IMS_PolarH10_HR`, `IMS_PolarH10_RR`, `IMS_PolarH10_ECG`, `IMS_PolarH10_ACC`, and `IMS_Experiment_Markers`. They include distinct source IDs for each application instance or Polar recorder instance. Incoming LSL outlets keep the name assigned by the external publisher, which IMS cannot rename; use type, hostname, source ID, UID, and channel labels to identify them.

**Avoid accidental duplicate recordings:** Polar H10 data published by IMS can also be discovered through the Network LSL Manager. Select either the local Polar recording, the discovered LSL copy, or both intentionally, as they are separate saved files. The local Polar HR/ECG/ACC timestamps should not be assumed hardware synchronized merely because they are LSL-broadcast. No new raw audio/video LSL publisher is created by this update; microphone audio and camera video continue to be saved in their native file formats.


## All-device LSL broadcast

IMS broadcasts separate descriptive outlets while selected devices are acquiring:

- `IMS_Microphone_Audio` or `IMS_Ultramic384K_Audio`: full-rate float32 normalized PCM samples in chunks. Timestamp passed to LSL is the estimated time of the last sample of each chunk; earlier times are interpolated by LSL. Host/device clock calibration is required for high-precision alignment. At 384 kHz, expect substantial network traffic; receiver/network performance must be tested. WAV recording remains the authoritative raw PCM16 artifact.
- `IMS_Camera1_Frames`, `IMS_Camera2_Frames`, `IMS_Camera3_Frames`: one sample per saved frame, fields `FrameNumber`, `Width`, `Height`, `MonotonicCaptureTime`; the LSL timestamp is the host-side frame-retrieval estimate (NOT actual exposure time). MP4 contains video pixels, not LSL.
- `IMS_PolarH10_HR`, `_RR`, `_ECG`, `_ACC`: existing separate physiological LSL outlets, active when Polar is selected and ready.
- `IMS_Experiment_Markers`: event markers, available while IMS is running.

**Discoverability**: only active/started devices have their data outlets. Use Preview or Record, then discover again in the Network LSL Manager or another LSL client. Do not enable an IMS-broadcast outlet as a network input in the same session unless you intentionally want a duplicate copy. Stream source IDs are unique per outlet, and channel/type metadata describes its data. External LSL broadcasters are not renamed by IMS.

### Camera configuration (IMS)
Click **Camera 1**, **Camera 2**, or **Camera 3** in the left device list to jump directly to the Cameras tab. There is no separate camera Settings page. For each slot, choose a detected camera, test the preview, then set dimensions, nominal FPS, autofocus or manual focus, automatic or manual exposure, brightness and gain. Click **Apply settings & restart preview** to test the changes; the same requested settings are passed to the camera recorder when you start a session. Camera controls are unavailable during an active session to protect recording consistency. Camera properties are best-effort: Windows DirectShow/MSMF drivers often ignore or clamp requested values. Check Activity & errors for unsupported settings and check the saved camera metadata for actual resolution and video FPS. Brightness/gain/exposure numbers are *driver-specific*, not standardized photographic units. External lighting cannot be controlled through OpenCV unless that lighting device exposes a supported control. Exposure auto/manual semantics differ by backend.

## Device-oriented GUI (latest update)

See `GUI_REDESIGN.md`. Eight primary tabs separate experiment readiness from settings and live monitoring while preserving recorder functionality and existing data formats.
