"""Embedded offline QA tab. Never touches active acquisition handles."""
from __future__ import annotations
import queue
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from analysis_core import find_streams, analyze, save_report


class PostExperimentTab(tk.Frame):
    def __init__(self, parent, root, session, bus=None):
        super().__init__(parent, bg='#1c2a40')
        self.root, self.session, self.bus = root, session, bus
        self.folder = tk.StringVar()
        self.events = tk.StringVar()
        self.selected = {}
        self.channel_vars = {}
        self.rate_vars = {}
        self.thresholds = {
            'start_ms': tk.StringVar(value='500'),
            'stop_ms': tk.StringVar(value='500'),
            'jitter_ms': tk.StringVar(value='10'),
            'gaps': tk.StringVar(value='0'),
            'nonpositive': tk.StringVar(value='0'),
            'event_offset_ms': tk.StringVar(value='20'),
            'event_drift_ppm': tk.StringVar(value='100'),
        }
        self.results = queue.Queue()
        self.specs = []
        self.current_streams = []
        self.report = None
        self.busy = False
        self._build()
        self.after(150, self._poll)

    def _build(self):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
        heading = tk.Frame(self, bg='#1c2a40'); heading.pack(fill='x', pady=(0,8))
        tk.Label(heading,text='POST-EXPERIMENT ANALYSIS',bg='#1c2a40',fg='white',font=('Segoe UI',16,'bold')).pack(side='left')
        self._button(heading,'Use last session', self.use_last).pack(side='right')
        top = tk.Frame(self,bg='#1c2a40');top.pack(fill='x',pady=4)
        tk.Label(top,text='Session',bg='#1c2a40',fg='white').pack(side='left')
        ttk.Entry(top,textvariable=self.folder).pack(side='left',fill='x',expand=True,padx=6)
        self._button(top,'Browse',self.browse).pack(side='left')
        self._button(top,'Scan',self.scan).pack(side='left',padx=4)
        second=tk.Frame(self,bg='#1c2a40');second.pack(fill='x',pady=4)
        tk.Label(second,text='Physical event anchors (optional)',bg='#1c2a40',fg='white').pack(side='left')
        ttk.Entry(second,textvariable=self.events).pack(side='left',fill='x',expand=True,padx=6)
        self._button(second,'Browse CSV',self.browse_events).pack(side='left')
        pan=ttk.PanedWindow(self,orient='horizontal');pan.pack(fill='both',expand=True,pady=7)
        left=ttk.Frame(pan);right=ttk.Frame(pan);pan.add(left,weight=1);pan.add(right,weight=3)
        ttk.Label(left,text='Select streams and data channels').pack(anchor='w',pady=5)
        self.scroll=tk.Canvas(left,highlightthickness=0,bg='#18263b',width=260)
        sb=ttk.Scrollbar(left,orient='vertical',command=self.scroll.yview);sb.pack(side='right',fill='y')
        self.scroll.configure(yscrollcommand=sb.set)
        self.scroll.pack(fill='both',expand=True)
        self.stream_area=ttk.Frame(self.scroll)
        self.scroll.create_window((5,5),window=self.stream_area,anchor='nw')
        self.stream_area.bind('<Configure>',lambda e:self.scroll.configure(scrollregion=self.scroll.bbox('all')))
        thresholds=ttk.LabelFrame(right,text='User-defined warning thresholds',padding=5)
        thresholds.pack(fill='x',pady=3)
        items=[('start_ms','Late start (ms)'),('stop_ms','Early stop (ms)'),('jitter_ms','Jitter MAD (ms)'),('gaps','Large gaps'),('nonpositive','Nonpositive intervals'),('event_offset_ms','Physical-event offset (ms)'),('event_drift_ppm','Physical-event drift (ppm)')]
        for i,(key,label) in enumerate(items):
            cell=ttk.Frame(thresholds);cell.grid(row=i//4,column=i%4,sticky='ew',padx=5,pady=3)
            ttk.Label(cell,text=label).pack(anchor='w');ttk.Entry(cell,textvariable=self.thresholds[key],width=13).pack(anchor='w')
        for i in range(4):thresholds.columnconfigure(i,weight=1)
        bar=ttk.Frame(right);bar.pack(fill='x',pady=4)
        self.run_btn=ttk.Button(bar,text='Analyze selected streams',command=self.run_analysis)
        self.run_btn.pack(side='left')
        self.status=tk.StringVar(value='Choose a completed experiment session.')
        ttk.Label(bar,textvariable=self.status).pack(side='left',padx=8)
        self.summary=ttk.Treeview(right,columns=('status','metric','detail'),show='headings',height=6)
        for key,width in [('status',85),('metric',165),('detail',480)]:
            self.summary.heading(key,text=key.title());self.summary.column(key,width=width,anchor='w')
        self.summary.pack(fill='x',pady=4)
        self.fig=Figure(figsize=(7.3,4.2),dpi=90,facecolor='#fff')
        self.ax=self.fig.add_subplot(111)
        self.canvas=FigureCanvasTkAgg(self.fig,master=right)
        self.canvas.get_tk_widget().pack(fill='both',expand=True)
        toolbar=NavigationToolbar2Tk(self.canvas,right,pack_toolbar=False);toolbar.update();toolbar.pack(fill='x')
        self.view=tk.StringVar(value='Coverage')
        self.plot_selector=ttk.Combobox(right,textvariable=self.view,values=['Coverage','Inter-sample intervals','Jitter distribution','Large gaps timeline','Nominal-rate drift','Reception lag','Pairwise overlap','Camera timing','Event offsets'],state='readonly',width=26)
        self.plot_selector.pack(anchor='e')
        self.view.trace_add('write',lambda *_:self.plot())
        ttk.Label(self,text='First/last timestamp differences measure coverage, not true clock offset. Camera timestamps are frame retrieval times, not exposure times.').pack(anchor='w')

    def _button(self,parent,label,cmd):return ttk.Button(parent,text=label,command=cmd)
    def browse(self):
        p=filedialog.askdirectory(title='Select completed session')
        if p:self.folder.set(p);self.scan()
    def browse_events(self):
        p=filedialog.askopenfilename(filetypes=[('CSV','*.csv')]);
        if p:self.events.set(p)
    def use_last(self):
        # session.folder is populated by the recorder after session start
        candidate=getattr(self.session,'folder',None) or getattr(self.session,'session_folder',None)
        if candidate:self.folder.set(str(candidate));self.scan()
        else:messagebox.showinfo('No previous session','Browse to an existing Participant / Session folder.')
    def scan(self):
        try:self.specs=find_streams(self.folder.get())
        except Exception as exc:messagebox.showerror('Session scan failed',str(exc));return
        for w in self.stream_area.winfo_children():w.destroy()
        self.selected={};self.channel_vars={};self.rate_vars={}
        for spec in self.specs:
            n=spec['name'];v=tk.BooleanVar(value=True);self.selected[n]=v
            group=ttk.LabelFrame(self.stream_area,text=n,padding=5);group.pack(fill='x',padx=4,pady=5)
            ttk.Checkbutton(group,text=f"Include {spec['kind']}",variable=v).pack(anchor='w')
            rv=tk.StringVar();self.rate_vars[n]=rv
            ttk.Label(group,text='Override nominal Hz (optional)').pack(anchor='w')
            ttk.Entry(group,textvariable=rv,width=16).pack(anchor='w')
            if spec['kind'] in ('lsl','sample'):
                self.channel_vars[n]={}
                for col in spec['columns']:
                    if col in ('lsl_timestamp','lsl_time','lsl_estimate','local_receive_clock','device_timestamp'):continue
                    ch=tk.BooleanVar(value=False);self.channel_vars[n][col]=ch
                    ttk.Checkbutton(group,text=col,variable=ch).pack(anchor='w')
        self.status.set(f'{len(self.specs)} timestamp files found')

    def _limits(self):
        limits={k:float(v.get()) for k,v in self.thresholds.items()}
        if any(x<0 for x in limits.values()):raise ValueError('Thresholds must be nonnegative.')
        return limits

    def run_analysis(self):
        if self.busy:return
        # Never run on a session being written to
        if getattr(self.session,'active',False):
            messagebox.showwarning('Session in progress','Stop recording before starting post-experiment analysis.');return
        names=[n for n,v in self.selected.items() if v.get()]
        if not names:messagebox.showwarning('No streams','Select at least one stream.');return
        try:
            limits=self._limits()
            rates={n:float(v.get()) for n,v in self.rate_vars.items() if n in names and v.get().strip()}
            if any(x<=0 for x in rates.values()):raise ValueError('Nominal rates must be positive')
        except ValueError as exc:messagebox.showerror('Invalid settings',str(exc));return
        channels={n:[col for col,var in self.channel_vars.get(n,{}).items() if var.get()] for n in names}
        folder=self.folder.get();events=self.events.get().strip() or None
        out=Path(folder)/'analysis_reports'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        self.busy=True;self.run_btn.configure(state='disabled');self.status.set('Running QA…')
        def worker():
            try:
                report,streams=analyze(folder,names,channels,rates,events)
                findings=self._evaluate(report,limits)
                report['quality_thresholds']=limits
                report['quality_findings']=findings
                save_report(report,streams,out)
                self.results.put(('ok',(report,streams,findings,out)))
            except Exception as exc:self.results.put(('error',repr(exc)))
        threading.Thread(target=worker,daemon=True).start()

    @staticmethod
    def _evaluate(report,lim):
        findings=[]
        def add(level,stream,metric,detail):findings.append(dict(level=level,stream=stream,metric=metric,detail=detail))
        if not report['reference']['start_from_marker']:add('CAUTION','Session','Start marker','No start marker; earliest selected sample used.')
        if not report['reference']['end_from_marker']:add('CAUTION','Session','Stop marker','No stop marker; latest selected sample used.')
        for row in report['alignment']:
            n=row['stream'];a=row.get('first_vs_session_start_ms');b=row.get('last_vs_session_stop_ms')
            if a is not None and a>lim['start_ms']:add('WARNING',n,'Late start',f'{a:.1f} ms after start marker')
            if b is not None and b< -lim['stop_ms']:add('WARNING',n,'Early stop',f'{-b:.1f} ms before stop marker')
        for row in report['streams']:
            n=row['stream']
            for field,key,title in [('interval_jitter_mad_ms','jitter_ms','Timestamp jitter MAD'),('large_gap_count','gaps','Large timestamp gaps'),('nonpositive_intervals','nonpositive','Nonpositive intervals')]:
                v=row.get(field)
                if v is not None and v>lim[key]:add('WARNING',n,title,f'{v} exceeds {lim[key]}')
            for warning in row.get('warnings',[]):add('CAUTION',n,'Data qualification',warning)
        for row in report['event_offsets']:
            n=f"{row['stream_a']} / {row['stream_b']}"
            v=row.get('median_offset_ms')
            if v is not None and abs(v)>lim['event_offset_ms']:add('WARNING',n,'Physical-event offset',f'{v} ms')
            v=row.get('relative_event_drift_ppm')
            if v is not None and abs(v)>lim['event_drift_ppm']:add('WARNING',n,'Physical-event drift',f'{v} ppm')
        if not report['event_offsets']:add('CAUTION','Session','Uncalibrated offset','No matched physical events; true cross-device offset/drift not measurable.')
        if not findings:add('PASS','Session','Timing thresholds','All evaluated metrics within chosen limits.')
        return findings

    def _poll(self):
        try:
            while True:
                typ,payload=self.results.get_nowait();self.busy=False;self.run_btn.configure(state='normal')
                if typ=='error':self.status.set('Analysis failed');messagebox.showerror('Analysis failed',payload)
                else:
                    self.report,self.current_streams,findings,out=payload
                    self.status.set(f"{sum(f['level']=='WARNING' for f in findings)} warning(s) • saved {out}")
                    for key in self.summary.get_children():self.summary.delete(key)
                    for row in findings:self.summary.insert('', 'end',values=(row['level'],row['stream']+' / '+row['metric'],row['detail']))
                    self.plot()
                    if self.bus:
                        try:self.bus.emit('Post-analysis','INFO',f'QA report saved: {out}')
                        except Exception:pass
        except queue.Empty:pass
        self.after(150,self._poll)

    def plot(self):
        """Offline exploratory plots. Never mutate recorded samples."""
        if not self.report:
            return
        import numpy as np
        ax = self.ax
        ax.clear()
        mode = self.view.get()
        streams = [s for s in self.current_streams if len(s.times)]
        stats = {r['stream']: r for r in self.report['streams']}
        colors = ax._get_lines  # rely on matplotlib's default series palette

        def label_empty(message):
            ax.text(.5, .5, message, transform=ax.transAxes,
                    ha='center', va='center', wrap=True)

        def stride(a, maximum=3000):
            return a[::max(1, int(np.ceil(len(a)/maximum)))]

        if mode == 'Coverage':
            ref = self.report['reference']['reference_start']
            rows = self.report['alignment']
            for i, row in enumerate(rows):
                ax.barh(i, row['end_lsl']-row['start_lsl'],
                        left=row['start_lsl']-ref)
            ax.set_yticks(range(len(rows)), [r['stream'] for r in rows])
            ax.invert_yaxis()
            ax.set_xlabel('Seconds from session reference (coverage, not offset)')

        elif mode == 'Inter-sample intervals':
            for stream in streams:
                if len(stream.times) < 2: continue
                delta = np.diff(stream.times)
                x = stream.times[1:] - stream.times[0]
                ax.plot(stride(x), stride(delta * 1000), label=stream.name, lw=.8)
            ax.set_xlabel('Seconds since first timestamp')
            ax.set_ylabel('Adjacent timestamp interval (ms)')

        elif mode == 'Jitter distribution':
            plotted = False
            for stream in streams:
                dt = np.diff(stream.times)
                if stream.nominal_hz and stream.nominal_hz > 0:
                    if stream.indices is not None and len(stream.indices) == len(stream.times):
                        dx = np.diff(stream.indices)
                        mask = (dt > 0) & (dx > 0)
                        residual = dt[mask] - dx[mask]/stream.nominal_hz
                    else:
                        residual = dt[dt > 0] - 1/stream.nominal_hz
                else:
                    positive = dt[dt > 0]
                    residual = positive - np.median(positive) if len(positive) else []
                residual = np.asarray(residual, dtype=float)*1000
                if not len(residual): continue
                # clip plotted extremes only; preserve raw data and statistical metrics
                lo, hi = np.percentile(residual, [1, 99]) if len(residual)>20 else (residual.min(),residual.max())
                clipped = np.clip(residual,lo,hi)
                ax.hist(clipped, bins=35, density=True, alpha=.38, label=stream.name)
                plotted = True
            if not plotted: label_empty('No intervals available')
            ax.set_xlabel('Interval residual (ms; plotting extremes clipped to 1–99%)')
            ax.set_ylabel('Density')

        elif mode == 'Large gaps timeline':
            found = False
            for i, stream in enumerate(streams):
                dt = np.diff(stream.times)
                if not len(dt): continue
                if stream.nominal_hz and stream.nominal_hz > 0:
                    if stream.indices is not None and len(stream.indices)==len(stream.times):
                        expected=np.diff(stream.indices)/stream.nominal_hz
                    else: expected=np.full(len(dt),1/stream.nominal_hz)
                else:
                    valid=dt[dt>0]
                    expected=np.full(len(dt),np.median(valid) if len(valid) else np.inf)
                idx=np.where((dt>1.5*expected)&(expected>0))[0]
                if len(idx):
                    ax.scatter(stream.times[idx+1]-self.report['reference']['reference_start'],
                               np.full(len(idx),i), s=16, label=stream.name)
                    found=True
            ax.set_yticks(range(len(streams)),[s.name for s in streams])
            ax.set_xlabel('Seconds from session reference (suspected cadence gaps)')
            if not found: label_empty('No cadence-based large gaps identified')

        elif mode == 'Nominal-rate drift':
            rows=[(name, m['nominal_rate_drift_ppm']) for name,m in stats.items()
                  if m.get('nominal_rate_drift_ppm') is not None]
            if rows:
                ax.barh([r[0] for r in rows],[r[1] for r in rows])
                ax.set_xlabel('Sample-index vs timestamp slope deviation (ppm)')
            else: label_empty('No reliable nominal-rate basis available')

        elif mode == 'Reception lag':
            plotted=False
            for stream in streams:
                if stream.reception is None or len(stream.reception)!=len(stream.times): continue
                lag=(stream.reception-stream.times)*1000
                valid=np.isfinite(lag)
                if valid.any():
                    x=stream.times[valid]-stream.times[0]
                    ax.plot(stride(x),stride(lag[valid]),label=stream.name,lw=.85)
                    plotted=True
            ax.set_xlabel('Seconds since stream start')
            ax.set_ylabel('Receive clock − sample timestamp (ms)')
            if not plotted: label_empty('No paired receive/source timestamps available')

        elif mode == 'Pairwise overlap':
            rows=self.report.get('coverage_pairs',[])
            if rows:
                labels=[r['stream_a']+' / '+r['stream_b'] for r in rows]
                ax.barh(labels,[r['overlap_s'] for r in rows])
                ax.set_xlabel('Shared coverage (seconds; not synchronization accuracy)')
                ax.invert_yaxis()
            else: label_empty('Select at least two nonempty streams')

        elif mode == 'Camera timing':
            rows=[r for r in self.report['streams'] if r.get('camera_nominal_playback_duration_s') is not None]
            if rows:
                x=np.arange(len(rows));w=.37
                ax.bar(x-w/2,[r['camera_timestamp_span_s'] for r in rows],w,label='Timestamp span')
                ax.bar(x+w/2,[r['camera_nominal_playback_duration_s'] for r in rows],w,label='Nominal MP4 duration')
                ax.set_xticks(x,[r['stream'] for r in rows]);ax.set_ylabel('Duration (seconds)')
            else: label_empty('No camera frame timestamp data selected')

        elif mode == 'Event offsets':
            rows=self.report.get('event_offsets',[])
            if rows:
                labels=[r['stream_a']+' / '+r['stream_b'] for r in rows]
                vals=[r['median_offset_ms'] for r in rows]
                ax.barh(labels,vals);ax.set_xlabel('Matched physical event median offset (ms)')
            else: label_empty('No matched physical-event anchors supplied')

        if len(ax.lines) or (mode in ('Jitter distribution','Camera timing') and ax.patches):
            handles,labels=ax.get_legend_handles_labels()
            if handles and len(handles)<=12: ax.legend(fontsize=8,loc='best')
        ax.set_title(mode)
        ax.grid(True,alpha=.2)
        self.fig.tight_layout()
        self.canvas.draw_idle()
