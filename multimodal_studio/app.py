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
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from core import EventBus, SessionManager
from devices.audio import AudioRecorder, available_inputs
from devices.windows_audio import device_manager_sound_names, safe_hardware_match
from devices.polar import PolarRecorder
from devices.seetrue import SeeTrueRecorder
from devices.camera_recorder import CameraRecorder
from devices.camera_preview import CameraPreview, discover_cameras
import sounddevice as sd

BASE = Path(__file__).resolve().parent
BG, PANEL, CARD, WHITE = '#101827', '#172337', '#20304a', '#ecf3ff'
MUTED, BLUE, GREEN, YELLOW, RED = '#aabbd2', '#55a6ff', '#51d4a2', '#ffc66d', '#ff7585'
DEVICES = ['Microphone', 'Polar H10', 'SeeTrue', 'Camera 1', 'Camera 2', 'Camera 3']
ICONS = {'Microphone':'MIC', 'Polar H10':'HEART', 'SeeTrue':'EYE', 'Camera 1':'CAM 1', 'Camera 2':'CAM 2', 'Camera 3':'CAM 3'}
DESCRIPTIONS = {'Microphone':'Choose any Windows audio input', 'Polar H10':'Heart rate, ECG, motion', 'SeeTrue':'LSL gaze stream', 'Camera 1':'Camera preview + MP4 recording', 'Camera 2':'Camera preview + MP4 recording', 'Camera 3':'Camera preview + MP4 recording'}

class App:
    def __init__(self, root):
        self.root = root
        self.root.title('Multimodal Studio  |  Experiment Recorder')
        self.root.geometry('1280x850')
        self.root.minsize(1030, 720)
        self.root.configure(bg=BG)
        self.bus = EventBus()
        self.bus.install_exception_hooks()
        self.bus.emit("Application", "INFO", f"Application started; persistent log: {self.bus.application_log}")
        self.session = SessionManager(self.bus)
        self.recorders = {'Microphone':AudioRecorder('Microphone',self.bus),
                          'Polar H10':PolarRecorder(self.bus),
                          'SeeTrue':SeeTrueRecorder(self.bus),
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
        self.lsl_details = tk.StringVar(value='Scan to locate the SeeTrue broadcaster.')
        self.see_live = tk.StringVar(value='Waiting for LSL samples')
        self.device_choices, self.lsl_choices, self.ble_device = [], [], None
        self.windows_sound_names = []
        self.audio_name_lookup = {}
        self.lsl_channel_vars = []
        self.selected_device = 'Microphone'
        self.started_at = None
        self.preview_active = False
        self.preview_tmp = None
        self.preview_devices = {}
        self.buttons = {}
        self.monitor_panels = {}
        self.camera_previews = {i: CameraPreview(i, self.bus) for i in range(1, 4)}
        self.camera_choices = []
        self.camera_selects = {}
        self.camera_images = {}
        self.camera_labels = {}
        self.camera_status = {}
        self.camera_buttons = {}
        self._camera_scanning = False
        self._style()
        self._build()
        self.refresh_audio()
        self.select_device('Microphone')
        self.root.after(350, self.poll)
        self.root.protocol('WM_DELETE_WINDOW',self.close)

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
        head = tk.Frame(self.root,bg=BG,padx=24,pady=16); head.pack(fill='x')
        self.label(head,'MULTIMODAL STUDIO',19,WHITE,True).pack(side='left')
        self.label(head,'  /  EXPERIMENT RECORDER',11,MUTED).pack(side='left',pady=(7,0))
        self.status_top=self.label(head,'●  IDLE',11,GREEN,True)
        self.status_top.pack(side='right')
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
        left=tk.Frame(body,bg=PANEL,width=300,padx=12,pady=14); left.pack(side='left',fill='y',padx=(0,12)); left.pack_propagate(False)
        self.label(left,'DEVICES',11,WHITE,True).pack(anchor='w',padx=9)
        self.label(left,'Choose only what you need',9,MUTED).pack(anchor='w',padx=9,pady=(0,9))
        self.cards={}
        for device in DEVICES:
            card=tk.Frame(left,bg=CARD,padx=10,pady=8,highlightthickness=1,highlightbackground='#334662')
            card.pack(fill='x',pady=4)
            row=tk.Frame(card,bg=CARD);row.pack(fill='x')
            toggle=tk.Checkbutton(row,variable=self.enabled[device],command=lambda n=device:self.toggle(n),
                                  bg=CARD,fg=WHITE,activebackground=CARD,selectcolor=BG,
                                  bd=0,highlightthickness=0,cursor='hand2')
            toggle.pack(side='left')
            btn=tk.Button(row,text=device,command=lambda n=device:self.select_device(n),
                          bg=CARD,fg=WHITE,activebackground='#304865',activeforeground=WHITE,
                          bd=0,relief='flat',cursor='hand2',font=('Segoe UI',11,'bold'),anchor='w')
            btn.pack(side='left',fill='x',expand=True)
            self.label(card,DESCRIPTIONS[device],9,MUTED).pack(anchor='w',padx=27)
            state=self.label(card,'Not selected',9,MUTED)
            state.pack(anchor='w',padx=27)
            self.cards[device]=(card,state,toggle)
        self.label(left,'Selected cameras save independent MP4 and frame timestamps',9,YELLOW,wraplength=260).pack(anchor='w',padx=9,pady=(14,0))
        right=tk.Frame(body,bg=BG);right.pack(side='left',fill='both',expand=True)
        overview=tk.Frame(right,bg=PANEL,padx=17,pady=13);overview.pack(fill='x',pady=(0,10))
        top=tk.Frame(overview,bg=PANEL);top.pack(fill='x')
        self.label(top,'SESSION OVERVIEW',10,MUTED,True).pack(side='left')
        self.label(top,'Elapsed',10,MUTED).pack(side='right',padx=(12,0))
        self.label(top,'  •  ',10,MUTED).pack(side='right')
        self.label(top,'Selected',10,MUTED).pack(side='right')
        vals=tk.Frame(overview,bg=PANEL);vals.pack(fill='x',pady=(5,0))
        self.label(vals,'Ready when you are',12,WHITE,True,textvariable=self.session_status).pack(side='left')
        self.label(vals,'00:00:00',19,WHITE,True,textvariable=self.time_label).pack(side='right')
        self.label(vals,'0 selected',11,BLUE,True,textvariable=self.count_label).pack(side='right',padx=(0,30))
        self.tabs=ttk.Notebook(right);self.tabs.pack(fill='both',expand=True)
        self.configure_tab=tk.Frame(self.tabs,bg=PANEL,padx=22,pady=22)
        self.monitor_tab=tk.Frame(self.tabs,bg=PANEL,padx=12,pady=12)
        self.see_tab=tk.Frame(self.tabs,bg=PANEL,padx=22,pady=22)
        self.camera_tab=tk.Frame(self.tabs,bg=PANEL,padx=22,pady=22)
        self.activity_tab=tk.Frame(self.tabs,bg=PANEL,padx=16,pady=16)
        for name,frame in [('Device settings',self.configure_tab),('Live monitoring',self.monitor_tab),('SeeTrue · LSL',self.see_tab),('3 camera previews',self.camera_tab),('Activity & errors',self.activity_tab)]:
            self.tabs.add(frame,text='  '+name+'  ')
        self.config_head=self.label(self.configure_tab,'Device settings',17,WHITE,True)
        self.config_head.pack(anchor='w')
        self.config_desc=self.label(self.configure_tab,'',10,MUTED,wraplength=680,justify='left');self.config_desc.pack(anchor='w',pady=(4,18))
        self.settings_container=tk.Frame(self.configure_tab,bg=PANEL);self.settings_container.pack(fill='both',expand=True)
        self.settings={}
        for n in DEVICES:
            f=tk.Frame(self.settings_container,bg=PANEL)
            self.settings[n]=f
        self._build_monitor()
        self._build_configs()
        self._build_seetrue()
        self._build_cameras()
        self._build_activity()
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
        f=self.settings['SeeTrue']
        self.label(f,'SeeTrue receives data through LSL',11,WHITE,True).pack(anchor='w')
        self.label(f,'Open the SeeTrue · LSL tab to discover the stream, review channel metadata and select it.',10,MUTED,wraplength=640,justify='left').pack(anchor='w',pady=(8,15))
        self.action(f,'Open SeeTrue · LSL →',lambda:self.tabs.select(self.see_tab),accent=True).pack(anchor='w')
        for i in range(1,4):
            f=self.settings[f'Camera {i}']
            self.label(f,f'Camera {i} — device selection and live preview',12,WHITE,True).pack(anchor='w')
            self.label(f,'Choose a camera in the Cameras tab. The same selection is used for MP4 recording and frame timestamps.',10,YELLOW,wraplength=670,justify='left').pack(anchor='w',pady=(10,14))
            self.action(f,'Configure camera →',lambda:self.tabs.select(self.camera_tab)).pack(anchor='w')

    def _build_seetrue(self):
        self.label(self.see_tab,'SEETRUE EYE TRACKER',17,WHITE,True).pack(anchor='w')
        self.label(self.see_tab,'Step 1: start SeeTrue LSL broadcaster. Step 2: discover. Step 3: select a stream. Then enable SeeTrue on the left.',10,MUTED,wraplength=690,justify='left').pack(anchor='w',pady=(5,20))
        row=tk.Frame(self.see_tab,bg=PANEL);row.pack(fill='x')
        self.action(row,'↻ Discover LSL streams',self.scan_lsl,accent=True).pack(side='left')
        self.lsl_scan_state=self.label(row,'Not scanned',10,MUTED)
        self.lsl_scan_state.pack(side='left',padx=14)
        self.label(self.see_tab,'Available streams',11,WHITE,True).pack(anchor='w',pady=(20,7))
        self.lsl_select=ttk.Combobox(self.see_tab,state='readonly',width=76)
        self.lsl_select.pack(fill='x')
        self.lsl_select.bind('<<ComboboxSelected>>',lambda event:self.describe_lsl())
        self.label(self.see_tab,'Selected stream details',11,WHITE,True).pack(anchor='w',pady=(20,7))
        self.label(self.see_tab,'',10,MUTED,textvariable=self.lsl_details,wraplength=750,justify='left').pack(anchor='w')
        self.label(self.see_tab,'Channels to RECORD',11,WHITE,True).pack(anchor='w',pady=(14,5))
        toolrow=tk.Frame(self.see_tab,bg=PANEL);toolrow.pack(fill='x',pady=(0,5))
        self.action(toolrow,'Select all',lambda:self.set_lsl_channels(True)).pack(side='left',padx=(0,6))
        self.action(toolrow,'Clear selection',lambda:self.set_lsl_channels(False)).pack(side='left')
        self.lsl_channel_count=self.label(toolrow,'Choose a stream',10,MUTED)
        self.lsl_channel_count.pack(side='left',padx=12)
        holder=tk.Frame(self.see_tab,bg=PANEL);holder.pack(fill='both',expand=True)
        self.lsl_channel_canvas=tk.Canvas(holder,bg=PANEL,highlightthickness=0,height=180)
        scroll=ttk.Scrollbar(holder,orient='vertical',command=self.lsl_channel_canvas.yview)
        self.lsl_channel_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y')
        self.lsl_channel_canvas.pack(side='left',fill='both',expand=True)
        self.lsl_channel_box=tk.Frame(self.lsl_channel_canvas,bg=PANEL)
        self.lsl_channel_window=self.lsl_channel_canvas.create_window((0,0),window=self.lsl_channel_box,anchor='nw')
        self.lsl_channel_box.bind('<Configure>',lambda e:self.lsl_channel_canvas.configure(scrollregion=self.lsl_channel_canvas.bbox('all')))
        self.lsl_channel_canvas.bind('<Configure>',lambda e:self.lsl_channel_canvas.itemconfigure(self.lsl_channel_window,width=e.width))
        self.label(self.see_tab,'Live data',11,WHITE,True).pack(anchor='w',pady=(22,7))
        self.label(self.see_tab,'',10,GREEN,textvariable=self.see_live,wraplength=760,justify='left').pack(anchor='w')
        self.label(self.see_tab,'Channel availability depends on the SeeTrue LSL broadcaster; the software cannot infer missing channels.',9,MUTED,wraplength=730).pack(anchor='w',pady=(24,0))

    def _build_cameras(self):
        self.label(self.camera_tab,'CAMERA SETUP & LIVE PREVIEW',17,WHITE,True).pack(anchor='w')
        self.label(self.camera_tab,'Select a camera for each enabled slot. Test preview first; Start Session records the SAME camera to MP4.',10,YELLOW,wraplength=900).pack(anchor='w',pady=(4,10))
        self.action(self.camera_tab,'↻ Scan available cameras',self.scan_cameras,accent=True).pack(anchor='w',pady=(0,12))
        grid=tk.Frame(self.camera_tab,bg=PANEL);grid.pack(fill='both',expand=True)
        for i in range(1,4):
            grid.columnconfigure(i-1,weight=1)
            frame=tk.Frame(grid,bg='#0b1423',highlightbackground='#36506d',highlightthickness=1)
            frame.grid(row=0,column=i-1,sticky='nsew',padx=5)
            self.label(frame,f'CAMERA {i}',11,WHITE,True).pack(anchor='w',padx=12,pady=(12,4))
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
        grid.rowconfigure(0,weight=1)
        self.root.after(650, self._render_cameras)

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
            preview.start(index)
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
                info.configure(text=f'{assessment}  |  Peak: {peak:.4f}  |  RMS: {rec.level:.4f}  |  Saved: {rec.count:,}  |  Drops: {rec.lost_blocks}')
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
            elif name=='SeeTrue':
                rows=[(t,s) for t,s in list(rec.preview_timed) if now-5 <= t <= now]
                if rows:
                    for i in rec.selected_indices[:2] if hasattr(rec,'selected_indices') else range(min(2,len(rows[-1][1]))):
                        try:
                            pts=[(now-t,float(r[i])) for t,r in rows]
                            ax.plot(*zip(*pts),lw=1,label=rec.channels[i] if i<len(rec.channels) else f'Channel {i+1}')
                        except (ValueError,TypeError,IndexError):continue
                    ax.legend(loc='upper right',fontsize=7)
                info.configure(text=f'Samples: {rec.samples:,} · First two selected channels (numeric only)')
            canvas.draw_idle()

    def _build_activity(self):
        top=tk.Frame(self.activity_tab,bg=PANEL);top.pack(fill='x',pady=(0,10))
        self.label(top,'EVENT LOG',16,WHITE,True).pack(side='left')
        self.action(top,'Clear screen',lambda:self.log_text.delete('1.0','end')).pack(side='right')
        self.log_text=tk.Text(self.activity_tab,bg='#0b1423',fg=WHITE,insertbackground=WHITE,
                              font=('Consolas',10),bd=0,wrap='word',padx=12,pady=12,state='normal')
        self.log_text.pack(fill='both',expand=True)
        self._log('INFO  Interface initialized. Enable devices in the left panel.')

    def _log(self,text):
        self.log_text.insert('end',f"{datetime.now():%H:%M:%S}   {text}\n")
        self.log_text.see('end')

    def select_device(self,name):
        self.selected_device=name
        self.config_head.configure(text=name)
        self.config_desc.configure(text=DESCRIPTIONS[name]+'  ·  Toggle on the left to include it in the next recording.')
        for n,f in self.settings.items():
            f.pack_forget()
        self.settings[name].pack(fill='both',expand=True)
        for n,(card,_,_) in self.cards.items():
            card.configure(highlightbackground=BLUE if n==name else '#334662',highlightthickness=2 if n==name else 1)
        self.tabs.select(self.configure_tab)

    def _update_selection(self):
        count=sum(v.get() for v in self.enabled.values())
        self.count_label.set(f'{count} selected')
        if not self.session.active and not self.preview_active:self.session_status.set('Ready to record' if count else 'Choose devices to begin')
        for name,(_,label,_) in self.cards.items():
            state=self.status[name].get()
            label.configure(text=state,fg=RED if state in ('ERROR','DROPPED') else GREEN if state in ('RECORDING','RECOVERED') else (YELLOW if state=='WARNING' else MUTED))

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

    def scan_lsl(self):
        if self.session.active or self.preview_active:
            return
        self.lsl_scan_state.configure(text='Searching…')
        def work():
            try:result=SeeTrueRecorder.scan(timeout=2)
            except Exception as exc:
                self.bus.emit('SeeTrue','ERROR',f'Scan failed: {exc!r}')
                self.bus.events.put(('LSL_SCAN','DONE',[]));return
            self.bus.events.put(('LSL_SCAN','DONE',result))
        threading.Thread(target=work,daemon=True).start()

    def set_lsl_channels(self, checked):
        if self.session.active or self.preview_active:
            return
        for var in self.lsl_channel_vars:
            var.set(checked)
        self.update_lsl_count()

    def update_lsl_count(self):
        selected=sum(v.get() for v in self.lsl_channel_vars)
        self.lsl_channel_count.configure(text=f'{selected} / {len(self.lsl_channel_vars)} selected')

    def describe_lsl(self):
        i=self.lsl_select.current()
        for widget in self.lsl_channel_box.winfo_children():
            widget.destroy()
        self.lsl_channel_vars=[]
        if 0<=i<len(self.lsl_choices):
            stream=self.lsl_choices[i]
            self.lsl_details.set(f"Name: {stream['name']}\nType: {stream['type']}\nChannels: {stream['channels']}\nSource ID: {stream['source_id']}")
            labels=stream.get('labels',[])
            for j in range(stream['channels']):
                var=tk.BooleanVar(value=True)
                self.lsl_channel_vars.append(var)
                label=labels[j] if j<len(labels) else f'channel_{j+1}'
                tk.Checkbutton(self.lsl_channel_box, text=f'{j+1:3d}   {label}', variable=var,
                    command=self.update_lsl_count,bg=PANEL,fg=WHITE,
                    selectcolor=BG,activebackground=PANEL,activeforeground=WHITE,
                    anchor='w',state='disabled' if self.session.active or self.preview_active else 'normal').pack(fill='x')
        self.update_lsl_count()

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
        if self.enabled['SeeTrue'].get():
            i=self.lsl_select.current()
            if i<0:raise ValueError('Discover/select a SeeTrue LSL stream in the SeeTrue tab.')
            indices=[j for j,var in enumerate(self.lsl_channel_vars) if var.get()]
            SeeTrueRecorder.validate_selection(indices,self.lsl_choices[i]['channels'])
            selected['SeeTrue']=(self.recorders['SeeTrue'],{'uid':self.lsl_choices[i]['uid'],'channel_indices':indices})
        for i in range(1,4):
            n=f'Camera {i}'
            if self.enabled[n].get():
                choice=self.camera_selects[i].current()
                if not 0 <= choice < len(self.camera_choices):
                    raise ValueError(f'Camera {i}: select a detected camera in the Cameras tab')
                index=self.camera_choices[choice][0]
                selected[n]=(self.recorders[n],{'index':index})
        camera_indexes=[settings['index'] for name,(_,settings) in selected.items() if name.startswith('Camera')]
        if len(set(camera_indexes)) != len(camera_indexes):
            raise ValueError('Camera slots must use different physical camera indices')
        if not selected:raise ValueError('Enable at least one device on the left.')
        return selected

    def preview(self):
        if self.session.active:return
        if self.preview_active:
            self.stop_preview()
            return
        try:
            selected=self._selected_recorders()
            self.preview_tmp=tempfile.TemporaryDirectory(prefix='multimodal_preview_')
            self.preview_devices=selected
            self.preview_active=True
            for name,(rec,settings) in selected.items():
                try:
                    if name.startswith('Camera'):
                        slot=int(name.rsplit(' ',1)[1])
                        p=self.camera_previews[slot]
                        if not (p.thread and p.thread.is_alive()):
                            p.start(settings['index'])
                        self.status[name].set('PREVIEW')
                    else:
                        rec.start(Path(self.preview_tmp.name), **settings)
                        self.status[name].set('PREVIEW')
                except Exception as exc:
                    self.bus.emit(name,'ERROR',f'Preview start failed: {exc!r}')
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
            try:
                if name.startswith('Camera'):
                    self.camera_previews[int(name.rsplit(' ',1)[1])].stop()
                else:
                    rec.stop()
            except Exception as exc:self.bus.emit(name,'ERROR',f'Preview stop failed: {exc!r}')
        self.preview_active=False
        self.preview_devices={}
        if self.preview_tmp:
            try:self.preview_tmp.cleanup()
            except Exception as exc:self.bus.emit('Preview','WARNING',f'Preview cleanup: {exc!r}')
            self.preview_tmp=None
        self.preview_btn.configure(text='▶  PREVIEW')
        self.status_top.configure(text='●  IDLE',fg=GREEN)
        self.session_status.set('Preview stopped · ready to record')
        self._lock(False)

    def start(self):
        if self.session.active:return
        if self.preview_active:
            self.stop_preview()  # stop tests, then open the actual experiment files
        try:
            selected=self._selected_recorders()
            # Independent Test previews own their camera capture handles; release them
            # before session recorders open those same selected hardware inputs.
            for slot, p in self.camera_previews.items():
                if p.thread and p.thread.is_alive():
                    p.stop()
                    if p.thread and p.thread.is_alive():
                        raise RuntimeError(f'Camera {slot} preview did not release the device')
                    self.camera_buttons[slot].configure(text='▶ Test preview')
            path=self.session.start(self.folder.get(),self.participant.get(),selected)
            self.started_at=datetime.now()
            self.status_top.configure(text='●  RECORDING',fg=GREEN)
            self.session_status.set('Recording · '+path.name)
            self.start_btn.configure(state='disabled');self.stop_btn.configure(state='normal');self.preview_btn.configure(state='disabled')
            self.marker_btn.configure(state='normal')
            self._lock(True)
            self.tabs.select(self.monitor_tab)
        except Exception as exc:
            self.bus.emit('Session','ERROR',f'Session start failed: {exc!r}')
            messagebox.showerror('Session could not start',str(exc))

    def _lock(self,locked):
        state='disabled' if locked else 'normal'
        for _,(_,_,c) in self.cards.items():c.configure(state=state)
        for widget in (self.mic_select,self.mic_rate_combo,self.lsl_select,*self.camera_selects.values()):
            widget.configure(state='disabled' if locked else 'readonly')
        for widget in self.lsl_channel_box.winfo_children():
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
                self.camera_choices=message
                labels=[label for _,label in message]
                for combo in self.camera_selects.values():
                    combo.configure(values=labels)
                    if labels:combo.current(0)
                self.bus.emit('Cameras','INFO',f'Camera scan finished: {len(labels)} available')
                if not labels:
                    self.bus.emit('Cameras','WARNING','No usable camera frames detected. Check Windows Settings > Privacy & security > Camera > Let desktop apps access your camera; close Teams/Zoom/Camera app and retry. Integrated IR camera may be Windows Hello-only.')
                continue
            if name=='LSL_SCAN':
                self.lsl_choices=message
                labels=[f"{x['name']}  ·  {x['type']}  ·  {x['channels']} channels" for x in message]
                self.lsl_select.configure(values=labels)
                self.lsl_scan_state.configure(text=f'{len(labels)} streams found')
                if labels:self.lsl_select.current(0);self.describe_lsl()
                else:self.lsl_details.set('No streams discovered. Check SeeTrue broadcaster and network.')
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
            see=self.recorders['SeeTrue']
            if see.last_sample is not None:self.see_live.set(f'{see.samples:,} samples  ·  Latest: {str(see.last_sample)[:350]}')
        self._update_selection()
        self._draw_monitors()
        self.root.after(500,self.poll)

    def close(self):
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
