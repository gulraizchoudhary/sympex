Preview visibility diagnostics (27 Oct 2026)

- Preview now automatically enables the selected Microphone if no sensors were enabled.
- A failed recorder start is marked ERROR; Preview refuses to report success when none start.
- Live Monitoring displays received frames and saved frames separately.
- Plot errors are sent to the Activity log instead of stopping the UI polling cycle.
- Host timing can sometimes be nonsensical: such timestamps use an arrival-based fallback for the live DISPLAY only; the original capture timestamp remains in recording metadata.
- WASAPI Exclusive high-rate mode remains enabled for 384000 Hz.

Test steps:
1. Select Microphone (UltraMic384K_PRO 16bit r0) / WASAPI, set 384000 Hz.
2. Click Preview, or first enable Microphone in left sidebar.
3. Live Monitoring opens automatically. Watch Received and Peak/RMS.
4. If Received rises but graph is blank, read Monitoring errors in Activity.
5. If Received remains zero, read Microphone ERROR or DROPPED in Activity.
6. Stop Preview before reopening sound settings or changing endpoint.

This build passed Python compilation and 21 simulated unit tests. Hardware testing remains necessary.
