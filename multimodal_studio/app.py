"""Interactive, independent-device experiment recorder (Tkinter, Python 3.10+)."""
import queue
import json
import tempfile
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime
import numpy as np
import sounddevice as sd
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from core import EventBus, SessionManager
from devices.audio import AudioRecorder, available_inputs
from devices.windows_audio import device_manager_sound_names, safe_hardware_match
from devices.polar import PolarRecorder
from devices.seetrue import NetworkLSLRecorder
from devices.camera_recorder import CameraRecorder
from devices.camera_preview import CameraPreview, discover_cameras
from devices.camera_controls import normalize_settings

BASE = Path(__file__).resolve().parent
BG, PANEL, CARD, WHITE = '#101827', '#172337', '#20304a', '#ecf3ff'
MUTED, BLUE, GREEN, YELLOW, RED = '#aabbd2', '#55a6ff', '#51d4a2', '#ffc66d', '#ff7585'
DEVICES = ['Microphone', 'Polar H10', 'Camera 1', 'Camera 2', 'Camera 3']
ICONS = {'Microphone':'MIC', 'Polar H10':'HEART', 'Camera 1':'CAM 1', 'Camera 2':'CAM 2', 'Camera 3':'CAM 3'}
DESCRIPTIONS = {'Microphone':'Choose any Windows audio input', 'Polar H10':'Heart rate, ECG, motion', 'Camera 1':'Camera preview + MP4 recording', 'Camera 2':'Camera preview + MP4 recording', 'Camera 3':'Camera preview + MP4 recording'}

class App:
    def __init__(self, root):
        self.root = root
        self.root.title('Intelligent Multimodal Microsurgery Studio (IMMSS) | Acquisition & Analysis')
        self.root.geometry('1280x850')
        self.root.minsize(1030, 720)
        self.root.configure(bg=BG)
        self.bus = EventBus()
        self.bus.install_exception_hooks()
        self.bus.emit("Application", "INFO", f"Intelligent Multimodal Microsurgery Studio (IMMSS) started; persistent log: {self.bus.application_log}")
        self.session = SessionManager(self.bus)
        self.recorders = {'Microphone':AudioRecorder('Microphone',self.bus),
                          'Polar H10':PolarRecorder(self.bus),
                          **{f'Camera {i}':CameraRecorder(i,self.bus) for i in range(1,4)}}
        self.enabled = {n:tk.BooleanVar(value=False) for n in DEVICES}
        self.status = {n:tk.StringVar(value='Not selected') for n in DEVICES}
        self.folder = tk.StringVar(value=str((BASE.parent/'Participant_Data').resolve()))
        self.participant = tk.StringVar(value='001')
        self.marker_label = tk.StringVar(value='EVENT')
        self.session_status = tk.StringVar(value='Ready to configure')
        self.time_label = tk.StringVar(value='00:00:00')
        self.count_label = tk.StringVar(value='0 selected')
        self.mic_rate = tk.StringVar(value='48000')
        self.mic_friendly_name = tk.StringVar(value='')
        self.alias_path = BASE / 'microphone_aliases.json'
        self.mic_aliases = self._load_mic_aliases()
        self.mic_details = tk.StringVar(value='Select an input to view device capabilities')
        self.windows_hardware_info = tk.StringVar(value='Windows sound hardware: not scanned')
        self.ecg, self.acc = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
        self.polar_detail = tk.StringVar(value='No Polar H10 selected')
        self.device_choices, self.ble_device = [], None
        self.windows_sound_names = []
        self.audio_name_lookup = {}
        self.network_choices = []
        self.network_selections = {}
        self.network_channel_vars = []
        self.network_current_uid = None
        self.selected_device = 'Microphone'
        self.started_at = None
        self._start_results = queue.Queue()
        self._starting_session = False
        self.preview_active = False
        self.preview_tmp = None
        self.preview_devices = {}
        self.preview_started = set()
        self.buttons = {}
        self.device_enable_controls = {}
        self.monitor_panels = {}
        self.camera_previews = {i: CameraPreview(i, self.bus) for i in range(1, 4)}
        self.camera_choices = []
        self.camera_selects = {}
        self.camera_images = {}
        self.camera_labels = {}
        self.camera_status = {}
        self.camera_buttons = {}
        self.camera_setting_vars = {}
        self.camera_setting_widgets = {}
        self._camera_scanning = False
        self._style()
        self._build()
        self.refresh_audio()
        self.tabs.select(self.dashboard_tab)
        self.root.after(350, self.poll)
        self.root.protocol('WM_DELETE_WINDOW',self.close)

    def show_about(self):
        """Display transparent scope and purpose without claiming unvalidated AI."""
        messagebox.showinfo(
            'About Intelligent Multimodal Microsurgery Studio',
            'Intelligent Multimodal Microsurgery Studio (IMMSS)\n'
            'Microsurgical Multimodal Research Platform\n\n'
            'Acquisition · Live Monitoring · Temporal Alignment · Quality Assessment\n\n'
            'Modular support for audio, video, Polar H10 and external network LSL streams.\n'
            'Post-experiment synchronization-quality analysis is available in its own tab.\n\n'
            'Physical synchronization accuracy and AI-based surgical assessment require\n'
            'separate experimental validation.\n\n'
            'Existing recording filenames and session structures are preserved.'
        )

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TCombobox', fieldbackground=CARD, background=CARD, foreground=WHITE,
                        arrowcolor=WHITE, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD)
        style.map('TCombobox', fieldbackground=[('readonly',CARD)], foreground=[('readonly',WHITE)])
        style.configure('TProgressbar', troughcolor=CARD, background=GREEN, borderwidth=0)
        style.configure('TScrollbar', background=CARD, troughcolor=PANEL)

    def label(self, parent, text, size=10, color=WHITE, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent['bg'], fg=color,
                        font=('Segoe UI',size,'bold' if bold else 'normal'), **kw)

    def action(self, parent, text, command, accent=False, danger=False, **kw):
        base = RED if danger else (BLUE if accent else CARD)
        fg = '#07111f' if (accent or danger) else WHITE
        return tk.Button(parent, text=text, command=command, bg=base, fg=fg, relief='flat',
                         activebackground='#84bcff', activeforeground='#07111f', cursor='hand2',
                         font=('Segoe UI',10,'bold'), padx=17, pady=9, bd=0, **kw)

    def field(self,parent,var,width=20):
        return tk.Entry(parent,textvariable=var,width=width,relief='flat',bg=CARD,fg=WHITE,
                        insertbackground=WHITE,highlightthickness=1,highlightbackground='#3b4c68',
                        highlightcolor=BLUE,font=('Segoe UI',11))

    def _build(self):
        head = tk.Frame(self.root,bg=BG,padx=24,pady=13); head.pack(fill='x')
        branding = tk.Frame(head, bg=BG); branding.pack(side='left')
        title_row = tk.Frame(branding, bg=BG); title_row.pack(anchor='w')
        self.label(title_row,'Intelligent Multimodal ',19,WHITE,True).pack(side='left')
        self.label(title_row,'Microsurgery Studio',19,BLUE,True).pack(side='left')
        self.label(title_row,'  IMMSS',10,MUTED,True).pack(side='left',pady=(8,0))
        self.label(branding,'Multimodal Acquisition  •  Temporal Alignment  •  Synchronization Quality Assessment for Microsurgery',9,MUTED).pack(anchor='w',pady=(3,0))
        self.status_top=self.label(head,'●  IDLE',11,GREEN,True)
        self.status_top.pack(side='right',padx=(12,0))
        self.action(head,'About IMMSS',self.show_about).pack(side='right',padx=(0,10))
        line=tk.Frame(self.root,bg='#31445f',height=1); line.pack(fill='x')
        setup=tk.Frame(self.root,bg=BG,padx=24,pady=14); setup.pack(fill='x')
        self.label(setup,'Participant',10,MUTED).grid(row=0,column=0,sticky='w')
        self.field(setup,self.participant,15).grid(row=1,column=0,sticky='ew',padx=(0,16),ipady=9)
        self.label(setup,'Data location',10,MUTED).grid(row=0,column=1,sticky='w')
        self.field(setup,self.folder,35).grid(row=1,column=1,sticky='ew',padx=(0,8),ipady=9)
        self.action(setup,'Browse…',self.browse).grid(row=1,column=2,padx=(0,14))
        setup.columnconfigure(1,weight=1)
        self.preview_btn=self.action(setup,'▶  PREVIEW',self.preview,accent=False)
        self.preview_btn.grid(row=1,column=3,padx=(0,7))
        self.start_btn=self.action(setup,'●  START SESSION',self.start,accent=True)
        self.start_btn.grid(row=1,column=4,padx=(0,7))
        self.stop_btn=self.action(setup,'■  STOP',self.stop,danger=True,state='disabled')
        self.stop_btn.grid(row=1,column=5)
        body=tk.Frame(self.root,bg=BG,padx=24); body.pack(fill='both',expand=True)
        # A single full-width workspace. Device selection lives in each device tab;
        # the Dashboard provides readiness and global session controls only.
        right=tk.Frame(body,bg=BG)
        right.pack(fill='both',expand=True)
        overview=tk.Frame(right,bg=PANEL,padx=17,pady=13);overview.pack(fill='x',pady=(0,10))
        top=tk.Frame(overview,bg=PANEL);top.pack(fill='x')
        self.label(top,'SESSION STATUS',10,MUTED,True).pack(side='left')
        self.label(top,'Elapsed',10,MUTED).pack(side='right',padx=(12,0))
        self.label(top,'  •  ',10,MUTED).pack(side='right')
        self.label(top,'Selected',10,MUTED).pack(side='right')
        vals=tk.Frame(overview,bg=PANEL);vals.pack(fill='x',pady=(5,0))
        self.label(vals,'Ready when you are',12,WHITE,True,textvariable=self.session_status).pack(side='left')
        self.label(vals,'00:00:00',19,WHITE,True,textvariable=self.time_label).pack(side='right')
        self.label(vals,'0 selected',11,BLUE,True,textvariable=self.count_label).pack(side='right',padx=(0,30))
        self.tabs=ttk.Notebook(right);self.tabs.pack(fill='both',expand=True)
        # Dedicated configuration tabs; existing recorder classes and settings are reused.
        self.dashboard_tab=tk.Frame(self.tabs,bg=PANEL,padx=18,pady=14)
        self.polar_tab=tk.Frame(self.tabs,bg=PANEL,padx=22,pady=18)
        self.microphone_tab=tk.Frame(self.tabs,bg=PANEL,padx=22,pady=18)
        self.monitor_tab=tk.Frame(self.tabs,bg=PANEL,padx=12,pady=12)
        self.network_tab=tk.Frame(self.tabs,bg=PANEL,padx=18,pady=14)
        self.camera_tab=tk.Frame(self.tabs,bg=PANEL,padx=22,pady=22)
        self.activity_tab=tk.Frame(self.tabs,bg=PANEL,padx=16,pady=16)
        self.analysis_tab=tk.Frame(self.tabs,bg=PANEL,padx=8,pady=8)
        for name,frame in [('Experiment dashboard',self.dashboard_tab),
                           ('Polar H10',self.polar_tab),('Microphone',self.microphone_tab),
                           ('Cameras',self.camera_tab),('Network LSL',self.network_tab),
                           ('Live monitoring',self.monitor_tab),
                           ('Post-experiment analysis',self.analysis_tab),
                           ('Logs & diagnostics',self.activity_tab)]:
            self.tabs.add(frame,text='  '+name+'  ')
        self.settings={'Microphone': self.microphone_tab, 'Polar H10': self.polar_tab}
        self._build_dashboard()
        self._build_device_enable(self.microphone_tab,'Microphone')
        self._build_device_enable(self.polar_tab,'Polar H10')
        self._build_monitor()
        self._build_configs()
        self._build_network_lsl()
        self._build_cameras()
        self._build_activity()
        from post_analysis import PostExperimentTab
        self.post_analysis = PostExperimentTab(self.analysis_tab,self.root,self.session,self.bus)
        self.post_analysis.pack(fill='both',expand=True)
        foot=tk.Frame(self.root,bg=BG,padx=24,pady=10);foot.pack(fill='x')
        self.label(foot,'Event marker:',10,MUTED).pack(side='left')
        self.field(foot,self.marker_label,24).pack(side='left',ipady=8,padx=8)
        self.marker_btn=self.action(foot,'+ Add marker',self.mark,state='disabled')
        self.marker_btn.pack(side='left')
        self.label(foot,'Other sensors continue if a device fails. Check Activity for warnings.',9,MUTED).pack(side='right')
        self.toggle_controls=[]
        self._update_selection()

    def _build_configs(self):
        f=self.settings['Microphone']
        self.label(f,'Connected recording microphone',11,WHITE,True).pack(anchor='w')
        self.label(f,'Choose one Windows audio input. The same device is used for Preview and Record.',
                   10,MUTED,wraplength=650,justify='left').pack(anchor='w',pady=(5,9))
        self.mic_select=ttk.Combobox(f,state='readonly',width=80)
        self.mic_select.pack(fill='x',pady=(6,10))
        self.mic_select.bind('<<ComboboxSelected>>',lambda _event:self.on_audio_selected())
        self.label(f,'Friendly name for the selected input',10,WHITE,True).pack(anchor='w',pady=(3,3))
        alias_row=tk.Frame(f,bg=PANEL)
        alias_row.pack(fill='x',pady=(0,8))
        self.mic_alias_entry=self.field(alias_row,self.mic_friendly_name,30)
        self.mic_alias_entry.pack(side='left',fill='x',expand=True)
        self.mic_alias_save=self.action(alias_row,'Save name',self.save_mic_alias)
        self.mic_alias_save.pack(side='left',padx=(8,0))
        self.mic_alias_clear=self.action(alias_row,'Clear name',self.clear_mic_alias)
        self.mic_alias_clear.pack(side='left',padx=(8,0))
        self.label(f,'Use Preview to verify the input before naming it. Names are local labels, not verified USB hardware identities.',9,MUTED,wraplength=650,justify='left').pack(anchor='w',pady=(0,10))
        self.label(f,'',10,MUTED,textvariable=self.mic_details,wraplength=650,justify='left').pack(anchor='w',pady=(0,13))
        self.label(f,'Windows Device Manager hardware (identification only)',10,WHITE,True).pack(anchor='w',pady=(0,3))
        self.label(f,'',9,MUTED,textvariable=self.windows_hardware_info,wraplength=650,justify='left').pack(anchor='w',pady=(0,12))
        self.label(f,'Sampling rate',11,WHITE,True).pack(anchor='w')
        self.mic_rate_combo=ttk.Combobox(f,values=['44100','48000','96000','192000','384000'],
                                          textvariable=self.mic_rate,width=15,state='readonly')
        self.mic_rate_combo.pack(anchor='w',pady=(6,13))
        self.action(f,'↻ Refresh Windows audio inputs',self.refresh_audio).pack(anchor='w')
        self.action(f,'View Device Manager sound hardware names',self.show_windows_audio_names).pack(anchor='w',pady=(8,0))
        self.label(f,'Windows may expose the same microphone through several audio backends. '
                     'Device Manager lists sound hardware; Windows recording endpoints may have different names. '
                     'Hardware names are shown only when they can be matched without guessing. Select one input endpoint.',
                   9,YELLOW,wraplength=650,justify='left').pack(anchor='w',pady=(12,0))
        f=self.settings['Polar H10']
        self.label(f,'Bluetooth Polar H10',11,WHITE,True).pack(anchor='w')
        self.label(f,'Scan before starting the session. HR/RR are included by default.',10,MUTED).pack(anchor='w',pady=(3,12))
        self.action(f,'Scan for Polar H10',self.scan_polar,accent=True).pack(anchor='w',pady=(0,12))
        self.label(f,'Not scanned',10,MUTED,textvariable=self.polar_detail,wraplength=650).pack(anchor='w',pady=(0,15))
        tk.Checkbutton(f,text='Record ECG (~130 Hz)',variable=self.ecg,bg=PANEL,fg=WHITE,activebackground=PANEL,selectcolor=BG).pack(anchor='w')
        tk.Checkbutton(f,text='Record accelerometer (~25 Hz)',variable=self.acc,bg=PANEL,fg=WHITE,activebackground=PANEL,selectcolor=BG).pack(anchor='w')

    def _build_cameras(self):
        self.label(self.camera_tab,'CAMERA SETUP & LIVE PREVIEW',17,WHITE,True).pack(anchor='w')
        self.label(self.camera_tab,'Select devices and configure imaging in one place. Settings apply to both Test Preview and session recording.',10,YELLOW,wraplength=900).pack(anchor='w',pady=(4,10))
        self.action(self.camera_tab,'↻ Scan available cameras',self.scan_cameras,accent=True).pack(anchor='w',pady=(0,12))
        camera_canvas=tk.Canvas(self.camera_tab,bg=PANEL,highlightthickness=0)
        camera_scroll=ttk.Scrollbar(self.camera_tab,orient='vertical',command=camera_canvas.yview)
        camera_canvas.configure(yscrollcommand=camera_scroll.set)
        camera_scroll.pack(side='right',fill='y')
        camera_canvas.pack(side='left',fill='both',expand=True)
        grid=tk.Frame(camera_canvas,bg=PANEL)
        camera_window=camera_canvas.create_window((0,0),window=grid,anchor='nw')
        grid.bind('<Configure>',lambda e:camera_canvas.configure(scrollregion=camera_canvas.bbox('all')))
        camera_canvas.bind('<Configure>',lambda e:camera_canvas.itemconfigure(camera_window,width=e.width))
        for i in range(1,4):
            grid.columnconfigure(i-1,weight=1)
            frame=tk.Frame(grid,bg='#0b1423',highlightbackground='#36506d',highlightthickness=1)
            frame.grid(row=0,column=i-1,sticky='nsew',padx=5)
            self.label(frame,f'CAMERA {i}',11,WHITE,True).pack(anchor='w',padx=12,pady=(12,4))
            enable=tk.Checkbutton(frame,text='Include in experiment',
                                  variable=self.enabled[f'Camera {i}'],
                                  command=lambda slot=i:self.toggle(f'Camera {slot}'),
                                  bg='#0b1423',fg=WHITE,selectcolor=BG,
                                  activebackground='#0b1423',activeforeground=WHITE)
            enable.pack(anchor='w',padx=10)
            self.device_enable_controls[f'Camera {i}']=enable
            combo=ttk.Combobox(frame,state='readonly',width=26)
            combo.pack(fill='x',padx=10,pady=5)
            self.camera_selects[i]=combo
            label=self.label(frame,'No preview · select camera',10,MUTED,wraplength=260)
            label.pack(expand=True,fill='both',padx=8,pady=8)
            self.camera_labels[i]=label
            state=tk.StringVar(value='Not connected')
            self.camera_status[i]=state
            self.label(frame,'',9,YELLOW,textvariable=state,wraplength=265).pack(pady=(0,6))
            btn=self.action(frame,'▶ Test preview',lambda slot=i:self.toggle_camera_preview(slot))
            btn.pack(pady=(0,12))
            self.camera_buttons[i]=btn
            self._build_camera_properties(frame, i)
        grid.rowconfigure(0,weight=1)
        self.root.after(650, self._render_cameras)


    def _build_camera_properties(self, frame, slot):
        self.label(frame, 'CAMERA PROPERTIES', 10, WHITE, True).pack(anchor='w', padx=10, pady=(4, 3))
        vars_ = {
            'resolution': tk.StringVar(value='640x480'),
            'fps': tk.StringVar(value='25'),
            'autofocus': tk.BooleanVar(value=True),
            'focus': tk.StringVar(value='0'),
            'autoexposure': tk.BooleanVar(value=True),
            'exposure': tk.StringVar(value='-5'),
            'brightness': tk.StringVar(value='0'),
            'gain': tk.StringVar(value='0'),
        }
        self.camera_setting_vars[slot] = vars_
        widgets = []
        def line(label, var, choices=None):
            row = tk.Frame(frame, bg='#0b1423')
            row.pack(fill='x', padx=10, pady=2)
            self.label(row, label, 9, MUTED).pack(side='left')
            if choices:
                widget = ttk.Combobox(row, textvariable=var, values=choices, state='readonly', width=12)
            else:
                widget = ttk.Entry(row, textvariable=var, width=12)
            widget.pack(side='right')
            widgets.append(widget)
        line('Dimensions', vars_['resolution'], ['640x480','800x600','1280x720','1920x1080'])
        line('FPS', vars_['fps'], ['15','25','30','60'])
        for label, key in [('Autofocus','autofocus'), ('Auto exposure','autoexposure')]:
            widget = tk.Checkbutton(frame, text=label, variable=vars_[key], bg='#0b1423', fg=WHITE,
                                    activebackground='#0b1423', selectcolor=BG)
            widget.pack(anchor='w', padx=10)
            widgets.append(widget)
        for label,key in [('Manual focus','focus'),('Exposure','exposure'),('Brightness','brightness'),('Gain','gain')]:
            line(label, vars_[key])
        self.camera_setting_widgets[slot] = widgets
        self.action(frame,'Apply settings & restart preview',lambda i=slot:self.apply_camera_preview_settings(i)).pack(pady=(8,12))
        self.label(frame,'Controls depend on camera and Windows backend; readback and warnings appear in Activity.',8,MUTED,wraplength=250).pack(padx=10,pady=(0,8))

    def camera_settings(self, slot):
        v = self.camera_setting_vars[slot]
        try:
            w,h = (int(x) for x in v['resolution'].get().split('x'))
            return normalize_settings({
                'width': w, 'height': h, 'fps': float(v['fps'].get()),
                'autofocus': v['autofocus'].get(), 'focus': float(v['focus'].get()),
                'autoexposure': v['autoexposure'].get(), 'exposure': float(v['exposure'].get()),
                'brightness': float(v['brightness'].get()), 'gain': float(v['gain'].get()),
            })
        except (ValueError, TypeError) as exc:
            raise ValueError(f'Camera {slot}: invalid property value: {exc}') from exc

    def apply_camera_preview_settings(self, slot):
        if self.session.active or self.preview_active:
            self.bus.emit(f'Camera {slot}', 'WARNING', 'Stop experiment Preview/Record to adjust camera properties')
            return
        try:
            self.camera_settings(slot)
            p = self.camera_previews[slot]
            if p.thread and p.thread.is_alive():
                p.stop()
                if p.thread and p.thread.is_alive():
                    raise RuntimeError('Camera capture is still stopping')
                self.camera_buttons[slot].configure(text='▶ Test preview')
                self.toggle_camera_preview(slot)
            self.bus.emit(f'Camera {slot}', 'INFO', 'Camera properties applied to next preview/recording; verify actual values in log')
        except Exception as exc:
            self.bus.emit(f'Camera {slot}', 'ERROR', f'Cannot apply settings: {exc}')

    def scan_cameras(self):
        if self._camera_scanning or self.session.active or self.preview_active:
            self.bus.emit('Cameras','WARNING','Stop experiment Preview/Record before scanning cameras')
            return
        if any(p.thread and p.thread.is_alive() for p in self.camera_previews.values()):
            self.bus.emit('Cameras','WARNING','Stop all live camera previews before scanning')
            return
        self._camera_scanning=True
        self.bus.emit('Cameras','INFO','Scanning indices 0–9 using DirectShow, Media Foundation and default backend')
        def work():
            try:
                diagnostics=[]
                def record_diagnostic(msg):
                    diagnostics.append(msg)
                    self.bus.emit('Cameras','INFO',msg)
                result=discover_cameras(10, report=record_diagnostic)
                if not result:
                    self.bus.emit('Cameras','WARNING',
                                  'Camera discovery failed on all tested backends. '
                                  'Review the per-index INFO events above; test using camera_diagnostics.py. '
                                  f'{len(diagnostics)} detailed checks logged.')
            except Exception as exc:
                self.bus.emit('Cameras','ERROR',f'Camera scan failed: {exc!r}')
                result=[]
            self.bus.events.put(('CAMERA_SCAN','DONE',result))
        threading.Thread(target=work,daemon=True).start()

    def toggle_camera_preview(self, slot):
        if self.session.active or self.preview_active:
            self.bus.emit(f'Camera {slot}', 'WARNING', 'Stop session Preview/Record before changing camera test preview')
            return
        preview=self.camera_previews[slot]
        if preview.thread and preview.thread.is_alive():
            preview.stop()
            self.camera_buttons[slot].configure(text='▶ Test preview')
            self.camera_status[slot].set('Stopped')
            return
        combo=self.camera_selects[slot]
        choice=combo.current()
        if not 0<=choice<len(self.camera_choices):
            self.bus.emit(f'Camera {slot}','WARNING','Select a camera from the dropdown first')
            return
        index=self.camera_choices[choice][0]
        for other, p in self.camera_previews.items():
            if other != slot and p.thread and p.thread.is_alive() and p.device_index==index:
                self.bus.emit(f'Camera {slot}','WARNING',f'Camera {index} is already used by Camera {other}')
                return
        try:
            preview.start(index, settings=self.camera_settings(slot))
            self.camera_buttons[slot].configure(text='■ Stop preview')
            self.camera_status[slot].set('Connecting...')
        except Exception as exc:
            self.bus.emit(f'Camera {slot}','ERROR',f'Preview start failed: {exc!r}')

    def _render_cameras(self):
        try:
            from PIL import Image, ImageTk
            for slot, preview in self.camera_previews.items():
                recorder=self.recorders[f'Camera {slot}']
                source=recorder if (recorder.thread and recorder.thread.is_alive()) else preview
                frame, stamp, error = source.snapshot()
                if frame is not None and time.monotonic()-stamp<2 and source.thread and source.thread.is_alive():
                    h,w=frame.shape[:2]
                    image=Image.fromarray(frame)
                    image.thumbnail((300,245))
                    photo=ImageTk.PhotoImage(image)
                    self.camera_labels[slot].configure(image=photo,text='')
                    self.camera_images[slot]=photo
                    self.camera_status[slot].set(f'LIVE · {w} × {h}')
                elif error or not (source.thread and source.thread.is_alive()):
                    self.camera_labels[slot].configure(image='',text='No live video')
                    self.camera_images.pop(slot,None)
                    self.camera_buttons[slot].configure(text='▶ Test preview')
                    if error:self.camera_status[slot].set('ERROR · '+error[:65])
        except Exception as exc:
            self.bus.emit('Cameras','ERROR',f'Camera UI update: {exc!r}')
        self.root.after(150,self._render_cameras)

    def _build_monitor(self):
        header=tk.Frame(self.monitor_tab,bg=PANEL);header.pack(fill='x',pady=(0,10))
        self.label(header,'LIVE MONITORING',17,WHITE,True).pack(side='left')
        self.label(header,'5-second scrolling window · Preview or Record',9,MUTED).pack(side='right')
        self.monitor_canvas=tk.Canvas(self.monitor_tab,bg=PANEL,highlightthickness=0)
        scroll=ttk.Scrollbar(self.monitor_tab,orient='vertical',command=self.monitor_canvas.yview)
        self.monitor_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y')
        self.monitor_canvas.pack(side='left',fill='both',expand=True)
        self.monitor_inner=tk.Frame(self.monitor_canvas,bg=PANEL)
        self.monitor_win=self.monitor_canvas.create_window((0,0),window=self.monitor_inner,anchor='nw')
        self.monitor_inner.bind('<Configure>',lambda e:self.monitor_canvas.configure(scrollregion=self.monitor_canvas.bbox('all')))
        self.monitor_canvas.bind('<Configure>',lambda e:self.monitor_canvas.itemconfigure(self.monitor_win,width=e.width))
        self._rebuild_monitor()

    def _rebuild_monitor(self):
        for widget in self.monitor_inner.winfo_children():widget.destroy()
        self.monitor_panels.clear()
        choices=[name for name in DEVICES if self.enabled[name].get()]
        if not choices:
            self.label(self.monitor_inner,'Enable one or more devices to see their monitors here.',12,MUTED).pack(pady=65)
            return
        for name in choices:
            card=tk.Frame(self.monitor_inner,bg=CARD,padx=12,pady=10)
            card.pack(fill='x',padx=7,pady=7)
            top=tk.Frame(card,bg=CARD);top.pack(fill='x')
            self.label(top,name,12,WHITE,True).pack(side='left')
            state=self.label(top,'SELECTED · WAITING',9,YELLOW,True)
            state.pack(side='right')
            if name.startswith('Camera'):
                self.label(card,'CAMERA — Live video is displayed on the Cameras tab.',11,YELLOW).pack(pady=20)
                self.monitor_panels[name]=(state,None,None,None)
                continue
            fig=Figure(figsize=(7.3,1.75),dpi=95,facecolor=CARD)
            ax=fig.add_subplot(111)
            ax.set_facecolor('#0b1423')
            ax.tick_params(colors=MUTED,labelsize=8)
            for spine in ax.spines.values():spine.set_color('#50627d')
            label='Amplitude' if name in ('Microphone',) else ('BPM / ECG / ACC' if name=='Polar H10' else 'LSL channel value')
            ax.set_ylabel(label,color=MUTED,fontsize=8)
            fig.tight_layout(pad=1.5)
            canvas=FigureCanvasTkAgg(fig,master=card)
            canvas.get_tk_widget().pack(fill='x',expand=True)
            info=self.label(card,'Press PREVIEW to inspect signal before recording',9,MUTED)
            info.pack(anchor='w',pady=(5,0))
            self.monitor_panels[name]=(state,ax,canvas,info)

    def _draw_monitors(self):
        from pylsl import local_clock
        now = local_clock()
        for name,(state,ax,canvas,info) in self.monitor_panels.items():
            current=self.status[name].get()
            state.configure(text=current,fg=RED if current in ('ERROR','DROPPED') else GREEN if current in ('RECORDING','RECOVERED','PREVIEW') else YELLOW)
            if ax is None: continue
            rec=self.recorders[name]
            ax.clear()
            ax.set_facecolor('#0b1423')
            ax.tick_params(colors=MUTED,labelsize=8)
            ax.set_xlim(5,0)
            ax.set_xticks([5,4,3,2,1,0])
            ax.set_xlabel('Time ago (seconds) · newest at 0',color=MUTED,fontsize=8)
            for spine in ax.spines.values():spine.set_color('#50627d')
            if name in ('Microphone',):
                from monitoring import aged_points
                points = aged_points(rec.snapshot_preview(), now, seconds=5.0)
                if points:
                    xs, ys = zip(*points)
                    ax.plot(xs, ys, color=BLUE, lw=.75)
                ax.set_ylim(-1.0,1.0)
                ax.set_ylabel('Amplitude (−1 to +1)',color=MUTED,fontsize=8)
                peak=max((abs(y) for _,y in points),default=0.0)
                assessment=('NO RECENT SAMPLES — check acquisition' if not points else 'SILENCE — recorded samples near zero' if peak < 1/32768 else 'LOW LEVEL — check gain/input' if peak < 0.003 else 'Signal received')
                info.configure(text=f'{assessment}  |  Peak: {peak:.4f}  |  RMS: {rec.level:.4f}  |  Received: {rec.total_frames_received:,}  |  Saved: {rec.count:,}  |  Drops: {rec.lost_blocks}')
            elif name=='Polar H10':
                points=[(now-t,y) for t,y in list(rec.ecg_timed) if 0 <= now-t <= 5]
                if points:
                    xs,ys=zip(*points)
                    ax.plot(xs,ys,color=GREEN,lw=.85,label='ECG')
                else:
                    points=[(now-t,y) for t,y in list(rec.hr_timed) if 0 <= now-t <= 5]
                    if points:
                        xs,ys=zip(*points)
                        ax.plot(xs,ys,color=GREEN,lw=1.2,label='HR')
                if points:ax.legend(loc='upper right',fontsize=7)
                info.configure(text=f'HR: {rec.last_hr if rec.last_hr is not None else "—"} BPM · ECG points: {len(rec.ecg_preview)} · ACC points: {len(rec.acc_preview)}')
            canvas.draw_idle()

    def _build_activity(self):
        top=tk.Frame(self.activity_tab,bg=PANEL);top.pack(fill='x',pady=(0,10))
        self.label(top,'EVENT LOG',16,WHITE,True).pack(side='left')
        self.action(top,'Clear screen',lambda:self.log_text.delete('1.0','end')).pack(side='right')
        self.log_text=tk.Text(self.activity_tab,bg='#0b1423',fg=WHITE,insertbackground=WHITE,
                              font=('Consolas',10),bd=0,wrap='word',padx=12,pady=12,state='normal')
        self.log_text.pack(fill='both',expand=True)
        self._log('INFO  Interface initialized. Enable sensors in their dedicated tabs.')

    def _log(self,text):
        self.log_text.insert('end',f"{datetime.now():%H:%M:%S}   {text}\n")
        self.log_text.see('end')

    def select_device(self,name):
        """Clicking a sidebar device navigates directly to its own controls."""
        self.selected_device=name
        destination=(self.camera_tab if name.startswith('Camera ')
                     else self.microphone_tab if name=='Microphone'
                     else self.polar_tab)
        self.tabs.select(destination)
        if name.startswith('Camera '):
            slot=int(name.rsplit(' ',1)[1])
            self.camera_selects[slot].focus_set()

    def _build_device_enable(self,parent,name):
        strip=tk.Frame(parent,bg=CARD,padx=12,pady=10)
        # Called before device-specific controls are constructed. Pack directly;
        # querying winfo_children() here includes `strip` itself and using it
        # as the `before=` anchor raises TclError: ... isn't packed.
        strip.pack(fill='x')
        box=tk.Checkbutton(strip,text='Include '+name+' in experiment',
                           variable=self.enabled[name],command=lambda n=name:self.toggle(n),
                           bg=CARD,fg=WHITE,activebackground=CARD,activeforeground=WHITE,
                           selectcolor=BG,font=('Segoe UI',11,'bold'))
        box.pack(side='left')
        self.device_enable_controls[name]=box
        self.label(strip,'Settings apply to Preview and Record',9,MUTED).pack(side='right')

    def _build_dashboard(self):
        self.label(self.dashboard_tab,'EXPERIMENT READINESS',17,WHITE,True).pack(anchor='w')
        self.label(self.dashboard_tab,
                   'Use the device tabs to include and configure equipment, test its signals, then start a session. '
                   'Only enabled devices are acquired.',10,MUTED,wraplength=790,justify='left').pack(anchor='w',pady=(5,16))
        self.readiness_rows={}
        rows=tk.Frame(self.dashboard_tab,bg=PANEL)
        rows.pack(fill='x')
        for name in DEVICES:
            row=tk.Frame(rows,bg=CARD,padx=12,pady=9)
            row.pack(fill='x',pady=3)
            self.action(row,'Configure  →',lambda n=name:self.select_device(n)).pack(side='right')
            self.label(row,name,11,WHITE,True).pack(side='left',padx=(0,12))
            state=tk.StringVar(value='Not selected')
            self.label(row,'',10,MUTED,textvariable=state).pack(side='left')
            self.readiness_rows[name]=state
        self.label(self.dashboard_tab,'NETWORK LSL',11,WHITE,True).pack(anchor='w',pady=(16,4))
        self.network_dashboard_status=tk.StringVar(value='No external LSL streams selected')
        self.label(self.dashboard_tab,'',10,MUTED,textvariable=self.network_dashboard_status).pack(anchor='w')
        self.action(self.dashboard_tab,'Manage network streams  →',
                    lambda:self.tabs.select(self.network_tab)).pack(anchor='w',pady=(7,10))
        self.label(self.dashboard_tab,'PREVIEW / RECORD',11,WHITE,True).pack(anchor='w',pady=(9,4))
        self.label(self.dashboard_tab,
                   'Use the Preview and Start Session buttons above. Polar sample readiness is '
                   'checked during session startup. The Live Monitoring tab displays incoming signals.',
                   10,MUTED,wraplength=790,justify='left').pack(anchor='w')
        self.action(self.dashboard_tab,'Open live monitoring  →',
                    lambda:self.tabs.select(self.monitor_tab)).pack(anchor='w',pady=(10,0))

    def _update_selection(self):
        count=sum(v.get() for v in self.enabled.values())
        netcount=sum(1 for v in self.network_selections.values() if v.get('enabled'))
        self.count_label.set(f'{count + netcount} selected')
        if hasattr(self,'readiness_rows'):
            for n,var in self.readiness_rows.items():
                var.set(('Enabled  ·  '+self.status[n].get()) if self.enabled[n].get() else 'Not included')
            self.network_dashboard_status.set(f'{netcount} external stream(s) selected')
        if not self.session.active and not self.preview_active:self.session_status.set('Ready to record' if (count or netcount) else 'Choose devices to begin')

    def toggle(self,name):
        if self.session.active or self.preview_active:
            self.enabled[name].set(not self.enabled[name].get())
            return
        self.status[name].set('Selected · configure →' if self.enabled[name].get() else 'Not selected')
        self.select_device(name)
        self._rebuild_monitor()
        self._update_selection()

    def browse(self):
        path=filedialog.askdirectory(initialdir=self.folder.get())
        if path:self.folder.set(path)

    def show_windows_audio_names(self):
        names = self.windows_sound_names
        messagebox.showinfo(
            'Sound, video and game controllers',
            ('Windows hardware names:\n\n' + '\n'.join('• ' + x for x in names))
            if names else 'No Windows hardware names were obtained. Check the Activity log.'
        )

    def _load_mic_aliases(self):
        try:
            data=json.loads(self.alias_path.read_text(encoding='utf-8'))
            return data if isinstance(data,dict) else {}
        except FileNotFoundError:
            return {}
        except (ValueError,OSError) as exc:
            self.bus.emit('Audio','WARNING',f'Could not read microphone names: {exc!r}')
            return {}

    @staticmethod
    def _mic_key(index, device, hostapi_name):
        # A locally recognizable endpoint fingerprint, NOT a permanent hardware ID.
        # Matching an index across changed Windows device layouts is not assumed safe.
        return json.dumps([int(index),str(device['name']),str(hostapi_name)],ensure_ascii=False)

    def _selected_mic_key(self):
        pos=self.mic_select.current()
        if not 0 <= pos < len(self.device_choices):
            return None
        import sounddevice as sd
        index=self.device_choices[pos][0]
        device=sd.query_devices(index)
        api=sd.query_hostapis(device['hostapi'])['name']
        return self._mic_key(index,device,api)

    def save_mic_alias(self):
        if self.session.active or self.preview_active:
            self.bus.emit('Audio','WARNING','Stop Preview/Record before changing microphone names')
            return
        key=self._selected_mic_key()
        if key is None:
            self.bus.emit('Audio','WARNING','Select a microphone endpoint before naming it')
            return
        alias=self.mic_friendly_name.get().strip()
        if not alias or len(alias)>80:
            self.bus.emit('Audio','WARNING','Enter a microphone name between 1 and 80 characters')
            return
        self.mic_aliases[key]=alias
        try:
            self.alias_path.write_text(json.dumps(self.mic_aliases,indent=2,ensure_ascii=False),encoding='utf-8')
        except OSError as exc:
            self.bus.emit('Audio','ERROR',f'Could not save microphone name: {exc!r}')
            return
        self.bus.emit('Audio','INFO',f'Friendly microphone name saved: {alias} (user assigned, not hardware verified)')
        self.refresh_audio()

    def clear_mic_alias(self):
        if self.session.active or self.preview_active:
            self.bus.emit('Audio','WARNING','Stop Preview/Record before changing microphone names')
            return
        key=self._selected_mic_key()
        if key is None:return
        self.mic_aliases.pop(key,None)
        try:
            self.alias_path.write_text(json.dumps(self.mic_aliases,indent=2,ensure_ascii=False),encoding='utf-8')
        except OSError as exc:
            self.bus.emit('Audio','ERROR',f'Could not clear microphone name: {exc!r}')
            return
        self.bus.emit('Audio','INFO','User microphone label cleared')
        self.refresh_audio()

    def refresh_audio(self):
        if self.session.active or self.preview_active:
            self.bus.emit('Audio','WARNING','Stop Preview/Record before refreshing audio inputs')
            return
        try:
            old_key = self.device_choices[self.mic_select.current()][0] if (
                0 <= self.mic_select.current() < len(self.device_choices)) else None
            try:
                self.windows_sound_names = device_manager_sound_names()
                self.bus.emit('Audio','INFO',
                              'Windows sound hardware: ' + (', '.join(self.windows_sound_names) or 'none reported'))
            except Exception as exc:
                self.windows_sound_names = []
                self.bus.emit('Audio','WARNING',f'Windows hardware name lookup failed: {exc!r}')
            self.windows_hardware_info.set(', '.join(self.windows_sound_names) if self.windows_sound_names else 'No hardware names reported (or lookup failed)')
            raw_inputs = available_inputs()
            self.audio_name_lookup = {}
            self.device_choices = []
            import sounddevice as sd
            for index, endpoint_label in raw_inputs:
                endpoint_name = sd.query_devices(index)['name'].strip()
                matched = safe_hardware_match(endpoint_name, self.windows_sound_names)
                api=sd.query_hostapis(sd.query_devices(index)['hostapi'])['name']
                alias=self.mic_aliases.get(self._mic_key(index,sd.query_devices(index),api))
                if alias:
                    label=f'{alias} [user label]  |  {endpoint_label}'
                elif matched:
                    label = f'{matched}  |  {endpoint_label}'
                    self.audio_name_lookup[index] = matched
                else:
                    label = endpoint_label
                self.device_choices.append((index, label))
            self.mic_select.configure(values=[label for _,label in self.device_choices])
            preferred = next((i for i,(index,_) in enumerate(self.device_choices)
                              if index == old_key),None)
            if preferred is None:
                preferred=next((i for i,(_,label) in enumerate(self.device_choices)
                                if 'ultramic' in label.lower()),None)
            if preferred is None and self.device_choices:
                preferred=0
            if preferred is not None:self.mic_select.current(preferred)
            else:self.mic_select.set('')
            self.on_audio_selected()
            self.bus.emit('Audio','INFO',f'{len(self.device_choices)} audio input endpoint(s) enumerated; '
                          f'{len(self.audio_name_lookup)} with unambiguous matching hardware name')
        except Exception as exc:self.bus.emit('Audio','ERROR',f'Audio discovery: {exc!r}')

    def on_audio_selected(self):
        pos=self.mic_select.current()
        if not 0 <= pos < len(self.device_choices):
            self.mic_details.set('No microphone selected')
            return
        import sounddevice as sd
        index,label=self.device_choices[pos]
        key=self._selected_mic_key()
        self.mic_friendly_name.set(self.mic_aliases.get(key,'') if key else '')
        try:
            detail=sd.query_devices(index)
            native=int(round(detail['default_samplerate']))
            ultramic='ultramic' in detail['name'].lower() or 'ultramic' in self.mic_friendly_name.get().lower()
            self.mic_rate.set('384000' if ultramic else str(native) if native in (44100,48000,96000,192000,384000) else '48000')
            self.mic_details.set(f"Device Manager match: {self.audio_name_lookup.get(index, 'Not verified')} | Audio endpoint: {detail['name']} | Input channels: {detail['max_input_channels']} "
                                 f"| Default sample rate: {native:,} Hz | {'Ultrasonic mode' if ultramic else 'Standard audio mode'}")
        except Exception as exc:
            self.mic_details.set(f'Could not read device details: {exc!r}')

    def scan_polar(self):
        self.polar_detail.set('Scanning Bluetooth…')
        try:
            future=self.recorders['Polar H10'].scan()
            future.add_done_callback(lambda f:self.bus.events.put(('POLAR_SCAN','DONE',f)))
        except Exception as exc:self.bus.emit('Polar H10','ERROR',repr(exc))

    def _build_network_lsl(self):
        self.label(self.network_tab, 'Network LSL Stream Manager', 17, WHITE, True).pack(anchor='w')
        self.label(self.network_tab, 'Single entry point for SeeTrue and external LSL streams. IMMSS outlets retain the existing IMS_ prefix for data compatibility; external names are supplied by their broadcasters.',
                   10, MUTED, wraplength=800, justify='left').pack(anchor='w', pady=(5,10))
        row=tk.Frame(self.network_tab,bg=PANEL); row.pack(fill='x',pady=(0,8))
        self.action(row, 'Discover network streams', self.scan_network_lsl, accent=True).pack(side='left')
        self.network_status=self.label(row,'Not scanned',10,MUTED)
        self.network_status.pack(side='left',padx=14)
        self.label(self.network_tab,'Tick the streams to save, then click a row to configure its channels.',10,MUTED).pack(anchor='w')
        frame=tk.Frame(self.network_tab,bg=PANEL);frame.pack(fill='both',expand=True)
        self.network_list=tk.Listbox(frame,selectmode='browse',height=9,exportselection=False,
              bg=BG,fg=WHITE,selectbackground='#345a7e',font=('Consolas',10))
        self.network_list.pack(side='left',fill='both',expand=True)
        bar=ttk.Scrollbar(frame,command=self.network_list.yview)
        bar.pack(side='left',fill='y');self.network_list.configure(yscrollcommand=bar.set)
        self.network_list.bind('<<ListboxSelect>>',lambda e:self.describe_network_stream())
        self.network_checks=tk.Frame(self.network_tab,bg=PANEL)
        self.network_checks.pack(fill='x',pady=(8,4))
        self.network_info=tk.StringVar(value='Select a stream to configure channels')
        self.label(self.network_tab,'',10,MUTED,textvariable=self.network_info,wraplength=800,justify='left').pack(anchor='w')
        tools=tk.Frame(self.network_tab,bg=PANEL);tools.pack(fill='x',pady=(8,4))
        self.action(tools,'Select all channels',lambda:self.network_set_channels(True)).pack(side='left',padx=(0,8))
        self.action(tools,'Clear channels',lambda:self.network_set_channels(False)).pack(side='left')
        holder=tk.Frame(self.network_tab,bg=PANEL);holder.pack(fill='both',expand=True)
        self.net_canvas=tk.Canvas(holder,bg=PANEL,highlightthickness=0,height=150)
        self.net_canvas.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(holder,orient='vertical',command=self.net_canvas.yview)
        scroll.pack(side='left',fill='y');self.net_canvas.configure(yscrollcommand=scroll.set)
        self.net_channel_frame=tk.Frame(self.net_canvas,bg=PANEL)
        self.net_window=self.net_canvas.create_window((0,0),window=self.net_channel_frame,anchor='nw')
        self.net_channel_frame.bind('<Configure>',lambda e:self.net_canvas.configure(scrollregion=self.net_canvas.bbox('all')))
        self.net_canvas.bind('<Configure>',lambda e:self.net_canvas.itemconfigure(self.net_window,width=e.width))

    def scan_network_lsl(self):
        if self.session.active or self.preview_active or self._starting_session:return
        self.network_status.configure(text='Searching…')
        def work():
            try:
                streams=NetworkLSLRecorder.scan(timeout=3)
                self.bus.events.put(('NET_LSL_SCAN','DONE',streams))
            except Exception as exc:
                self.bus.emit('Network LSL','ERROR',f'Discovery error: {exc!r}')
                self.bus.events.put(('NET_LSL_SCAN','DONE',[]))
        threading.Thread(target=work,daemon=True).start()

    def network_toggle_stream(self):
        if self.session.active or self.preview_active:return
        i=self.network_list.curselection()
        if not i:return
        uid=self.network_choices[i[0]]['uid']
        entry=self.network_selections[uid]
        entry['enabled']=not entry['enabled']
        self._update_network_rows()

    def _update_network_rows(self):
        keep=self.network_list.curselection()
        self.network_list.delete(0,'end')
        for stream in self.network_choices:
            state=self.network_selections.get(stream['uid'],{}).get('enabled',False)
            label=(('☑' if state else '☐') + f" {stream['name']} | {stream['type']} | {stream['channels']}ch "
                + f"| {stream.get('nominal_srate',0):g}Hz | {stream.get('hostname','?')} | {stream['uid'][:12]}")
            self.network_list.insert('end',label)
        if keep and keep[0]<self.network_list.size():self.network_list.selection_set(keep[0])
        active=sum(v['enabled'] for v in self.network_selections.values())
        self.network_status.configure(text=f'{len(self.network_choices)} discovered · {active} selected')

    def describe_network_stream(self):
        for w in self.network_checks.winfo_children():w.destroy()
        for w in self.net_channel_frame.winfo_children():w.destroy()
        self.network_channel_vars=[]
        selected=self.network_list.curselection()
        if not selected:return
        stream=self.network_choices[selected[0]]; uid=stream['uid'];self.network_current_uid=uid
        record=self.network_selections[uid]
        self.action(self.network_checks, 'Disable stream' if record['enabled'] else 'Enable stream',
                    self.network_toggle_stream,accent=True).pack(side='left')
        self.network_info.set(f"Data type: {stream['type']} | Stream: {stream['name']} | UID: {uid} | Source ID: {stream['source_id']} | "
                              f"Host: {stream.get('hostname','?')} | {stream['channels']} channels")
        for j,label in enumerate(stream['labels']):
            v=tk.BooleanVar(value=j in record['channels']);self.network_channel_vars.append(v)
            tk.Checkbutton(self.net_channel_frame,text=f'{j+1:3d}  {label}',variable=v,
                command=lambda u=uid:self.save_network_channels(u),bg=PANEL,fg=WHITE,
                selectcolor=BG,activebackground=PANEL,activeforeground=WHITE,
                anchor='w').pack(fill='x')

    def save_network_channels(self,uid):
        if self.session.active or self.preview_active:return
        self.network_selections[uid]['channels']=[i for i,v in enumerate(self.network_channel_vars) if v.get()]

    def network_set_channels(self,value):
        if self.session.active or self.preview_active:return
        for v in self.network_channel_vars:v.set(value)
        if self.network_current_uid:self.save_network_channels(self.network_current_uid)

    def audio_args(self,box,rate):
        i = box.current()
        if not 0 <= i < len(self.device_choices):
            raise ValueError('Select an audio input first.')
        device = self.device_choices[i][0]
        details = sd.query_devices(device, 'input')
        backend = sd.query_hostapis(details['hostapi'])['name']
        if rate >= 384000 and 'WASAPI' not in backend.upper():
            raise ValueError(
                f'384 kHz requires the Ultramic WASAPI endpoint. '
                f'Currently selected: {details["name"]} ({backend}). '
                'Choose Microphone (UltraMic384K_PRO 16bit r0) — Windows WASAPI.'
            )
        return {'device': device, 'sample_rate': rate, 'channels': 1}

    def _selected_recorders(self):
        selected={}
        if self.enabled['Microphone'].get():
            selected['Microphone']=(self.recorders['Microphone'],
                                   self.audio_args(self.mic_select,int(self.mic_rate.get())))
        if self.enabled['Polar H10'].get():
            selected['Polar H10']=(self.recorders['Polar H10'],{'ble_device':self.ble_device,'ecg':self.ecg.get(),'acc':self.acc.get()})
        for i in range(1,4):
            n=f'Camera {i}'
            if self.enabled[n].get():
                choice=self.camera_selects[i].current()
                if not 0 <= choice < len(self.camera_choices):
                    raise ValueError(f'Camera {i}: select a detected camera in the Cameras tab')
                index=self.camera_choices[choice][0]
                selected[n]=(self.recorders[n],{'index':index, 'camera_settings':self.camera_settings(i)})
        # All external LSL streams, including SeeTrue, use this single manager.
        for n, stream in enumerate(self.network_choices, 1):
            settings=self.network_selections.get(stream['uid'])
            if not settings or not settings['enabled']:continue
            channels=NetworkLSLRecorder.validate_selection(settings['channels'],stream['channels'])
            key=f'Network LSL {n:02d} · {stream["name"]}'
            recorder=NetworkLSLRecorder(self.bus,name=key,filename_prefix=f'network_lsl_{n:02d}')
            selected[key]=(recorder,{'uid':stream['uid'],'channel_indices':channels})
        camera_indexes=[settings['index'] for name,(_,settings) in selected.items() if name.startswith('Camera')]
        if len(set(camera_indexes)) != len(camera_indexes):
            raise ValueError('Camera slots must use different physical camera indices')
        if not selected:raise ValueError('Enable a device or select a network LSL stream.')
        return selected

    def preview(self):
        if self.session.active:return
        if self.preview_active:
            self.stop_preview()
            return
        try:
            if not any(v.get() for v in self.enabled.values()) and self.mic_select.current() >= 0:
                self.enabled['Microphone'].set(True)
                self.status['Microphone'].set('Selected · configure →')
                self._rebuild_monitor()
                self.bus.emit('Preview','INFO','No devices were enabled; microphone selected for Preview automatically')
            selected=self._selected_recorders()
            self.preview_tmp=tempfile.TemporaryDirectory(prefix='multimodal_preview_')
            self.preview_devices=selected
            self.preview_started=set()
            self.preview_active=True
            started_count=0
            for name,(rec,settings) in selected.items():
                try:
                    if name.startswith('Camera'):
                        slot=int(name.rsplit(' ',1)[1])
                        p=self.camera_previews[slot]
                        if not (p.thread and p.thread.is_alive()):
                            p.start(settings['index'], settings=settings.get('camera_settings'))
                        self.status[name].set('PREVIEW')
                    else:
                        rec.start(Path(self.preview_tmp.name), **settings)
                        self.status[name].set('PREVIEW')
                    started_count += 1
                    self.preview_started.add(name)
                except Exception as exc:
                    self.status[name].set('ERROR')
                    self.bus.emit(name,'ERROR',f'Preview start failed: {exc!r}')
            if not started_count:
                raise RuntimeError('No selected device could start Preview. Check Activity & errors for the exact reason.')
            self.preview_btn.configure(text='■ STOP PREVIEW')
            self.status_top.configure(text='●  PREVIEW',fg=BLUE)
            self.session_status.set('Preview mode · temporary test capture')
            self._lock(True)
            self.tabs.select(self.monitor_tab)
        except Exception as exc:
            self.bus.emit('Preview','ERROR',f'Preview setup failed: {exc!r}')
            self.stop_preview()
            messagebox.showerror('Preview could not start',str(exc))

    def stop_preview(self):
        if not self.preview_active:return
        for name,(rec,_) in self.preview_devices.items():
            if name not in self.preview_started:
                continue
            try:
                if name.startswith('Camera'):
                    self.camera_previews[int(name.rsplit(' ',1)[1])].stop()
                else:
                    rec.stop()
            except Exception as exc:self.bus.emit(name,'ERROR',f'Preview stop failed: {exc!r}')
        self.preview_active=False
        self.preview_devices={}
        self.preview_started=set()
        if self.preview_tmp:
            try:self.preview_tmp.cleanup()
            except Exception as exc:self.bus.emit('Preview','WARNING',f'Preview cleanup: {exc!r}')
            self.preview_tmp=None
        self.preview_btn.configure(text='▶  PREVIEW')
        self.status_top.configure(text='●  IDLE',fg=GREEN)
        self.session_status.set('Preview stopped · ready to record')
        self._lock(False)

    def start(self):
        if self.session.active or self._starting_session:
            return
        if self.preview_active:
            self.stop_preview()
        try:
            selected = self._selected_recorders()
            for slot, p in self.camera_previews.items():
                if p.thread and p.thread.is_alive():
                    p.stop()
                    if p.thread and p.thread.is_alive():
                        raise RuntimeError(f'Camera {slot} preview did not release the device')
                    self.camera_buttons[slot].configure(text='▶ Test preview')
            folder, participant = self.folder.get(), self.participant.get()
            self._starting_session = True
            self.start_btn.configure(state='disabled')
            self.preview_btn.configure(state='disabled')
            self._lock(True)
            self.session_status.set('Connecting selected Polar and checking live streams…' if 'Polar H10' in selected else 'Starting recording…')
            self.status_top.configure(text='●  CONNECTING',fg=YELLOW)
            def worker():
                try:
                    path = self.session.start(folder, participant, selected)
                    self._start_results.put((True,path))
                except Exception as exc:
                    self._start_results.put((False,exc))
            threading.Thread(target=worker,daemon=True,name='SessionStartup').start()
        except Exception as exc:
            self.bus.emit('Session','ERROR',f'Session start failed: {exc!r}')
            self._starting_session=False
            self._lock(False)
            self.start_btn.configure(state='normal')
            self.preview_btn.configure(state='normal')
            messagebox.showerror('Session could not start',str(exc))

    def _handle_start_result(self, ok, result):
        self._starting_session = False
        if ok:
            self.started_at=datetime.now()
            self.status_top.configure(text='●  RECORDING',fg=GREEN)
            self.session_status.set('Recording · '+result.name)
            self.start_btn.configure(state='disabled')
            self.stop_btn.configure(state='normal')
            self.preview_btn.configure(state='disabled')
            self.marker_btn.configure(state='normal')
            self.tabs.select(self.monitor_tab)
        else:
            self._lock(False)
            self.status_top.configure(text='●  IDLE',fg=GREEN)
            self.session_status.set('Session preflight failed — recording not started')
            self.start_btn.configure(state='normal')
            self.preview_btn.configure(state='normal')
            self.bus.emit('Session','ERROR',f'Session start failed: {result!r}')
            messagebox.showerror('Session could not start',str(result))

    def _lock(self,locked):
        state='disabled' if locked else 'normal'
        for c in self.device_enable_controls.values():c.configure(state=state)
        for widget in (self.mic_select,self.mic_rate_combo,*self.camera_selects.values()):
            widget.configure(state='disabled' if locked else 'readonly')
        for widgets in self.camera_setting_widgets.values():
            for widget in widgets:
                try:
                    widget.configure(state='disabled' if locked else ('readonly' if isinstance(widget, ttk.Combobox) else 'normal'))
                except tk.TclError:
                    pass
        for widget in self.net_channel_frame.winfo_children():
            widget.configure(state='disabled' if locked else 'normal')
        for widget in self.network_checks.winfo_children():
            widget.configure(state='disabled' if locked else 'normal')

    def stop(self):
        if not self.session.active:return
        self.session.stop()
        self.started_at=None
        self.status_top.configure(text='●  IDLE',fg=GREEN)
        self.start_btn.configure(state='normal');self.stop_btn.configure(state='disabled');self.preview_btn.configure(state='normal')
        self.marker_btn.configure(state='disabled')
        self._lock(False)
        self.session_status.set('Session saved · ready for next run')

    def mark(self):
        if self.session.active:
            text=self.marker_label.get().strip() or 'EVENT'
            self.session.marker(text)
            self._log(f'MARKER   {text}')

    def poll(self):
        try:
            ok, result = self._start_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self._handle_start_result(ok, result)
        for _ in range(120):
            try:name,status,message=self.bus.events.get_nowait()
            except queue.Empty:break
            if name=='POLAR_SCAN':
                try:
                    self.ble_device=message.result()
                    self.polar_detail.set(str(self.ble_device) if self.ble_device else 'No Polar H10 found')
                except Exception as exc:
                    self.polar_detail.set('BLE scan failed: '+repr(exc))
                    self.bus.emit('Polar H10','ERROR',f'BLE scan failed: {exc!r}')
                continue
            if name=='CAMERA_SCAN':
                self._camera_scanning=False
                old_indices = {}
                for slot, combo in self.camera_selects.items():
                    current = combo.current()
                    if 0 <= current < len(self.camera_choices):
                        old_indices[slot] = self.camera_choices[current][0]
                self.camera_choices=message
                labels=[label for _,label in message]
                index_map={index:position for position,(index,_) in enumerate(message)}
                used=set()
                for slot,combo in self.camera_selects.items():
                    combo.configure(values=labels)
                    prior=old_indices.get(slot)
                    if prior in index_map and prior not in used:
                        combo.current(index_map[prior]); used.add(prior)
                    else:
                        combo.set('')
                        if self.enabled[f'Camera {slot}'].get():
                            self.status[f'Camera {slot}'].set('Choose a detected camera')
                self.bus.emit('Cameras','INFO',f'Camera scan finished: {len(labels)} available')
                if not labels:
                    self.bus.emit('Cameras','WARNING','No usable camera frames detected. Check Windows Settings > Privacy & security > Camera > Let desktop apps access your camera; close Teams/Zoom/Camera app and retry. Integrated IR camera may be Windows Hello-only.')
                continue
            if name=='NET_LSL_SCAN':
                previous=self.network_selections
                self.network_choices=message
                self.network_selections={x['uid']:{'enabled':previous.get(x['uid'],{}).get('enabled',False),
                    'channels':previous.get(x['uid'],{}).get('channels',list(range(x['channels'])))}
                    for x in message}
                self._update_network_rows()
                if message:
                    self.network_list.selection_set(0)
                    self.describe_network_stream()
                self.bus.emit('Network LSL','INFO',f'Discovered {len(message)} LSL outlets; inspect type, source ID and UID before selection')
                continue
            if name in self.status:self.status[name].set(status)
            self._log(f'{name:<13} {status:<10} {message}')
        if self.session.active or self.preview_active:
            if self.started_at:
                s=int((datetime.now()-self.started_at).total_seconds())
                self.time_label.set(f'{s//3600:02}:{s//60%60:02}:{s%60:02}')
            for name,rec in (self.session.recorders if self.session.active else {n:r for n,(r,_) in self.preview_devices.items()}).items():
                if hasattr(rec,'health'):
                    try:rec.health()
                    except Exception as exc:self.bus.emit(name,'ERROR',f'Health check: {exc!r}')
        self._update_selection()
        try:
            self._draw_monitors()
        except Exception as exc:
            self.bus.emit('Monitoring','ERROR',f'Live plot update failed: {exc!r}')
        self.root.after(500,self.poll)

    def close(self):
        if self._starting_session:
            messagebox.showwarning('Connecting', 'Polar preflight is still running. Close after it completes or times out.')
            return
        if self.session.active:
            if not messagebox.askyesno('Stop and exit','Stop the current session and close the application?'):return
            self.stop()
        if self.preview_active:self.stop_preview()
        for preview in self.camera_previews.values():
            preview.stop()
        try:self.recorders['Polar H10'].close()
        except Exception as exc:self.bus.emit('Polar H10','WARNING',f'BLE loop close: {exc!r}')
        self.bus.emit('Application','INFO','Application closing')
        self.bus.close()
        self.root.destroy()

if __name__=='__main__':
    root=tk.Tk()
    App(root)
    root.mainloop()
