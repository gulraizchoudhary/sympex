# Intelligent Multimodal Microsurgery Studio (IMMSS)

**Multimodal acquisition, trial annotation, synchronized LSL streaming, and recording-quality review for the SYMPEX tympanostomy study**

**Code author:** Gulraiz  
**Documentation revision:** 26 November 2026  
**Basis:** Original `multimodal_studio.zip` and the successive IMMSS code-update packages supplied in this conversation, through the camera JPEG/LSL and LSL stop-cleanup updates. This describes the combined *source-level implementation*, not a certified deployed release. Check the installed version if local edits differ.

> **What IMMSS is:** A Python/Tkinter research acquisition application integrating up to three cameras, microphones, external LSL streams (including SeeTrue Station output), and an optional Polar physiological sensor. It organizes coded longitudinal visits, records timestamped procedure/trial events, offers live monitoring, and generates integrity/QA information. It is **not** a surgical scoring system, an eye-tracker calibration program, or an XDF recorder.

## Contents

1. [Capabilities and limits](#1-capabilities-and-limits)
2. [Install and run](#2-install-and-run)
3. [Interface and operational workflow](#3-interface-and-operational-workflow)
4. [SYMPEX identities, visits, and trials](#4-sympex-identities-visits-and-trials)
5. [Devices and acquisition](#5-devices-and-acquisition)
6. [LSL streams and shutdown](#6-lsl-streams-and-shutdown)
7. [Directory structure, filenames, and files](#7-directory-structure-filenames-and-files)
8. [Metadata and integrity](#8-metadata-and-integrity)
9. [Preview, monitoring, and offline analysis](#9-preview-monitoring-and-offline-analysis)
10. [Performance and storage](#10-performance-and-storage)
11. [Tests and hardware validation](#11-tests-and-hardware-validation)
12. [Troubleshooting](#12-troubleshooting)
13. [Architecture and development guidelines](#13-architecture-and-development-guidelines)
14. [Study governance and known gaps](#14-study-governance-and-known-gaps)

## 1. Capabilities and limits

| Area | Implemented source behavior | Important limitation |
|---|---|---|
| Longitudinal session identity | SYMPEX study, centre, coded participant, visit, phase, session UUID, computer hostname | A recorded session is not a complete participant registry or scheduling system |
| Centres | `CPH`, `KUH`, `JNS` in the GUI | Centre availability does not imply institutional study approval |
| Training unit | Five *trials* by default; configurable 1–5 before the visit starts | One trial denotes one tympanostomy attempt; no automatic success/error scoring |
| Events | Continuous session recording with `T01_START`, `T01_END`, etc., in a common LSL/event timeline | Event accuracy depends on the experimenter marking boundaries consistently |
| Video | Up to three selected cameras; JPEG frames, index CSV, camera metadata | 30 fps is requested, not guaranteed; JPEG sequences can create very many files |
| Audio | PortAudio microphone/Ultramic, WAV capture, timing companion, LSL publication | Audio ADC timestamps are estimates; driver and hardware timing must be tested |
| Eye tracking / LSL inputs | Select external streams/channels for CSV acquisition | SeeTrue calibration happens in SeeTrue Station, not IMMSS |
| Physiology | Exclusive choice: none (default), H10, or Verity Sense | Different physiological measures are not interchangeable |
| LSL outputs | Session metadata, experiment markers, camera frame indices, selected audio/Polar data | No native XDF output or managed LabRecorder pairing in this source |
| Recording integrity | Device readiness checks and post-stop integrity status | Software checks do not prove physical accuracy or perfectly synchronized exposure |
| Offline QA | Stream discovery, timing/coverage/jitter/drift diagnostics and a compact report | Quantitative inter-device alignment needs physical events/anchors |

## 2. Install and run

### Requirements

- Python **3.10+** recommended; desktop environment with Tkinter.
- For acquisition, install dependencies in `requirements.txt`:

```text
numpy>=1.24
sounddevice>=0.4.6
soundfile>=0.12
pylsl>=1.16
bleak>=0.21
polar-python
matplotlib>=3.7
opencv-python>=4.8
Pillow>=10.0
pandas>=2.0
```

- Appropriate camera, audio, Bluetooth, and network/LSL drivers; device-specific permissions.
- SeeTrue Station configured separately when eye-tracking LSL is required.
- Enough local disk space for continuous multimodal recordings.

### Commands

From the project directory containing `app.py`:

```bash
python -m pip install -r requirements.txt
python app.py
```

For automated software tests:

```bash
python -m unittest discover -s tests -v
```

On Windows, use the actual Python interpreter/virtual environment configured for the recording machine. Some audio devices, especially 384 kHz Ultramic configurations, require an appropriate WASAPI input and exclusive mode. Avoid changing the acquisition computer's drivers immediately before a research visit.

### Project layout (principal modules)

```text
multimodal_studio/
├── app.py                     # Tkinter GUI and experiment interaction
├── core.py                    # Session lifecycle, metadata, markers, manifest
├── block_management.py        # Trial counts, limits, visit resume logic
├── output_names.py            # File names, modality paths, legacy discovery
├── recording_integrity.py     # Live readiness and post-recording integrity
├── monitoring.py              # Rolling signal displays
├── post_analysis.py           # Offline QA GUI
├── analysis_core.py           # Stream/timing analysis and report generation
├── camera_diagnostics.py
├── devices/
│   ├── audio.py               # Microphone/WAV and audio LSL
│   ├── polar.py               # Polar H10
│   ├── polar_verity.py        # Polar Verity Sense
│   ├── seetrue.py              # Generic network LSL receiver
│   ├── camera_recorder.py     # Numbered JPEGs + frame-index LSL
│   ├── camera_controls.py     # Camera property requests and defaults
│   ├── camera_preview.py
│   ├── cameras.py
│   └── windows_audio.py
├── benchmarks/
│   ├── benchmark_camera_frames.py
│   └── README_CAMERA_FRAMES.md
├── tests/
└── requirements.txt
```

## 3. Interface and operational workflow

The main GUI tabs are **Experiment dashboard**, **Polar Sensor**, **Microphone**, **Cameras**, **Network LSL**, **Live monitoring**, **Post-experiment analysis**, and **Logs & diagnostics**.

### Before each participant visit

1. Verify approvals, consent, coded participant identity, and local recording destination.
2. Set **Centre** (`CPH`, `KUH`, `JNS`), **Participant**, **Visit** (`V01` etc.), and **Phase** (`Training`, `EndTraining`, `Retention`, `Refresher`).
3. Review **Max trials**. It defaults to **5** for current defined phases and may be changed to 1–5 before the visit's first recording. A saved visit fixes its allowed maximum.
4. Choose the devices required by this visit; **No Physiological Sensor** is the startup default.
5. For cameras, scan, assign distinct camera indices to enabled slots, set/test exposure and field of view, and verify output frame dimensions and frame rate.
6. For microphone/Ultramic, refresh real audio endpoints, select the intended input, verify supported sample rate, and inspect signal levels.
7. For H10 or Verity Sense, scan and select the physical BLE device; do **not** select both. For external SeeTrue/LSL, discover the correct source and select channels.
8. Verify SeeTrue calibration/validation **in SeeTrue Station**; confirm the relevant stream appears and contains samples in IMMSS.
9. Run a short Preview/Test and check each selected device's readiness, storage, and appropriate signal/data quality.
10. Start the formal session once. Click the smart trial button to mark `T01_START`; later end trial 1, and repeat for the configured number of trials. The acquisition continues between trials.
11. Stop the session. Inspect completion status, manifest, camera/frame indices, and a sample of each recorded file before the participant leaves.

### Controls and semantics

| Control | Effect |
|---|---|
| **PREVIEW / STOP PREVIEW** | Temporary pre-session monitoring; preview acquisition is not a formal participant session |
| **START SESSION** | Creates a unique recording segment, initializes selected devices, waits for live readiness, begins formal recording |
| **Smart trial button** | Toggles Start Trial → End Trial → Start Next Trial, without restarting devices |
| **STOP** | Closes or interrupts an active trial, stops recorders, finalizes status/files, and releases owned LSL outlets |
| **Manual marker** | Adds an arbitrary timestamped event to the session's event file and marker LSL stream |
| **Post-experiment analysis** | Reviews already captured sessions; should not access active acquisition handles |

**Shortcut:** `Ctrl+Shift+T` is actually bound to the trial action in `app.py`. One GUI label still contains `Ctrl+Shift+B` from the older block interface; treat that label as stale until corrected.

**Trial boundaries:** Trial `T01` has `T01_START` and `T01_END` markers. A Stop during an active trial creates `T01_INTERRUPTED`. The next attempt should use the visit-recovery rules and a new recording segment; never overwrite prior data. Trial-start/trial-end markers are experimenter annotations, not automatically recognized surgical actions.

## 4. SYMPEX identities, visits, and trials

The study-specific data model is:

**Study → Centre → Coded participant → Visit/training session → Recording segment → Trial markers.**

- **Participant**: coded student identity, for example `P001`; do not put names or contact information into acquisition folders.
- **Training session / visit**: `V01`–`V99`, with phases `Training`, `EndTraining`, `Retention`, `Refresher`. Visit number is not inherently calendar week number.
- **Recording session**: a unique UUID and timestamped folder; normally one continuous acquisition per visit, but a technical failure may require an additional segment.
- **Trial**: a single tympanostomy attempt, numbered `T01`–`T05` under the current limit, identified by markers rather than separate video/audio/physiology recordings.

The manager inspects existing visit metadata and markers when reopening a visit. It attempts to resume from the earliest unfinished trial; completed trial markers are not intentionally repeated. It also refuses to silently change the allowed trial count after a visit has a saved configuration. A partially completed technical recording does **not** imply the biological trial was successful.

A `B01` marker may appear in **legacy** recordings; older block concepts should not automatically be interpreted as individual procedures without checking their original protocol.

## 5. Devices and acquisition

### 5.1 Cameras (slots 1–3)

- Default requested output: **640 × 480**, **30 fps**. Actual sensor delivery is backend/driver dependent; the application records measured rate/metadata and may resize captured frames to output dimensions.
- Main recorder: `devices/camera_recorder.py`; property handling: `devices/camera_controls.py`.
- Each enabled camera saves a **numbered JPEG sequence**, not MP4:

```text
video/
├── <PREFIX>camera_1_frames/
│   ├── 00000000.jpg
│   ├── 00000001.jpg
│   └── ...
├── <PREFIX>camera_1_frames.csv
└── <PREFIX>camera_1_metadata.json
```

- JPEG quality default: **85**. A dedicated capture thread sends frames to a **bounded 32-frame writer queue**. Slow disk writes may drop frames; those drops are recorded rather than disguised as a stable 30 fps acquisition.
- A frame number is assigned only after **successful JPEG write**; the same zero-based index appears in the index CSV and `IMS_CameraN_Frames` LSL notification.
- Camera frame LSL channel order: `FrameNumber`, `Width`, `Height`, `MonotonicCaptureTime` (4 `double64` channels). The LSL sample timestamp is the host estimate after `cap.read()`.
- **Frames themselves are not transmitted over LSL.** The stream carries frame number, dimensions, and timing information.
- Timestamp values do **not** establish the instant of physical sensor exposure.
- Camera metadata also stores device index, backend, original dimensions, requested settings, estimated actual capture fps, captured/saved/dropped frame counts, LSL publish failures, total JPEG bytes, and an ordered-content SHA-256 digest.

### 5.2 Microphones and Ultramic

- Uses `sounddevice`/PortAudio plus `soundfile`; supports standard microphones and compatible Ultramic input configurations.
- Records **WAV** plus timing information, while separately publishing chunked audio over LSL, without blocking the real-time audio callback.
- The selected endpoint, host API, rate, and channel count matter. Ultramic 384 kHz setups may require the appropriate **WASAPI exclusive** endpoint; Windows driver behavior must be tested.
- Acquisition queue overflow, writer failures, callback errors, and LSL queue drops are reported.
- Audio timestamps based on PortAudio ADC timing are **estimates** in the LSL clock domain; they are not direct measurements of microphone membrane excitation.
- On Stop, pending queued LSL chunks are discarded as part of broadcast shutdown rather than being sent after recording ends.

### 5.3 Polar Sensor — one exclusive choice

| Choice | Acquisition |
|---|---|
| **None (default)** | No Polar BLE connection; other devices operate normally |
| **Polar H10** | HR/RR, optional ECG and accelerometer; existing recorder `devices/polar.py` |
| **Polar Verity Sense** | Optical HR/PPG and optional accelerometer according to enabled settings and actual device/firmware support; recorder `devices/polar_verity.py` |

- Only **one** Polar sensor is permitted in the same recording; the session manager also enforces exclusivity independently of the GUI.
- BLE discovery/connection and live-data readiness are sensor-specific. When selected, Polar availability is checked before formal acquisition starts.
- H10 and Verity use **different physiological modalities**: ECG-derived beat-to-beat intervals and optical pulse measures should not be assumed interchangeable for HRV analysis.
- The software does not restore a previously selected physiological sensor at startup.
- Record a participant's sensor type across visits; any change should be documented for later analysis.
- **Implementation caution:** the current Verity `_file()` method writes `polar_verity_*.csv` directly to the passed session folder, unlike the H10 recorder's `session_file()` routing. Verify that files land in `physiology/` before relying on uniform modality placement; this deserves a code fix/test.

### 5.4 SeeTrue and network LSL

- SeeTrue Station is responsible for eye-tracker calibration and validation. IMMSS is an **LSL receiver/recorder**, not a calibration controller.
- In **Network LSL**, discover available sources, choose the intended source and select channels; channel order and labels are determined by the broadcaster.
- Writes selected samples to a CSV and an accompanying channel-index/label mapping JSON. Preserve source UID and mapping for reproducibility.
- Supports more than just SeeTrue; other discoverable streams may be captured as generic network LSL sources.
- Network loss/interruption counters and sample counts are part of recording-integrity checks.
- Receiving samples does not prove that gaze was calibrated or suitable for scientific analyses.

## 6. LSL streams and shutdown

### IMMSS-owned LSL stream families

| Stream | Role | Scope |
|---|---|---|
| `IMS_Experiment_Markers` | Event/trial markers (`RECORDING_START`, `T01_START`, etc.) | Recording session only |
| `IMS_Session_Metadata` | One JSON sample plus descriptor describing the session's data organization | SYMPEX recording session only |
| `IMS_Camera1_Frames`, `IMS_Camera2_Frames`, `IMS_Camera3_Frames` | Saved JPEG frame indices and timing | Selected cameras during session |
| `IMS_Microphone_Audio` or `IMS_Ultramic384K_Audio` | Audio samples/chunks | Selected audio input during session |
| Polar H10 streams | Enabled HR/ECG/ACC etc., with model-specific names | H10-selected session |
| `IMS_PolarVerity_*` | Enabled Verity HR/PPG/ACC streams | Verity-selected session |
| SeeTrue/external input streams | **Not created by IMMSS**; IMMSS subscribes to them | Controlled by external broadcaster |

The LSL stream source IDs for session markers and camera frame outlets are newly generated per recording. `IMS_Session_Metadata` publishes the **data-organization JSON**, not a continuously updated copy of all final session metadata/trial results.

**When Stop is pressed:** the marker and metadata outlet references are released, camera outlets are released on writer completion, audio publication is shut down, and the selected Polar recorder is stopped. An LSL resolver may cache departed streams briefly. External SeeTrue Station streams are independent; IMMSS cannot stop their broadcasters.

**Important:** LSL provides clock/timestamp tools; it does not by itself validate physical alignment of camera exposure, USB audio ADC and Bluetooth/optical sensors. Initial visual alignment in IMMSS Reviewer has been reported, with quantitative physical-event validation to be documented separately.

**XDF / LabRecorder:** This source does **not** automatically create `.xdf` files or control LabRecorder. SeeTrue + LabRecorder path presetting, automatic pairing, XDF monitoring, and path logging were discussed as prospective functionality, **not implemented** in the inspected code. External LabRecorder may capture available LSL streams if separately configured.

## 7. Directory structure, filenames, and files

### 7.1 SYMPEX visit/session organization

The recording-root path is user-selectable. If it does not already end in `SYMPEX_2027`, the study subfolder is added automatically.

```text
<DATA_ROOT>/
└── SYMPEX_2027/
    └── JNS/                         # also CPH or KUH
        └── P001/
            └── V01_Training/
                └── S20270210T093000_a1b2c3d4/
                    ├── JNS_P001_V01_Sa1b2c3d4__metadata.json
                    ├── JNS_P001_V01_Sa1b2c3d4__events.csv
                    ├── JNS_P001_V01_Sa1b2c3d4__session_manifest.json
                    ├── video/       # created if camera output is produced
                    │   ├── JNS_P001_V01_Sa1b2c3d4__camera_1_frames/
                    │   │   ├── 00000000.jpg
                    │   │   └── ...
                    │   ├── JNS_P001_V01_Sa1b2c3d4__camera_1_frames.csv
                    │   └── JNS_P001_V01_Sa1b2c3d4__camera_1_metadata.json
                    ├── audio/       # microphone WAV and time index if enabled
                    ├── eye_tracker/ # selected external LSL CSV and channel mapping
                    └── physiology/  # Polar recordings if supported by recorder
```

This is an **illustrative** session with one camera; output varies with selected devices. Empty modality folders are not intentionally created. Files inside modality folders still carry the session prefix, except individually numbered images inside their uniquely named frame directories.

**Filename prefix** (SYMPEX): `<CENTRE>_<PARTICIPANT>_<VISIT>_S<SHORT_SESSION_ID>__`.

Example: `JNS_P001_V01_Sa1b2c3d4__events.csv`.

For non-SYMPEX/legacy sessions the source retains a different participant/session-based flat layout and compatibility lookup. Do not assume every historical dataset has the same directory hierarchy.

### 7.2 Main files and ownership

| File/folder | Why it exists |
|---|---|
| `metadata.json` | Study/visit/session identity, computer, selected devices/settings, file-layout information, trial summaries written on finalization |
| `events.csv` | Timestamped recording markers, trial starts/ends/interruption and manual annotations |
| `session_manifest.json` | File inventory, relative paths, sizes/hashes, per-device readiness/integrity, completion/partial/abort status |
| `video/*camera_N_frames/` | Actual saved numbered JPEG images |
| `video/*camera_N_frames.csv` | One timestamp/index row per successfully saved image |
| `video/*camera_N_metadata.json` | Per-camera format and acquisition-performance summary (purposeful camera-specific metadata) |
| `audio/` | Microphone WAV and its timestamp companion |
| `eye_tracker/` | Captured network/SeeTrue LSL CSV and selected-channel mapping |
| `physiology/` | Sensor-specific HR/ECG/PPG/ACC CSV when enabled and routed correctly |
| `logs/application.log` | Rotating application-level diagnostics (outside session) |

The recording source internally requests a file named `config.json` but `output_names.py` maps that to **`metadata.json`** for SYMPEX. Similarly, its internal `markers.csv` request maps to **`events.csv`**. This distinction matters when reading or extending the code.

The manifest intentionally does **not** hash each JPEG file a second time at session end: each camera frame directory is summarized in one entry, using count, bytes and the camera writer's ordered-frame digest. Regular session files are individually hashed.

### 7.3 Data minimization and durability

- Files are session-unique to prevent accidental overwrites when visits are repeated or interrupted.
- Do **not** manually merge two sessions into one folder or rename only one member of a paired recording/index collection.
- Copy the **entire session directory**, including `metadata.json`, `events.csv`, `session_manifest.json`, JPEG folders, and timing companions.
- Treat `COMPLETED` as a *software-recording integrity* outcome, not proof of correct surgical execution or scientifically validated synchronization.

## 8. Metadata and integrity

### 8.1 Session metadata (schema v7 in inspected source)

Key groups include:

```json
{
  "schema_version": 7,
  "session_id": "<full UUID>",
  "computer_name": "ACQUISITION-PC-02",
  "participant": "P001",
  "study": {
    "study_id": "SYMPEX_2027",
    "centre": "JNS",
    "participant": "P001",
    "visit": "V01",
    "phase": "Training",
    "recording_scope": "visit",
    "max_trials": 5,
    "training_session": 1,
    "trial_unit": "one_tympanostomy_procedure"
  },
  "participant_metadata": {"participant_id": "P001", "centre": "JNS"},
  "training_session_metadata": {"visit_id": "V01", "training_session": 1, "phase": "Training"},
  "training_trials": [
    {"trial_id": "T01", "trial_number": 1,
     "start_lsl_time": "1234.100000000", "end_lsl_time": "1280.200000000",
     "status": "completed"}
  ],
  "physiological_sensor": {
    "model": null,
    "device_identifier": null,
    "requested_channels": [],
    "selection_policy": "H10_or_Verity_exclusive"
  },
  "data_organization": {"study_root": "SYMPEX_2027", "centre": "JNS", "visit": "V01"},
  "devices": [],
  "settings": {},
  "started_wall_time": "2027-02-10T09:30:00+02:00"
}
```

**Example only:** IDs, timestamps and subsets are illustrative. The actual `data_organization` object also includes relative session path, filename prefix, folder map, trial-storage rule, requested devices and computer name. When no sensor is selected, the current code stores `physiological_sensor.model: null` (not necessarily the literal string `"none"`).

The PI's study metadata levels are **participant → training session → training trial**. At present, the program automatically knows IDs and boundaries, **not** baseline stereovision, handedness, simulator cartridge conditions, operative quality/error ratings, or SURG-TLX scores. Those require separate approved collection and linkage.

### 8.2 Event record

Each event record contains an LSL-local-clock timestamp, marker label, and recording-session ID. Example event progression:

```text
RECORDING_START
T01_START
T01_END
T02_READY
T02_START
T02_END
...
RECORDING_STOP
```

The application may also save arbitrary typed event markers. Stop during a trial marks it `Tnn_INTERRUPTED` before the recording stop marker.

### 8.3 Integrity and session status

The session manager checks enabled recorders before formal acquisition and assesses them after stopping:

| Result | Meaning |
|---|---|
| `COMPLETED` | No acquisition problems detected by implemented software integrity checks |
| `PARTIAL` | Files saved but at least one selected device/stream showed a recognized issue |
| `ABORTED` | Startup/preflight failed; no formal recording start event committed |

Typical checks: live camera frames; saved JPEG count and index; requested versus estimated actual fps; camera writer-queue drops; microphone callbacks and saved audio, queue and publishing losses; external LSL sample count and interruptions; Polar readiness/errors. Device-level reports live under `device_readiness` and `recording_integrity` in the manifest. The integrity logic cannot confirm XDF files, native exposure times, physiological artifact rejection, or that SeeTrue Station calibration succeeded.

The manifest includes relative file paths and SHA-256 digests where appropriate. Do not treat it as a substitute for backups, independent restore checks, or institutional data protection requirements.

## 9. Preview, monitoring, and offline analysis

### Preview and monitoring

- **PREVIEW** opens temporary acquisition on selected channels to show live data; its temporary data should be removed when Preview ends. Preview is **not** a research session.
- The live-monitor panel presents rolling, decimated data such as microphone amplitude/RMS, Polar signals, and selected numeric external LSL channels. It is a diagnostic display, **not** full-rate scientific data processing.
- The Camera tab includes test preview and camera controls. A camera slot must be enabled for formal recording.
- All important status/error messages go to **Logs & diagnostics** and a rotating application log.

### Offline QA

1. Open **Post-experiment analysis**.
2. Browse to a completed session or use the most recent session.
3. Scan available CSV/frame streams; select appropriate streams and channels.
4. Optionally provide a CSV of **physical event anchors** for external timing comparison.
5. Review stream overlap, timing intervals, missing or nonpositive intervals, jitter, gaps and event offset/drift warnings.
6. Export the report; default behavior favors a compact JSON report over multiple redundant exports.

QA thresholds are configurable in the UI. They are warning thresholds, not proof of scientific acceptance; the study should define its own timing criteria from real hardware tests. The application can discover older flat exports as well as newer modality-based sessions.

**IMMSS Reviewer:** Visual synchronization checks in a reviewer have been reported as convincing. A visual check does **not** replace a documented quantitative common-event validation. If using an external reviewer application, confirm its version and import capability separately from this acquisition code.

## 10. Performance and storage

### Camera throughput

At the requested 30 fps:

| Duration | Files per camera | Three cameras |
|---|---:|---:|
| 1 minute | 1,800 JPEGs | 5,400 JPEGs |
| 10 minutes | 18,000 JPEGs | 54,000 JPEGs |
| 1 hour | 108,000 JPEGs | 324,000 JPEGs |

A prior **synthetic** JPEG writing benchmark reached approximately 495–518 aggregate frames/s for three 640×480 camera workloads, above the nominal 90 aggregate fps requirement. That benchmark did **not** include real USB camera capture, multiple simultaneous full-rate audio and BLE sensors, network interruptions, or long-session reliability; refer to `benchmarks/README_CAMERA_FRAMES.md` and reproduce the test locally.

High-detail synthetic JPEGs extrapolated to **tens of GiB per camera-hour**. Real storage varies widely with image content, quality and filesystem. A very large number of small files can slow backup, antivirus scans and directory enumeration. Test sustained capture and copy/restore operations on the intended recording PC; keep free storage headroom.

### Timing

- Camera timestamp: host time after frame retrieval, **not exposure**.
- Audio timestamps: estimated ADC mapping, not verified end-to-end physical sound onset.
- Polar streams: BLE/device timing needs separate validation against the LSL host clock.
- Network streams: incoming timestamps and channel meanings depend on external publishers.
- **Offset** = systematic timing difference; **jitter** = variation around an expected timing relationship; **drift** = gradual timing difference across a recording.

## 11. Tests and hardware validation

### Software tests

```bash
python -m unittest discover -s tests -v
python benchmarks/benchmark_camera_frames.py --help
```

The project includes tests for session identity/naming, trial controls, modality storage, Polar selection, camera frame numbering and manifest compaction, device readiness, and LSL outlet lifecycle. Historical test counts reported across earlier updates were release-specific; a count in a past message does **not** certify the present merged installation.

### Required deployment checks (document before collecting participants)

- [ ] All enabled cameras simultaneously hold an acceptable measured capture fps for a full expected visit.
- [ ] Each JPEG filename, index CSV row and published LSL frame number agree.
- [ ] Camera exposure/lighting and field of view are repeatable between centres and visits.
- [ ] Microphone WAV decodes; no unexpected silence/overrun; high-rate Ultramic endpoint works.
- [ ] Network LSL has the correct SeeTrue stream UID, channels and live sample rate.
- [ ] SeeTrue Station calibration/validation is completed and documented separately.
- [ ] Selected Polar device connects and publishes actual required channels; **no sensor** mode leaves Polar inactive.
- [ ] IMMSS-owned marker/metadata/camera/audio/Polar streams disappear after Stop and fresh streams appear after the next Start; external SeeTrue streams persist independently.
- [ ] Deliberately disconnect a camera, microphone, LSL source and Polar in test runs; verify manifest becomes partial/aborted as appropriate.
- [ ] Validate synchronization with a shared *physical* event across relevant recording modalities, measuring offset, jitter and drift.
- [ ] Test visit interruption, restart and trial recovery without overwriting existing files.
- [ ] Open and restore a copied session (not just the original directory) and verify the manifest/checksums.
- [ ] Test a complete 5-trial pilot in both expected short- and long-duration operating conditions.

## 12. Troubleshooting

| Symptom | Checks and actions |
|---|---|
| `from __future__ imports must occur at beginning` | Confirm the file was **replaced**, not appended; look for duplicate future imports or a second copied module in the file. Header comments/docstring are allowed before a future import. |
| No camera / camera busy | Rescan, check Windows camera privacy settings/USB device access, ensure no other application owns camera index, and select distinct indices across slots. |
| Requested 30 fps not achieved | Verify measured fps in camera metadata; check driver format, USB bus sharing, lighting/exposure, CPU load and storage write queue. |
| Large number of images | Expected for JPEG sequence mode. Use frame directory summaries and deliberate archival; do not delete frame-index CSV. |
| Frame numbers do not match LSL | Check LSL publication-error count, CSV and saved JPEG count; only successful JPEG writes are assigned/sent an index. |
| Microphone looks flat | Confirm correct host API and input, permissions, gain, live peak/RMS, and the saved WAV. |
| Ultramic 384 kHz fails | Select a supported WASAPI input and verify PortAudio settings; do not assume any driver/endpoint supports 384 kHz. |
| Polar does not connect | Check device power/placement, BLE adapter, prior phone pairing, correct model selected, and live packet readiness errors. |
| Verity data appears at session root | Current Verity writer may bypass modality routing; treat as a known layout inconsistency to fix, not missing data. |
| SeeTrue stream missing | Start SeeTrue Station LSL broadcast; check network/firewall, stream selection/UID and discover again. IMMSS cannot calibrate the tracker. |
| LSL stream remains visible after Stop | Refresh discovery and allow caches to expire; ensure no device worker is still active. Check logs for shutdown timeout; distinguish external SeeTrue publishers from IMMSS outlets. |
| Session status `PARTIAL` | Read per-device `recording_integrity` and `device_readiness`; preserve all data and document the affected trial/channel. |
| Session metadata or trial markers seem missing | Check the session-prefixed **`metadata.json` / `events.csv`** names rather than internal aliases `config.json` / `markers.csv`. |
| No `.xdf` file | Expected: native LabRecorder control/XDF saving is not implemented in this source. Use separately configured LabRecorder if an XDF is required. |
| Trial shortcut label says B | The current `app.py` binds **Ctrl+Shift+T**; old B label is a UI string inconsistency. |

## 13. Architecture and development guidelines

### Acquisition design

- **Do not regress the working H10 recorder** while developing Verity support. Both remain separate recorder classes behind one exclusive GUI selection.
- Keep the LSL publisher lifecycles **session-scoped**; release outlets on normal Stop, startup abort and exceptions. Never allow stale buffered audio or video events to leak into the next session.
- Keep camera capture nonblocking relative to disk encoding/writing; preserve explicitly measured dropped-frame counts and the 1:1 relationship of *saved JPEG ↔ index CSV ↔ published frame number*.
- Reuse `output_names.session_file()` for SYMPEX file routing. Avoid opening relative session files by constructing ad hoc paths in individual recorders.
- Keep trial boundaries in event markers and trial metadata; never create a whole duplicate set of modality recordings for each trial.
- Write only essential outputs, but retain per-device integrity and timing records needed for reproducibility.
- Make recovery explicit: preserve earlier segments, use a new immutable recording-session ID, and record interruptions.
- Maintain backward read compatibility for legacy flat sessions and `Bnn` event files; do not silently reinterpret their scientific semantics.
- Preserve identifiable data separation: research metadata should be coded; never broadcast names, contact details or unnecessary absolute filesystem paths over LSL.
- When adding author metadata, use **Gulraiz**.

### Suggested regression coverage for future changes

1. Mutual exclusion and startup default `none` for Polar.
2. Camera 640×480/30-fps requested settings, saved JPEG indices, LSL frame labels, queue-drop detection.
3. Trial max 1–5, single-button state transitions, interruption/resume and no overwriting.
4. Modality folder routing, manifest relative paths and stream mapping.
5. LSL outlet release on normal Stop, unexpected exceptions and failed startup.
6. Selected device readiness and appropriately conservative final integrity status.
7. Reopening and analyzing legacy files after a storage or marker format change.

## 14. Study governance and known gaps

IMMSS is engineered for research acquisition; study compliance still requires a finalized and approved protocol and local deployment validation. The supplied *SYMPEX Study 1: Tympanostomy 2027* working draft requests standardized simulator settings, five procedures per training visit, questionnaires, prior-experience documentation, expert ratings, and longitudinal retention/refresher assessments. It also leaves some allocation/visit/intervention decisions to investigators. The application does **not** replace these study procedures.

**Not implemented or not verified in the inspected application:**

- Formal SURG-TLX entry/scoring; complete baseline stereovision/fine motor questionnaire workflow; independent blinded expert video rating.
- Automatic determination of successful tube insertion, procedural errors, or trial outcomes beyond timing/completed/interrupted markers.
- SeeTrue calibration or an automated eye-tracker calibration-acceptance decision; SeeTrue Station owns calibration.
- Automatic LabRecorder process control, XDF file creation, pre-set XDF paths, verified SeeTrue–LabRecorder pairing or control-channel acknowledgement.
- Quantitative certification of LSL/physical-device synchronization solely from visual review.
- Guarantees of real camera 30-fps delivery, full USB/CPU/disk scheduling, or uninterrupted multi-hour capture.
- Verified automatic Polar reconnection/stream recovery after every physical BLE failure.

**Known source-level inconsistencies to address:**

1. `app.py` has a stale `Ctrl+Shift+B` label while actual trial key bindings are `Ctrl+Shift+T`.
2. `devices/polar_verity.py` writes Verity CSVs directly to its session folder instead of consistently using `output_names.session_file()`.
3. Some older documentation/comments refer to MP4 cameras or blocks; current output is JPEG frames and `Tnn` trial markers.
4. The cumulative build is assembled from sequential updates; re-run all tests and a complete acquisition pilot on the installed deployment source before considering it a frozen release.

### Maintainer guidance

Keep this README together with the actual deployed code revision. Every change to schema version, filename behavior, modality folder mapping, LSL stream definition or integrity criteria must update the corresponding README section and regression tests. Store a copy of the deployed software version and approved study protocol version alongside the participant's session metadata.

---

**Author:** Gulraiz  
**Project:** Intelligent Multimodal Microsurgery Studio (IMMSS) / SYMPEX Study 1  
**Document status:** Source-grounded technical README; hardware validation and investigator protocol approvals remain separate.
