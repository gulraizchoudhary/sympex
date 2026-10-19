"""Tkinter application: device controls, acquisition callbacks, session coordination and plots."""
import asyncio
import csv
import math
import os
import queue
import threading
from collections import deque
from datetime import datetime
from typing import Any, Optional

import numpy as np
import sounddevice as sd
import tkinter as tk
from tkinter import messagebox, ttk, scrolledtext
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from bleak import BleakScanner
from polar_python import PolarDevice
from polar_python.models import ACCData, ECGData, HRData
from pylsl import StreamInfo, StreamOutlet, local_clock

from config import (
    APP_TITLE, ECG_SAMPLE_RATE, ECG_RESOLUTION, ACC_SAMPLE_RATE, ACC_RESOLUTION,
    ACC_RANGE_G, MIC_SAMPLE_RATE, MIC_CHANNELS, MIC_BLOCKSIZE, MIC_DTYPE,
    MIC_VISUAL_WINDOW_SECONDS, MIC_VISUAL_MAX_POINTS, PLOT_WINDOW_SECONDS,
    PLOT_UPDATE_MS, UI_QUEUE_UPDATE_MS, DARK_BG, DARKER_BG, ACCENT_COLOR,
    TEXT_COLOR, SECONDARY_TEXT, SUCCESS_COLOR, WARNING_COLOR, ERROR_COLOR,
    BORDER_COLOR, DATA_DIR
)
from async_loop import AsyncLoopThread
from microphone import MicrophoneLSLRecorder

class PolarH10LSLApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1450x920")
        self.root.minsize(1100, 760)
        self.root.configure(bg=DARK_BG)

        self.closing = False
        self.ui_queue: queue.Queue = queue.Queue()
        self.ble = AsyncLoopThread()

        # BLE / Polar state
        self.ble_device = None
        self.polar_device: Optional[PolarDevice] = None
        self.connected = False
        self.ecg_active = False
        self.acc_active = False

        # Recording state
        self.recording = False
        self.recording_start_time: Optional[float] = None
        self.recording_stop_time: Optional[float] = None
        self.current_participant_id: Optional[str] = None
        self.session_folder: Optional[str] = None
        self.current_interval_start: Optional[float] = None
        self.intervals: list[tuple[float, float]] = []
        self.marked_timestamps: list[float] = []

        # Shared data. Polar callbacks execute on the BLE asyncio thread while
        # Tkinter plots run on the main thread, so buffer/file access is locked.
        self.data_lock = threading.Lock()
        self.file_lock = threading.Lock()

        self.hr_buffer = deque(maxlen=5000)          # (lsl_time, bpm)
        self.rr_buffer = deque(maxlen=5000)          # (lsl_time, ms)
        self.ecg_buffer = deque(maxlen=ECG_SAMPLE_RATE * 60)  # 60 s
        self.acc_buffer = deque(maxlen=ACC_SAMPLE_RATE * 60)  # (t, x, y, z)

        self.last_hr: Optional[float] = None
        self.last_rr: Optional[float] = None

        # Persistent recording file handles/writers
        self._files: dict[str, Any] = {}
        self._writers: dict[str, csv.writer] = {}

        # LSL outlets
        self.hr_outlet: Optional[StreamOutlet] = None
        self.rr_outlet: Optional[StreamOutlet] = None
        self.ecg_outlet: Optional[StreamOutlet] = None
        self.acc_outlet: Optional[StreamOutlet] = None
        self.marker_outlet: Optional[StreamOutlet] = None

        # Independent laptop microphone recorder / LSL outlet
        self.microphone = MicrophoneLSLRecorder(log_callback=self.post_log)
        self.mic_devices: list[dict[str, Any]] = []

        self._configure_ttk()
        self._build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.root.after(UI_QUEUE_UPDATE_MS, self._process_ui_queue)
        self.root.after(PLOT_UPDATE_MS, self._update_plots)

        self.log("Application started.")
        self.log("BLE backend: polar-python / PolarDevice")
        self.log("HR starts first; ECG and ACC follow after 1 s each when enabled.")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _configure_ttk(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "TNotebook",
            background=DARK_BG,
            borderwidth=0,
        )
        style.configure(
            "TNotebook.Tab",
            background=DARKER_BG,
            foreground=TEXT_COLOR,
            padding=(12, 6),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", ACCENT_COLOR)],
            foreground=[("selected", DARKER_BG)],
        )
        style.configure(
            "TCombobox",
            fieldbackground=DARK_BG,
            background=DARK_BG,
            foreground=TEXT_COLOR,
            arrowcolor=TEXT_COLOR,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", DARK_BG)],
            foreground=[("readonly", TEXT_COLOR)],
        )

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=DARK_BG, padx=18, pady=12)
        header.pack(fill=tk.X)

        tk.Label(
            header,
            text="POLAR H10 RECORDER & LSL RESTREAMER",
            font=("Segoe UI", 20, "bold"),
            bg=DARK_BG,
            fg=ACCENT_COLOR,
        ).pack(anchor="w")

        tk.Label(
            header,
            text="polar-python connection layer | HR/RR + optional ECG/ACC | LSL + CSV",
            font=("Segoe UI", 10),
            bg=DARK_BG,
            fg=SECONDARY_TEXT,
        ).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(self.root, bg=DARK_BG, padx=14, pady=8)
        body.pack(fill=tk.BOTH, expand=True)

        controls = tk.Frame(
            body,
            bg=DARKER_BG,
            padx=14,
            pady=14,
            highlightbackground=BORDER_COLOR,
            highlightthickness=1,
            width=390,
        )
        controls.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        controls.pack_propagate(False)

        right = tk.Frame(body, bg=DARK_BG)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self._build_controls(controls)
        self._build_right_panel(right)

    def _section_label(self, parent: tk.Widget, text: str) -> None:
        tk.Label(
            parent,
            text=text,
            font=("Segoe UI", 10, "bold"),
            bg=DARKER_BG,
            fg=SECONDARY_TEXT,
            anchor="w",
        ).pack(fill=tk.X, pady=(12, 4))

    def _build_controls(self, parent: tk.Frame) -> None:
        # Participant/session
        self._section_label(parent, "PARTICIPANT")
        participant_row = tk.Frame(parent, bg=DARKER_BG)
        participant_row.pack(fill=tk.X)

        self.participant_var = tk.StringVar()
        self.participant_entry = tk.Entry(
            participant_row,
            textvariable=self.participant_var,
            font=("Segoe UI", 11),
            bg=DARK_BG,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            relief=tk.FLAT,
        )
        self.participant_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.participant_entry.bind("<Return>", lambda _e: self.set_participant())

        self.set_id_button = self._button(
            participant_row,
            "SET",
            self.set_participant,
            compact=True,
        )
        self.set_id_button.pack(side=tk.RIGHT, padx=(8, 0))

        self.session_label = tk.Label(
            parent,
            text="No active participant",
            font=("Segoe UI", 9, "italic"),
            bg=DARKER_BG,
            fg=SECONDARY_TEXT,
            anchor="w",
            wraplength=350,
        )
        self.session_label.pack(fill=tk.X, pady=(5, 0))

        # Device
        self._section_label(parent, "POLAR H10 DEVICE")
        device_row = tk.Frame(parent, bg=DARKER_BG)
        device_row.pack(fill=tk.X)

        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(
            device_row,
            textvariable=self.device_var,
            state="readonly",
            width=31,
        )
        self.device_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.scan_button = self._button(
            device_row,
            "SCAN",
            self.scan_device,
            compact=True,
        )
        self.scan_button.pack(side=tk.RIGHT, padx=(8, 0))

        # Stream config
        self._section_label(parent, "STREAMS")
        self.hr_var = tk.BooleanVar(value=True)
        self.ecg_var = tk.BooleanVar(value=True)
        self.acc_var = tk.BooleanVar(value=True)
        self.mic_var = tk.BooleanVar(value=True)

        tk.Checkbutton(
            parent,
            text="HR + RR (always enabled)",
            variable=self.hr_var,
            state=tk.DISABLED,
            bg=DARKER_BG,
            fg=TEXT_COLOR,
            disabledforeground=TEXT_COLOR,
            selectcolor=DARK_BG,
            activebackground=DARKER_BG,
            activeforeground=TEXT_COLOR,
        ).pack(anchor="w")

        tk.Checkbutton(
            parent,
            text="ECG 130 Hz / 14 bit",
            variable=self.ecg_var,
            bg=DARKER_BG,
            fg=TEXT_COLOR,
            selectcolor=DARK_BG,
            activebackground=DARKER_BG,
            activeforeground=TEXT_COLOR,
        ).pack(anchor="w")

        tk.Checkbutton(
            parent,
            text="ACC 25 Hz / 16 bit / ±2 G",
            variable=self.acc_var,
            bg=DARKER_BG,
            fg=TEXT_COLOR,
            selectcolor=DARK_BG,
            activebackground=DARKER_BG,
            activeforeground=TEXT_COLOR,
        ).pack(anchor="w")

        tk.Checkbutton(
            parent,
            text="Microphone 48 kHz + LSL",
            variable=self.mic_var,
            command=self._update_mic_controls,
            bg=DARKER_BG,
            fg=TEXT_COLOR,
            selectcolor=DARK_BG,
            activebackground=DARKER_BG,
            activeforeground=TEXT_COLOR,
        ).pack(anchor="w")

        mic_row = tk.Frame(parent, bg=DARKER_BG)
        mic_row.pack(fill=tk.X, pady=(4, 0))
        self.mic_device_var = tk.StringVar()
        self.mic_device_combo = ttk.Combobox(
            mic_row, textvariable=self.mic_device_var, state="readonly", width=27
        )
        self.mic_device_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.mic_device_combo.bind("<<ComboboxSelected>>", self._on_mic_selected)
        self.refresh_mic_button = self._button(
            mic_row, "REFRESH", self.refresh_microphones, compact=True
        )
        self.refresh_mic_button.pack(side=tk.RIGHT, padx=(8, 0))

        self.mic_device_info_var = tk.StringVar(value="Searching for input devices...")
        tk.Label(
            parent, textvariable=self.mic_device_info_var, font=("Segoe UI", 8),
            bg=DARKER_BG, fg=SECONDARY_TEXT, anchor="w", wraplength=350,
        ).pack(fill=tk.X, pady=(3, 0))
        self.root.after(100, self.refresh_microphones)

        # Connection
        self._section_label(parent, "CONNECTION")
        self.connect_button = self._button(parent, "CONNECT", self.connect_device)
        self.connect_button.pack(fill=tk.X, pady=3)

        self.status_var = tk.StringVar(value="Status: Not connected")
        tk.Label(
            parent,
            textvariable=self.status_var,
            font=("Segoe UI", 9),
            bg=DARKER_BG,
            fg=SECONDARY_TEXT,
            anchor="w",
            wraplength=350,
        ).pack(fill=tk.X, pady=(5, 0))

        # Live values
        self._section_label(parent, "LIVE")
        live = tk.Frame(parent, bg=DARKER_BG)
        live.pack(fill=tk.X)

        self.hr_value_var = tk.StringVar(value="HR: -- bpm")
        self.rr_value_var = tk.StringVar(value="RR: -- ms")
        self.ecg_count_var = tk.StringVar(value="ECG samples: 0")
        self.acc_count_var = tk.StringVar(value="ACC samples: 0")
        self.mic_status_var = tk.StringVar(value="MIC: ready | Level: -- dBFS")

        for variable in (
            self.hr_value_var,
            self.rr_value_var,
            self.ecg_count_var,
            self.acc_count_var,
            self.mic_status_var,
        ):
            tk.Label(
                live,
                textvariable=variable,
                font=("Consolas", 10),
                bg=DARKER_BG,
                fg=TEXT_COLOR,
                anchor="w",
            ).pack(fill=tk.X)

        # Recording
        self._section_label(parent, "RECORDING")
        self.record_button = self._button(
            parent,
            "START RECORDING",
            self.toggle_recording,
            enabled=False,
        )
        self.record_button.pack(fill=tk.X, pady=3)

        self.mark_button = self._button(
            parent,
            "MARK TIMESTAMP",
            self.mark_timestamp,
            enabled=False,
        )
        self.mark_button.pack(fill=tk.X, pady=3)

        interval_row = tk.Frame(parent, bg=DARKER_BG)
        interval_row.pack(fill=tk.X, pady=3)

        self.start_interval_button = self._button(
            interval_row,
            "START INTERVAL",
            self.start_interval,
            compact=True,
            enabled=False,
        )
        self.start_interval_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))

        self.end_interval_button = self._button(
            interval_row,
            "END INTERVAL",
            self.end_interval,
            compact=True,
            enabled=False,
        )
        self.end_interval_button.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(3, 0))

        self.recording_label = tk.Label(
            parent,
            text="Not recording",
            font=("Segoe UI", 9, "italic"),
            bg=DARKER_BG,
            fg=SECONDARY_TEXT,
            anchor="w",
            wraplength=350,
        )
        self.recording_label.pack(fill=tk.X, pady=(5, 0))

        # LSL names
        self._section_label(parent, "LSL OUTLETS")
        lsl_text = (
            "HeartRate | RRinterval\n"
            "Polar_ECG | Polar_ACC\n"
            "Polar_Markers | Microphone_Audio"
        )
        tk.Label(
            parent,
            text=lsl_text,
            justify=tk.LEFT,
            font=("Consolas", 9),
            bg=DARKER_BG,
            fg=SECONDARY_TEXT,
            anchor="w",
        ).pack(fill=tk.X)

    def _button(
        self,
        parent: tk.Widget,
        text: str,
        command,
        compact: bool = False,
        enabled: bool = True,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=("Segoe UI", 9 if compact else 10, "bold"),
            bg=ACCENT_COLOR,
            fg=DARKER_BG,
            activebackground=TEXT_COLOR,
            activeforeground=DARKER_BG,
            disabledforeground=SECONDARY_TEXT,
            relief=tk.FLAT,
            padx=8 if compact else 12,
            pady=5 if compact else 8,
            state=tk.NORMAL if enabled else tk.DISABLED,
        )

    def _build_right_panel(self, parent: tk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)

        plot_tab = tk.Frame(notebook, bg=DARKER_BG)
        log_tab = tk.Frame(notebook, bg=DARKER_BG)
        notebook.add(plot_tab, text="Live data")
        notebook.add(log_tab, text="Log")

        plt.style.use("dark_background")
        self.figure, (self.ax_hr, self.ax_ecg, self.ax_acc, self.ax_mic) = plt.subplots(
            4,
            1,
            figsize=(10.5, 9.0),
            sharex=False,
        )
        self.figure.patch.set_facecolor(DARKER_BG)
        self.figure.subplots_adjust(hspace=0.55, left=0.09, right=0.97, top=0.95, bottom=0.07)

        for ax in (self.ax_hr, self.ax_ecg, self.ax_acc, self.ax_mic):
            ax.set_facecolor(DARK_BG)
            ax.grid(True, alpha=0.2)
            for spine in ax.spines.values():
                spine.set_color(BORDER_COLOR)
            ax.tick_params(colors=SECONDARY_TEXT)

        # Create the RR secondary axis once. Re-creating twinx() on every GUI
        # refresh would leak axes and progressively slow the application.
        self.ax_rr = self.ax_hr.twinx()
        self.ax_rr.set_facecolor("none")
        self.ax_rr.tick_params(colors=SECONDARY_TEXT)
        self.ax_rr.spines["right"].set_color(BORDER_COLOR)

        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_tab)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.log_widget = scrolledtext.ScrolledText(
            log_tab,
            bg=DARK_BG,
            fg=TEXT_COLOR,
            insertbackground=TEXT_COLOR,
            font=("Consolas", 10),
            wrap=tk.WORD,
        )
        self.log_widget.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self.log_widget.configure(state=tk.DISABLED)

    # ------------------------------------------------------------------
    # Thread-safe logging / UI event queue
    # ------------------------------------------------------------------

    def post_log(self, message: str) -> None:
        self.ui_queue.put(("log", message))

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] {message}\n"
        print(line, end="")
        if hasattr(self, "log_widget"):
            self.log_widget.configure(state=tk.NORMAL)
            self.log_widget.insert(tk.END, line)
            self.log_widget.see(tk.END)
            self.log_widget.configure(state=tk.DISABLED)

    def _process_ui_queue(self) -> None:
        if self.closing:
            return

        try:
            while True:
                event = self.ui_queue.get_nowait()
                kind = event[0]

                if kind == "log":
                    self.log(event[1])

                elif kind == "scan_done":
                    self._handle_scan_done(event[1])

                elif kind == "connect_done":
                    self._handle_connect_done(event[1])

                elif kind == "disconnect_done":
                    self._handle_disconnect_done(event[1])

        except queue.Empty:
            pass

        self.root.after(UI_QUEUE_UPDATE_MS, self._process_ui_queue)

    # ------------------------------------------------------------------
    # Participant / session
    # ------------------------------------------------------------------

    def set_participant(self) -> None:
        participant_id = self.participant_var.get().strip()
        if not participant_id:
            messagebox.showwarning("Participant", "Please enter a participant ID.")
            return

        if self.recording:
            messagebox.showwarning(
                "Recording active",
                "Stop the current recording before changing participant ID.",
            )
            return

        safe_id = "".join(c for c in participant_id if c.isalnum() or c in "-_ ").strip()
        if not safe_id:
            messagebox.showwarning("Participant", "Participant ID contains no valid characters.")
            return

        self.current_participant_id = safe_id
        self.session_label.config(
            text=f"Participant: {safe_id}",
            fg=SUCCESS_COLOR,
        )
        self.log(f"Participant set to: {safe_id}")

    # ------------------------------------------------------------------
    # Microphone selection
    # ------------------------------------------------------------------

    def refresh_microphones(self) -> None:
        if self.recording:
            return
        try:
            previous_index = self.get_selected_mic_index()
            self.mic_devices = self.microphone.list_input_devices()
        except Exception as exc:
            self.mic_devices = []
            self.mic_device_combo["values"] = []
            self.mic_device_var.set("")
            self.mic_device_info_var.set(f"Microphone query failed: {exc}")
            self.log(f"Microphone query failed: {type(exc).__name__}: {exc}")
            return

        labels = [f"{d['name']} — {d['hostapi']}" for d in self.mic_devices]
        self.mic_device_combo["values"] = labels
        if not self.mic_devices:
            self.mic_device_var.set("")
            self.mic_device_info_var.set("No audio input devices found")
            return

        selected_pos = 0
        if previous_index is not None:
            for pos, dev in enumerate(self.mic_devices):
                if dev["index"] == previous_index:
                    selected_pos = pos
                    break
        else:
            try:
                default_input = int(sd.default.device[0])
                for pos, dev in enumerate(self.mic_devices):
                    if dev["index"] == default_input:
                        selected_pos = pos
                        break
            except Exception:
                pass

        self.mic_device_combo.current(selected_pos)
        self._on_mic_selected()
        self.log(
            f"Found {len(self.mic_devices)} usable microphone(s) after "
            "filtering duplicate/unopenable audio endpoints."
        )

    def get_selected_mic_index(self) -> Optional[int]:
        if not hasattr(self, "mic_device_combo"):
            return None
        pos = self.mic_device_combo.current()
        if pos < 0 or pos >= len(self.mic_devices):
            return None
        return int(self.mic_devices[pos]["index"])

    def _on_mic_selected(self, _event=None) -> None:
        pos = self.mic_device_combo.current()
        if pos < 0 or pos >= len(self.mic_devices):
            self.mic_device_info_var.set("Select an input device")
            return
        dev = self.mic_devices[pos]
        native = dev["default_samplerate"]
        self.mic_device_info_var.set(
            f"Ready/usable | {dev['hostapi']} | {dev['transport']} | "
            f"max {dev['channels']} ch | default {native:.0f} Hz | "
            f"recording mono {MIC_SAMPLE_RATE} Hz"
        )

    def _update_mic_controls(self) -> None:
        enabled = bool(self.mic_var.get()) and not self.recording
        self.mic_device_combo.config(state="readonly" if enabled else tk.DISABLED)
        self.refresh_mic_button.config(state=tk.NORMAL if enabled else tk.DISABLED)

    # ------------------------------------------------------------------
    # Scan / connect / disconnect
    # ------------------------------------------------------------------

    def scan_device(self) -> None:
        if self.connected:
            messagebox.showinfo("Scan", "Disconnect before scanning for another device.")
            return

        self.scan_button.config(text="SCANNING...", state=tk.DISABLED)
        self.status_var.set("Status: Scanning for Polar H10...")
        self.log("Scanning for Polar H10...")

        future = self.ble.submit(self._scan_for_h10())
        future.add_done_callback(lambda f: self.ui_queue.put(("scan_done", f)))

    async def _scan_for_h10(self):
        # Intentionally mirrors the user's known-working minimal example.
        return await BleakScanner.find_device_by_filter(
            lambda bd, ad: bd.name and "Polar H10" in bd.name,
            timeout=8.0,
        )

    def _handle_scan_done(self, future) -> None:
        self.scan_button.config(text="SCAN", state=tk.NORMAL)

        try:
            device = future.result()
        except Exception as exc:
            self.status_var.set("Status: Scan failed")
            self.log(f"Scan failed: {type(exc).__name__}: {exc}")
            messagebox.showerror("Scan Error", f"Could not scan for Polar H10:\n\n{exc}")
            return

        if device is None:
            self.ble_device = None
            self.device_combo["values"] = []
            self.device_var.set("")
            self.status_var.set("Status: Polar H10 not found")
            self.log("No Polar H10 found. Ensure it is worn/awake and nearby.")
            messagebox.showwarning(
                "Polar H10 not found",
                "No Polar H10 was found. Ensure the sensor is awake, worn, and nearby.",
            )
            return

        self.ble_device = device
        display = f"{device.name} ({device.address})"
        self.device_combo["values"] = [display]
        self.device_combo.current(0)
        self.status_var.set(f"Status: Found {device.name}")
        self.log(f"Found {device.name} at {device.address}")

    def connect_device(self) -> None:
        if self.connected:
            self.disconnect_device()
            return

        if self.ble_device is None:
            messagebox.showwarning("Connect", "Scan for a Polar H10 first.")
            return

        if not self.current_participant_id:
            messagebox.showwarning("Participant", "Set a participant ID before connecting.")
            return

        enable_ecg = bool(self.ecg_var.get())
        enable_acc = bool(self.acc_var.get())

        self.connect_button.config(text="CONNECTING...", state=tk.DISABLED, bg=WARNING_COLOR)
        self.scan_button.config(state=tk.DISABLED)
        self.status_var.set("Status: Connecting...")
        self.log(f"Connecting to {self.ble_device.name}...")

        future = self.ble.submit(
            self._connect_and_start_streams(enable_ecg=enable_ecg, enable_acc=enable_acc)
        )
        future.add_done_callback(lambda f: self.ui_queue.put(("connect_done", f)))

    async def _connect_and_start_streams(self, enable_ecg: bool, enable_acc: bool):
        self.polar_device = PolarDevice(self.ble_device)

        try:
            await self.polar_device.connect()
            self.post_log("PolarDevice connected successfully.")

            # Create LSL outlets before streaming so no samples are lost.
            self._setup_lsl_outlets(enable_ecg=enable_ecg, enable_acc=enable_acc)

            # Keep the exact order that is already proven to work on the user's PC.
            self.post_log("Starting HR stream...")
            await self.polar_device.start_hr_stream(hr_callback=self._hr_callback)
            await asyncio.sleep(1.0)

            ecg_started = False
            acc_started = False

            if enable_ecg:
                try:
                    self.post_log("Starting ECG stream: 130 Hz, 14 bit...")
                    await self.polar_device.start_ecg_stream(
                        self._ecg_callback,
                        ECG_SAMPLE_RATE,
                        ECG_RESOLUTION,
                    )
                    ecg_started = True
                    await asyncio.sleep(1.0)
                except Exception as exc:
                    self.post_log(
                        f"ECG stream could not start: {type(exc).__name__}: {exc}"
                    )

            if enable_acc:
                try:
                    self.post_log("Starting ACC stream: 25 Hz, 16 bit, ±2 G...")
                    await self.polar_device.start_acc_stream(
                        self._acc_callback,
                        ACC_SAMPLE_RATE,
                        ACC_RESOLUTION,
                        ACC_RANGE_G,
                    )
                    acc_started = True
                    await asyncio.sleep(1.0)
                except Exception as exc:
                    self.post_log(
                        f"ACC stream could not start: {type(exc).__name__}: {exc}"
                    )

            return {
                "ecg": ecg_started,
                "acc": acc_started,
                "name": getattr(self.ble_device, "name", "Polar H10"),
                "address": getattr(self.ble_device, "address", ""),
            }

        except Exception:
            # Avoid leaving a half-open connection after a failed start.
            try:
                if self.polar_device is not None:
                    await self.polar_device.disconnect()
            except Exception:
                pass
            self.polar_device = None
            self._cleanup_lsl_outlets()
            raise

    def _handle_connect_done(self, future) -> None:
        self.connect_button.config(state=tk.NORMAL)

        try:
            result = future.result()
        except Exception as exc:
            self.connected = False
            self.ecg_active = False
            self.acc_active = False
            self.scan_button.config(state=tk.NORMAL)
            self.connect_button.config(text="CONNECT", bg=ACCENT_COLOR)
            self.status_var.set("Status: Connection failed")
            self.log(f"Connection failed: {type(exc).__name__}: {exc!r}")
            messagebox.showerror(
                "Connection Error",
                f"Could not connect/start the Polar H10 streams:\n\n"
                f"{type(exc).__name__}: {exc}",
            )
            return

        self.connected = True
        self.ecg_active = bool(result["ecg"])
        self.acc_active = bool(result["acc"])

        streams = ["HR/RR"]
        if self.ecg_active:
            streams.append("ECG")
        if self.acc_active:
            streams.append("ACC")

        self.connect_button.config(
            text="DISCONNECT",
            bg=ERROR_COLOR,
            command=self.disconnect_device,
        )
        self.scan_button.config(state=tk.DISABLED)
        self.record_button.config(state=tk.NORMAL)
        self.mark_button.config(state=tk.NORMAL)
        self.start_interval_button.config(state=tk.NORMAL)
        self.end_interval_button.config(state=tk.NORMAL)
        self.status_var.set(f"Status: Connected | {', '.join(streams)}")
        self.log(f"Connected. Active streams: {', '.join(streams)}")
        self.log("LSL outlets are live.")

    def disconnect_device(self) -> None:
        if not self.connected and self.polar_device is None:
            return

        if self.recording:
            self.stop_recording()

        if self.current_interval_start is not None:
            self.end_interval()

        self.connect_button.config(text="DISCONNECTING...", state=tk.DISABLED, bg=WARNING_COLOR)
        self.status_var.set("Status: Disconnecting...")
        self.log("Disconnecting from Polar H10...")

        future = self.ble.submit(self._disconnect_async())
        future.add_done_callback(lambda f: self.ui_queue.put(("disconnect_done", f)))

    async def _disconnect_async(self) -> None:
        if self.polar_device is not None:
            try:
                await self.polar_device.disconnect()
            finally:
                self.polar_device = None

    def _handle_disconnect_done(self, future) -> None:
        try:
            future.result()
            self.log("Polar H10 disconnected.")
        except Exception as exc:
            self.log(f"Disconnect warning: {type(exc).__name__}: {exc}")

        self.connected = False
        self.ecg_active = False
        self.acc_active = False
        self._cleanup_lsl_outlets()

        self.connect_button.config(
            text="CONNECT",
            state=tk.NORMAL,
            bg=ACCENT_COLOR,
            command=self.connect_device,
        )
        self.scan_button.config(state=tk.NORMAL)
        self.record_button.config(state=tk.DISABLED, text="START RECORDING", bg=ACCENT_COLOR)
        self.mark_button.config(state=tk.DISABLED)
        self.start_interval_button.config(state=tk.DISABLED)
        self.end_interval_button.config(state=tk.DISABLED)
        self.status_var.set("Status: Disconnected")

    # ------------------------------------------------------------------
    # LSL
    # ------------------------------------------------------------------

    @staticmethod
    def _add_channel_metadata(info: StreamInfo, labels: list[str], units: list[str]) -> None:
        channels = info.desc().append_child("channels")
        for label, unit in zip(labels, units):
            channel = channels.append_child("channel")
            channel.append_child_value("label", label)
            channel.append_child_value("unit", unit)

    def _setup_lsl_outlets(self, enable_ecg: bool, enable_acc: bool) -> None:
        address = getattr(self.ble_device, "address", "unknown")
        safe_address = "".join(c if c.isalnum() else "_" for c in address)

        # HR and RR retain the names used by the original GitHub application.
        hr_info = StreamInfo(
            "HeartRate",
            "ExciteOMeter",
            1,
            0.0,  # HR notifications are not guaranteed to be uniformly sampled.
            "float32",
            f"PolarH10_HR_{safe_address}",
        )
        self._add_channel_metadata(hr_info, ["HeartRate"], ["bpm"])
        self.hr_outlet = StreamOutlet(hr_info)

        rr_info = StreamInfo(
            "RRinterval",
            "ExciteOMeter",
            1,
            0.0,
            "float32",
            f"PolarH10_RR_{safe_address}",
        )
        self._add_channel_metadata(rr_info, ["RRInterval"], ["ms"])
        self.rr_outlet = StreamOutlet(rr_info)

        marker_info = StreamInfo(
            "Polar_Markers",
            "Markers",
            1,
            0.0,
            "string",
            f"PolarH10_Markers_{safe_address}",
        )
        self.marker_outlet = StreamOutlet(marker_info)

        if enable_ecg:
            ecg_info = StreamInfo(
                "Polar_ECG",
                "ECG",
                1,
                float(ECG_SAMPLE_RATE),
                "float32",
                f"PolarH10_ECG_{safe_address}",
            )
            self._add_channel_metadata(ecg_info, ["ECG"], ["raw"])
            self.ecg_outlet = StreamOutlet(ecg_info)

        if enable_acc:
            acc_info = StreamInfo(
                "Polar_ACC",
                "ACC",
                3,
                float(ACC_SAMPLE_RATE),
                "float32",
                f"PolarH10_ACC_{safe_address}",
            )
            self._add_channel_metadata(
                acc_info,
                ["ACC_X", "ACC_Y", "ACC_Z"],
                ["raw", "raw", "raw"],
            )
            self.acc_outlet = StreamOutlet(acc_info)

    def _cleanup_lsl_outlets(self) -> None:
        self.hr_outlet = None
        self.rr_outlet = None
        self.ecg_outlet = None
        self.acc_outlet = None
        self.marker_outlet = None

    def _push_marker(self, label: str, timestamp: Optional[float] = None) -> None:
        ts = local_clock() if timestamp is None else float(timestamp)
        if self.marker_outlet is not None:
            try:
                self.marker_outlet.push_sample([label], timestamp=ts)
            except Exception as exc:
                self.post_log(f"LSL marker push failed: {exc}")

    # ------------------------------------------------------------------
    # Polar callbacks
    # ------------------------------------------------------------------

    def _hr_callback(self, data: HRData) -> None:
        """Called by polar-python on the BLE event-loop thread."""
        try:
            now = local_clock()
            hr = float(getattr(data, "heartrate"))
            rr_values = list(getattr(data, "rr_intervals", []) or [])

            with self.data_lock:
                self.last_hr = hr
                self.hr_buffer.append((now, hr))

            if self.hr_outlet is not None:
                self.hr_outlet.push_sample([hr], timestamp=now)

            # If several RR intervals arrive in one HR packet, reconstruct a
            # reasonable sequence of interval-end timestamps ending at 'now'.
            rr_timestamps: list[float] = []
            if rr_values:
                remaining_after = 0.0
                reverse_times = []
                for rr in reversed(rr_values):
                    reverse_times.append(now - remaining_after)
                    remaining_after += float(rr) / 1000.0
                rr_timestamps = list(reversed(reverse_times))

            for rr, rr_ts in zip(rr_values, rr_timestamps):
                rr_ms = float(rr)
                with self.data_lock:
                    self.last_rr = rr_ms
                    self.rr_buffer.append((rr_ts, rr_ms))

                if self.rr_outlet is not None:
                    self.rr_outlet.push_sample([rr_ms], timestamp=rr_ts)

            if self.recording:
                with self.file_lock:
                    writer = self._writers.get("hr")
                    handle = self._files.get("hr")
                    if writer is not None:
                        writer.writerow([now, hr])
                        if handle is not None:
                            handle.flush()

                    rr_writer = self._writers.get("rr")
                    rr_handle = self._files.get("rr")
                    if rr_writer is not None:
                        for rr, rr_ts in zip(rr_values, rr_timestamps):
                            rr_writer.writerow([rr_ts, float(rr)])
                        if rr_handle is not None and rr_values:
                            rr_handle.flush()

        except Exception as exc:
            self.post_log(f"HR callback error: {type(exc).__name__}: {exc}")

    def _ecg_callback(self, data: ECGData) -> None:
        try:
            samples = list(getattr(data, "data", []) or [])
            if not samples:
                return

            device_timestamp = getattr(data, "timestamp", "")
            packet_end = local_clock()
            first_ts = packet_end - (len(samples) - 1) / ECG_SAMPLE_RATE

            rows = []
            with self.data_lock:
                for i, value in enumerate(samples):
                    ts = first_ts + i / ECG_SAMPLE_RATE
                    value_f = float(value)
                    self.ecg_buffer.append((ts, value_f))
                    rows.append((ts, device_timestamp, value_f))

            if self.ecg_outlet is not None:
                for ts, _device_ts, value_f in rows:
                    self.ecg_outlet.push_sample([value_f], timestamp=ts)

            if self.recording:
                with self.file_lock:
                    writer = self._writers.get("ecg")
                    handle = self._files.get("ecg")
                    if writer is not None:
                        writer.writerows(rows)
                        if handle is not None:
                            handle.flush()

        except Exception as exc:
            self.post_log(f"ECG callback error: {type(exc).__name__}: {exc}")

    def _acc_callback(self, data: ACCData) -> None:
        try:
            samples = list(getattr(data, "data", []) or [])
            if not samples:
                return

            device_timestamp = getattr(data, "timestamp", "")
            packet_end = local_clock()
            first_ts = packet_end - (len(samples) - 1) / ACC_SAMPLE_RATE

            rows = []
            lsl_rows = []

            with self.data_lock:
                for i, sample in enumerate(samples):
                    if len(sample) < 3:
                        continue
                    ts = first_ts + i / ACC_SAMPLE_RATE
                    x, y, z = float(sample[0]), float(sample[1]), float(sample[2])
                    self.acc_buffer.append((ts, x, y, z))
                    rows.append((ts, device_timestamp, x, y, z))
                    lsl_rows.append((ts, x, y, z))

            if self.acc_outlet is not None:
                for ts, x, y, z in lsl_rows:
                    self.acc_outlet.push_sample([x, y, z], timestamp=ts)

            if self.recording:
                with self.file_lock:
                    writer = self._writers.get("acc")
                    handle = self._files.get("acc")
                    if writer is not None:
                        writer.writerows(rows)
                        if handle is not None:
                            handle.flush()

        except Exception as exc:
            self.post_log(f"ACC callback error: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def toggle_recording(self) -> None:
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        if not self.connected:
            messagebox.showwarning("Recording", "Connect to the Polar H10 first.")
            return
        if not self.current_participant_id:
            messagebox.showwarning("Recording", "Set a participant ID first.")
            return
        if self.recording:
            return

        selected_mic = None
        if self.mic_var.get():
            selected_mic = self.get_selected_mic_index()
            if selected_mic is None:
                messagebox.showwarning(
                    "Microphone",
                    "Microphone recording is enabled, but no input device is selected.\n\n"
                    "Choose a microphone or disable microphone recording."
                )
                return
            try:
                self.microphone.validate_device(selected_mic)
            except Exception as exc:
                messagebox.showerror(
                    "Microphone unavailable",
                    "The selected microphone is unavailable or does not support "
                    f"mono {MIC_SAMPLE_RATE} Hz recording.\n\n{type(exc).__name__}: {exc}\n\n"
                    "Press REFRESH and select another input device."
                )
                return

        session_name = datetime.now().strftime("Session_%Y%m%d_%H%M%S")
        self.session_folder = os.path.join(
            str(DATA_DIR),
            f"Participant_{self.current_participant_id}",
            session_name,
        )
        os.makedirs(self.session_folder, exist_ok=True)

        try:
            self._open_recording_files()
        except Exception as exc:
            self._close_recording_files()
            messagebox.showerror("Recording", f"Could not create recording files:\n\n{exc}")
            return

        self.recording_start_time = local_clock()
        self.recording_stop_time = None
        self.recording = True

        if self.mic_var.get():
            try:
                self.microphone.start(self.session_folder, device=selected_mic)
                self.mic_status_var.set(f"MIC: recording input {selected_mic} / LSL live")
            except Exception as exc:
                self.recording = False
                self.microphone.stop()
                self._close_recording_files()
                self.mic_status_var.set("MIC: start failed")
                self._update_mic_controls()
                messagebox.showerror(
                    "Microphone",
                    f"Could not start the laptop microphone:\n\n{type(exc).__name__}: {exc}",
                )
                return

        # Marker is generated on the same pylsl local_clock timebase as every stream.
        self._push_marker("RECORDING_START", self.recording_start_time)

        self.mic_device_combo.config(state=tk.DISABLED)
        self.refresh_mic_button.config(state=tk.DISABLED)
        self.record_button.config(text="STOP RECORDING", bg=ERROR_COLOR)
        self.recording_label.config(
            text=f"Recording → {self.session_folder}",
            fg=SUCCESS_COLOR,
        )
        self.log(f"Recording started: {self.session_folder}")

    def stop_recording(self) -> None:
        if not self.recording:
            return

        if self.current_interval_start is not None:
            self.end_interval()

        self.recording_stop_time = local_clock()
        self._push_marker("RECORDING_STOP", self.recording_stop_time)
        self.recording = False
        self.microphone.stop()
        self.mic_status_var.set("MIC: ready")
        self._close_recording_files()
        self._update_mic_controls()

        self.record_button.config(text="START RECORDING", bg=ACCENT_COLOR)
        self.recording_label.config(text="Not recording", fg=SECONDARY_TEXT)
        self.log("Recording stopped and CSV files closed.")

    def _open_recording_files(self) -> None:
        if not self.session_folder:
            raise RuntimeError("No session folder")

        with self.file_lock:
            self._close_recording_files_locked()

            def open_writer(key: str, filename: str, header: list[str]) -> None:
                path = os.path.join(self.session_folder, filename)
                handle = open(path, "w", newline="", encoding="utf-8")
                writer = csv.writer(handle)
                writer.writerow(header)
                handle.flush()
                self._files[key] = handle
                self._writers[key] = writer

            open_writer("hr", "HeartRate_recording.csv", ["LSL_Timestamp", "HeartRate_bpm"])
            open_writer("rr", "RRinterval_recording.csv", ["LSL_Timestamp", "RR_ms"])
            open_writer("markers", "marked_timestamps.csv", ["LSL_Timestamp", "Marker"])
            open_writer(
                "intervals",
                "intervals.csv",
                ["Start_LSL_Timestamp", "End_LSL_Timestamp", "Duration_s"],
            )

            if self.ecg_active:
                open_writer(
                    "ecg",
                    "ECG_recording.csv",
                    ["LSL_Timestamp", "Polar_Device_Timestamp", "ECG_Value"],
                )

            if self.acc_active:
                open_writer(
                    "acc",
                    "ACC_recording.csv",
                    ["LSL_Timestamp", "Polar_Device_Timestamp", "X", "Y", "Z"],
                )

    def _close_recording_files_locked(self) -> None:
        for handle in list(self._files.values()):
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass
        self._files.clear()
        self._writers.clear()

    def _close_recording_files(self) -> None:
        with self.file_lock:
            self._close_recording_files_locked()

    # ------------------------------------------------------------------
    # Markers / intervals
    # ------------------------------------------------------------------

    def mark_timestamp(self) -> None:
        if not self.connected:
            return

        ts = local_clock()
        self.marked_timestamps.append(ts)
        self._push_marker("MARK", ts)

        if self.recording:
            with self.file_lock:
                writer = self._writers.get("markers")
                handle = self._files.get("markers")
                if writer is not None:
                    writer.writerow([ts, "MARK"])
                    if handle is not None:
                        handle.flush()

        self.log(f"Timestamp marked: {ts:.6f}")

    def start_interval(self) -> None:
        if not self.connected:
            return
        if self.current_interval_start is not None:
            messagebox.showinfo("Interval", "An interval is already active.")
            return

        ts = local_clock()
        self.current_interval_start = ts
        self._push_marker("INTERVAL_START", ts)
        self.start_interval_button.config(text="INTERVAL ACTIVE", bg=WARNING_COLOR)
        self.log(f"Interval started: {ts:.6f}")

    def end_interval(self) -> None:
        if self.current_interval_start is None:
            return

        start = self.current_interval_start
        end = local_clock()
        duration = end - start
        self.current_interval_start = None
        self.intervals.append((start, end))
        self._push_marker("INTERVAL_END", end)

        if self.recording:
            with self.file_lock:
                writer = self._writers.get("intervals")
                handle = self._files.get("intervals")
                if writer is not None:
                    writer.writerow([start, end, duration])
                    if handle is not None:
                        handle.flush()

        self.start_interval_button.config(text="START INTERVAL", bg=ACCENT_COLOR)
        self.log(f"Interval ended: {duration:.3f} s")

    # ------------------------------------------------------------------
    # Plotting
    # ------------------------------------------------------------------

    def _update_plots(self) -> None:
        if self.closing:
            return

        now = local_clock()
        cutoff = now - PLOT_WINDOW_SECONDS

        with self.data_lock:
            hr = [(t, v) for t, v in self.hr_buffer if t >= cutoff]
            rr = [(t, v) for t, v in self.rr_buffer if t >= cutoff]
            ecg = [(t, v) for t, v in self.ecg_buffer if t >= cutoff]
            acc = [(t, x, y, z) for t, x, y, z in self.acc_buffer if t >= cutoff]
            last_hr = self.last_hr
            last_rr = self.last_rr
            total_ecg = len(self.ecg_buffer)
            total_acc = len(self.acc_buffer)

        self.hr_value_var.set("HR: -- bpm" if last_hr is None else f"HR: {last_hr:.0f} bpm")
        self.rr_value_var.set("RR: -- ms" if last_rr is None else f"RR: {last_rr:.1f} ms")
        self.ecg_count_var.set(f"ECG buffer: {total_ecg} samples")
        self.acc_count_var.set(f"ACC buffer: {total_acc} samples")

        mic_audio, mic_dbfs = self.microphone.get_visualization_data()
        if self.microphone.active:
            self.mic_status_var.set(f"MIC: recording | Level: {mic_dbfs:.1f} dBFS")
        elif self.mic_var.get():
            self.mic_status_var.set("MIC: ready | Level: -- dBFS")
        else:
            self.mic_status_var.set("MIC: disabled")

        # HR/RR
        self.ax_hr.clear()
        self.ax_rr.clear()
        self._style_axis(self.ax_hr)
        self.ax_rr.set_facecolor("none")
        self.ax_rr.tick_params(colors=SECONDARY_TEXT)
        self.ax_rr.spines["right"].set_color(BORDER_COLOR)

        self.ax_hr.set_title("Heart rate and RR interval", color=TEXT_COLOR)
        self.ax_hr.set_ylabel("HR (bpm)", color=TEXT_COLOR)
        self.ax_rr.set_ylabel("RR (ms)", color=TEXT_COLOR)

        if hr:
            x = [t - now for t, _ in hr]
            y = [v for _, v in hr]
            self.ax_hr.plot(x, y, linewidth=1.7, label="HR")

        if rr:
            x_rr = [t - now for t, _ in rr]
            y_rr = [v for _, v in rr]
            self.ax_rr.scatter(x_rr, y_rr, s=14, alpha=0.7, label="RR")

        self.ax_hr.set_xlim(-PLOT_WINDOW_SECONDS, 0)
        self.ax_rr.set_xlim(-PLOT_WINDOW_SECONDS, 0)
        #self.ax_hr.set_xlabel("Seconds relative to now", color=TEXT_COLOR)

        # ECG
        self.ax_ecg.clear()
        self._style_axis(self.ax_ecg)
        self.ax_ecg.set_title("ECG", color=TEXT_COLOR)
        self.ax_ecg.set_ylabel("Raw", color=TEXT_COLOR)
        if ecg:
            x = [t - now for t, _ in ecg]
            y = [v for _, v in ecg]
            self.ax_ecg.plot(x, y, linewidth=0.8)
        self.ax_ecg.set_xlim(-PLOT_WINDOW_SECONDS, 0)
        #self.ax_ecg.set_xlabel("Seconds relative to now", color=TEXT_COLOR)

        # ACC magnitude
        self.ax_acc.clear()
        self._style_axis(self.ax_acc)
        self.ax_acc.set_title("Accelerometer magnitude", color=TEXT_COLOR)
        self.ax_acc.set_ylabel("Raw magnitude", color=TEXT_COLOR)
        if acc:
            x = [t - now for t, *_ in acc]
            mag = [math.sqrt(xv * xv + yv * yv + zv * zv) for _, xv, yv, zv in acc]
            self.ax_acc.plot(x, mag, linewidth=1.0)
        self.ax_acc.set_xlim(-PLOT_WINDOW_SECONDS, 0)
        #self.ax_acc.set_xlabel("Seconds relative to now", color=TEXT_COLOR)

        # Microphone waveform: display only the latest few seconds. The full audio
        # recording, timestamp CSV, and LSL stream continue independently until STOP.
        self.ax_mic.clear()
        self._style_axis(self.ax_mic)
        self.ax_mic.set_title("Microphone waveform (live display only)", color=TEXT_COLOR)
        self.ax_mic.set_ylabel("Amplitude", color=TEXT_COLOR)
        self.ax_mic.set_xlabel("Seconds relative to now", color=TEXT_COLOR)
        self.ax_mic.set_ylim(-1.0, 1.0)
        self.ax_mic.set_xlim(-MIC_VISUAL_WINDOW_SECONDS, 0)
        if mic_audio.size:
            step = max(1, int(math.ceil(mic_audio.size / MIC_VISUAL_MAX_POINTS)))
            display_audio = mic_audio[::step]
            duration = mic_audio.size / float(MIC_SAMPLE_RATE)
            x_mic = np.linspace(-duration, 0.0, display_audio.size)
            self.ax_mic.plot(x_mic, display_audio, linewidth=0.7)
        self.ax_mic.text(
            0.99, 0.90,
            f"{mic_dbfs:.1f} dBFS" if self.microphone.active else "-- dBFS",
            transform=self.ax_mic.transAxes, ha="right", va="top", color=SECONDARY_TEXT,
        )

        try:
            self.canvas.draw_idle()
        except Exception:
            pass

        self.root.after(PLOT_UPDATE_MS, self._update_plots)

    def _style_axis(self, ax) -> None:
        ax.set_facecolor(DARK_BG)
        ax.grid(True, alpha=0.2)
        for spine in ax.spines.values():
            spine.set_color(BORDER_COLOR)
        ax.tick_params(colors=SECONDARY_TEXT)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def on_closing(self) -> None:
        if self.closing:
            return
        self.closing = True

        try:
            if self.recording:
                self.stop_recording()

            if self.polar_device is not None:
                future = self.ble.submit(self._disconnect_async())
                try:
                    future.result(timeout=6.0)
                except Exception as exc:
                    print(f"Shutdown disconnect warning: {exc}")
        finally:
            self.microphone.stop()
            self._close_recording_files()
            self._cleanup_lsl_outlets()
            self.ble.stop()
            try:
                self.root.destroy()
            except Exception:
                pass
