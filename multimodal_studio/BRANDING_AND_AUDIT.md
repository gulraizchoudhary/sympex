# Intelligent Multimodal Microsurgery Studio (IMMSS)

The long-form branding is **Intelligent Multimodal Microsurgery Studio (IMMSS)**. Existing LSL `IMS_` names, recorded filenames, and session file formats are intentionally unchanged for interoperability with prior experiments.

## Verified changes
- Camera re-scan preserves each existing camera selection by capture index when still present, rather than assigning camera 0 to every camera slot.
- Preview stops only devices that successfully started, avoiding unnecessary cleanup calls for failed starts.
- Dashboard selected count includes selected network LSL sources.
- Readiness and recorder changes were deliberately limited: original device drivers and recording backends are retained.

## Scientific/data integrity limitations
- Camera frame timestamps are host-side after reading a frame, not sensor exposure timestamps; MP4 writer FPS may differ from actual capture rate.
- LSL timestamps alone do not establish physical cross-device synchronization.
- Physical devices (Polar H10 BLE, UltraMic WASAPI Exclusive, camera drivers, SeeTrue) require on-site Windows tests.
- UI launch smoke tests with dependency stubs do not prove hardware recording.

## Suggested Windows preflight
1. Open app, verify all tabs and enable toggles.
2. Scan cameras, select distinct indices, rescan and ensure assignments persist.
3. Select WASAPI UltraMic, verify 384000 Hz in Preview and actual saved WAV.
4. Connect Polar; confirm HR/ECG/ACC readiness before marker.
5. Enable two LSL outlets with the same name but different UIDs and channel maps.
6. Record a brief camera + audio + Polar + network LSL session; inspect files, logs, manifest and post-analysis charts.
7. Force one device failure and check other streams continue; verify timestamp continuity and partial-session status.
