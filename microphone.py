"""Microphone discovery, continuous WAV/CSV recording and LSL output."""
import csv
import math
import os
import threading
import wave
from collections import deque
from typing import Any, Optional
import numpy as np
import sounddevice as sd
from pylsl import StreamInfo, StreamOutlet, local_clock
from config import MIC_SAMPLE_RATE, MIC_CHANNELS, MIC_BLOCKSIZE, MIC_DTYPE, MIC_VISUAL_WINDOW_SECONDS

class MicrophoneLSLRecorder:
    """Record the laptop microphone to WAV + timestamp CSV and publish audio to LSL.

    Audio is acquired by sounddevice on its callback thread.  Each block is assigned
    sample timestamps on the pylsl local_clock() timebase.  The same timestamps are
    used for the LSL samples and the timestamp CSV, which makes the saved audio
    alignable with the Polar streams and markers.
    """

    def __init__(self, log_callback=None) -> None:
        self.log_callback = log_callback or (lambda _msg: None)
        self.stream = None
        self.outlet: Optional[StreamOutlet] = None
        self.wav_handle = None
        self.ts_handle = None
        self.ts_writer = None
        self.file_lock = threading.Lock()
        self.active = False
        self.sample_index = 0
        self.device = None

        # Visualization state is intentionally independent from recording storage.
        # Only the most recent few seconds are retained here; WAV/CSV/LSL continue
        # for the entire recording until stop() is called.
        self.visual_lock = threading.Lock()
        self.visual_buffer = deque(maxlen=int(MIC_SAMPLE_RATE * MIC_VISUAL_WINDOW_SECONDS))
        self.current_dbfs = -100.0

    def get_visualization_data(self) -> tuple[np.ndarray, float]:
        """Return a thread-safe copy of recent normalized audio and current dBFS."""
        with self.visual_lock:
            return np.asarray(self.visual_buffer, dtype=np.float32), float(self.current_dbfs)

    @staticmethod
    def _transport_hint(name: str) -> str:
        """Best-effort transport hint; PortAudio does not expose physical bus type."""
        n = name.lower()
        if any(x in n for x in ("bluetooth", "hands-free", "hands free", "headset", "airpods")):
            return "Bluetooth/headset (name hint)"
        if any(x in n for x in ("usb", "webcam", "camera")):
            return "USB/external (name hint)"
        if any(x in n for x in ("realtek", "microphone array", "internal", "built-in", "built in")):
            return "Built-in (name hint)"
        return "Transport not exposed by PortAudio"

    def list_input_devices(self) -> list[dict[str, Any]]:
        """Return one preferred, currently openable endpoint per microphone name.

        Windows commonly exposes the same microphone through MME, DirectSound,
        WASAPI and WDM-KS.  We group those duplicates and prefer WASAPI.  An
        endpoint is included only if PortAudio can open it using the recording
        format, so the GUI represents usable inputs rather than every enumerated
        endpoint.
        """
        raw_devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        candidates: list[dict[str, Any]] = []

        for idx, dev in enumerate(raw_devices):
            max_inputs = int(dev.get("max_input_channels", 0))
            if max_inputs < MIC_CHANNELS:
                continue
            host_idx = int(dev.get("hostapi", -1))
            host_name = (str(hostapis[host_idx].get("name", "Unknown"))
                         if 0 <= host_idx < len(hostapis) else "Unknown")
            try:
                sd.check_input_settings(
                    device=idx, channels=MIC_CHANNELS, dtype=MIC_DTYPE,
                    samplerate=MIC_SAMPLE_RATE,
                )
            except Exception:
                continue

            name = str(dev.get("name", f"Input {idx}"))
            candidates.append({
                "index": idx,
                "name": name,
                "channels": max_inputs,
                "default_samplerate": float(dev.get("default_samplerate", 0.0)),
                "hostapi": host_name,
                "transport": self._transport_hint(name),
                "usable": True,
            })

        # Collapse duplicate host-API representations of the same named endpoint.
        # Prefer WASAPI on Windows, then Core Audio, then ALSA, then other APIs.
        def api_rank(d: dict[str, Any]) -> tuple[int, int]:
            h = d["hostapi"].lower()
            if "wasapi" in h:
                rank = 0
            elif "core audio" in h:
                rank = 1
            elif "alsa" in h:
                rank = 2
            elif "directsound" in h:
                rank = 3
            elif "mme" in h:
                rank = 4
            else:
                rank = 5
            return rank, int(d["index"])

        grouped: dict[str, list[dict[str, Any]]] = {}
        for d in candidates:
            key = " ".join(d["name"].lower().split())
            grouped.setdefault(key, []).append(d)

        preferred = [sorted(group, key=api_rank)[0] for group in grouped.values()]
        preferred.sort(key=lambda d: (api_rank(d), d["name"].lower()))
        return preferred

    def validate_device(self, device_index: int) -> dict[str, Any]:
        """Verify that the selected device is still present and accepts our format."""
        dev = sd.query_devices(device_index, "input")
        if int(dev["max_input_channels"]) < MIC_CHANNELS:
            raise RuntimeError(
                f"Selected microphone provides only {dev['max_input_channels']} input channel(s)."
            )
        sd.check_input_settings(
            device=device_index,
            channels=MIC_CHANNELS,
            dtype=MIC_DTYPE,
            samplerate=MIC_SAMPLE_RATE,
        )
        return dev

    def _setup_lsl(self, device_name: str, device_index: int, native_rate: float,
                   hostapi: str = "Unknown", transport: str = "Unknown") -> None:
        source_id = "LaptopMic_" + "".join(c if c.isalnum() else "_" for c in device_name)
        info = StreamInfo(
            "Microphone_Audio",
            "Audio",
            MIC_CHANNELS,
            float(MIC_SAMPLE_RATE),
            "float32",
            source_id,
        )
        channels = info.desc().append_child("channels")
        ch = channels.append_child("channel")
        ch.append_child_value("label", "Microphone")
        ch.append_child_value("unit", "normalized")
        info.desc().append_child_value("hardware", device_name)
        info.desc().append_child_value("device_name", device_name)
        info.desc().append_child_value("device_index", str(device_index))
        info.desc().append_child_value("recording_sample_rate", str(MIC_SAMPLE_RATE))
        info.desc().append_child_value("device_default_sample_rate", str(native_rate))
        info.desc().append_child_value("channels", str(MIC_CHANNELS))
        info.desc().append_child_value("host_api", hostapi)
        info.desc().append_child_value("transport_hint", transport)
        self.outlet = StreamOutlet(info)

    def start(self, session_folder: str, device: Optional[int] = None) -> None:
        if self.active:
            return
        if device is None:
            raise RuntimeError("No microphone has been selected.")
        self.device = int(device)
        dev_info = self.validate_device(self.device)
        device_name = str(dev_info["name"])
        native_rate = float(dev_info["default_samplerate"])
        hostapis = sd.query_hostapis()
        host_idx = int(dev_info.get("hostapi", -1))
        hostapi = (str(hostapis[host_idx].get("name", "Unknown"))
                   if 0 <= host_idx < len(hostapis) else "Unknown")
        transport = self._transport_hint(device_name)
        self._setup_lsl(device_name, self.device, native_rate, hostapi, transport)

        wav_path = os.path.join(session_folder, "Microphone_recording.wav")
        ts_path = os.path.join(session_folder, "Microphone_timestamps.csv")

        self.wav_handle = wave.open(wav_path, "wb")
        self.wav_handle.setnchannels(MIC_CHANNELS)
        self.wav_handle.setsampwidth(np.dtype(MIC_DTYPE).itemsize)
        self.wav_handle.setframerate(MIC_SAMPLE_RATE)

        self.ts_handle = open(ts_path, "w", newline="", encoding="utf-8")
        self.ts_writer = csv.writer(self.ts_handle)
        self.ts_writer.writerow(["Sample_Index", "LSL_Timestamp"])
        self.ts_handle.flush()
        self.sample_index = 0
        with self.visual_lock:
            self.visual_buffer.clear()
            self.current_dbfs = -100.0

        self.stream = sd.InputStream(
            device=device,
            samplerate=MIC_SAMPLE_RATE,
            channels=MIC_CHANNELS,
            dtype=MIC_DTYPE,
            blocksize=MIC_BLOCKSIZE,
            callback=self._callback,
        )
        self.active = True
        try:
            self.stream.start()
        except Exception:
            self.active = False
            self.close()
            raise
        self.log_callback(f"Microphone recording started: {device_name} @ {MIC_SAMPLE_RATE} Hz")

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            self.log_callback(f"Microphone status: {status}")
        if not self.active:
            return

        # Timestamp the end of this acquired block in the common LSL clock, then
        # reconstruct uniformly spaced sample timestamps backwards through it.
        block_end = local_clock()
        first_ts = block_end - (frames - 1) / MIC_SAMPLE_RATE
        timestamps = [first_ts + i / MIC_SAMPLE_RATE for i in range(frames)]

        pcm = np.asarray(indata[:, 0], dtype=np.int16).copy()
        normalized = pcm.astype(np.float32) / 32768.0

        # GUI-only rolling copy. This never controls or truncates recording.
        rms = float(np.sqrt(np.mean(normalized * normalized))) if normalized.size else 0.0
        dbfs = 20.0 * math.log10(max(rms, 1e-5))
        with self.visual_lock:
            self.visual_buffer.extend(normalized.tolist())
            self.current_dbfs = max(-100.0, dbfs)

        outlet = self.outlet
        if outlet is not None:
            # Explicit timestamps ensure this stream uses the same LSL clock as Polar.
            for value, ts in zip(normalized, timestamps):
                outlet.push_sample([float(value)], timestamp=ts)

        with self.file_lock:
            if self.wav_handle is not None:
                self.wav_handle.writeframesraw(pcm.tobytes())
            if self.ts_writer is not None:
                start_idx = self.sample_index
                self.ts_writer.writerows(
                    (start_idx + i, ts) for i, ts in enumerate(timestamps)
                )
                self.sample_index += frames
                if self.ts_handle is not None:
                    self.ts_handle.flush()

    def stop(self) -> None:
        if not self.active and self.stream is None:
            return
        self.active = False
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        self.close()
        self.log_callback("Microphone recording stopped.")

    def close(self) -> None:
        with self.file_lock:
            if self.wav_handle is not None:
                try:
                    self.wav_handle.close()
                except Exception:
                    pass
                self.wav_handle = None
            if self.ts_handle is not None:
                try:
                    self.ts_handle.flush()
                    self.ts_handle.close()
                except Exception:
                    pass
                self.ts_handle = None
            self.ts_writer = None
        self.outlet = None
