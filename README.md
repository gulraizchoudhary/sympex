# Polar H10 + microphone recorder

Run with Python 3.10+:

```bash
pip install -r requirements.txt
python main.py
```

## Modules

- `main.py` — application entry point
- `app.py` — Tkinter UI, Polar callbacks, session control and plotting
- `microphone.py` — microphone selection, WAV/CSV recording, LSL and visualization buffer
- `async_loop.py` — dedicated BLE event loop thread
- `config.py` — shared constants and recording path

Recordings are stored in `Participant_Data` **beside** the `polar_h10_modular` application directory, not inside it.

The microphone records continuously until STOP; its five-second rolling waveform is only a display buffer.

**Timing caveat:** Microphone sample timestamps in this inherited implementation are estimated from `pylsl.local_clock()` at callback time, rather than hardware ADC timestamps. Verify latency and drift before using them as precise cross-device acquisition timestamps.

**Performance caveat:** The inherited microphone callback writes per-sample CSV timestamps and pushes individual LSL samples at 48 kHz. This can overload a Python audio callback on some computers. Test for audio dropouts and timestamp gaps before experimental use; a queued writer and chunked LSL publishing would be preferable for production recording.
