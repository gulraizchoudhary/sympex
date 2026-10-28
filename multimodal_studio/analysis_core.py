"""Offline synchronization QA for Multimodal Studio session files.
No claim of physical inter-device synchronization without common-event anchors.
"""
from __future__ import annotations
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd

@dataclass
class Stream:
    name: str
    source: Path
    kind: str
    times: np.ndarray
    indices: np.ndarray | None = None
    nominal_hz: float | None = None
    reception: np.ndarray | None = None
    values: dict | None = None
    warnings: tuple = ()


def _num(df, col):
    return pd.to_numeric(df[col], errors='coerce').to_numpy(dtype=float)


def _find_timestamp_column(df):
    preferred = ['lsl_timestamp','lsl_time','lsl_estimate','lsl_capture_time_estimate','lsl_estimated_first_sample']
    return next((x for x in preferred if x in df.columns), None)


def find_streams(folder):
    folder=Path(folder)
    if not folder.is_dir(): raise FileNotFoundError(folder)
    streams=[]
    for p in sorted(folder.glob('*.csv')):
        if p.name in ('markers.csv','sync_events.csv') or p.name.endswith('_offsets.csv'):
            continue
        try:
            cols=list(pd.read_csv(p,nrows=0).columns)
        except Exception:
            continue
        if {'file_sample_index','frames','lsl_estimated_first_sample'}.issubset(cols): kind='audio'
        elif {'frame_index','lsl_capture_time_estimate'}.issubset(cols): kind='camera'
        elif 'lsl_timestamp' in cols and 'local_receive_clock' in cols: kind='lsl'
        elif _find_timestamp_column(pd.DataFrame(columns=cols)): kind='sample'
        else: continue
        streams.append({'name':p.stem,'filename':p.name,'kind':kind,'columns':cols})
    return streams


def load_stream(folder, spec, channels=None, nominal_hz=None):
    p=Path(folder)/spec['filename']
    df=pd.read_csv(p,low_memory=False)
    kind=spec['kind']
    warnings=[]
    if kind=='audio':
        start=_num(df,'lsl_estimated_first_sample')
        idx=_num(df,'file_sample_index')
        frames=_num(df,'frames')
        stem=p.name.removesuffix('_timestamps.csv')
        wav=p.with_name(stem+'.wav')
        if nominal_hz is None and wav.exists():
            import soundfile as sf
            nominal_hz=float(sf.info(wav).samplerate)
        if nominal_hz is None: warnings.append('Audio sample rate unknown; add it manually or provide WAV.')
        valid=np.isfinite(start)&np.isfinite(idx)&np.isfinite(frames)&(frames>0)
        start,idx,frames=start[valid],idx[valid],frames[valid]
        # Last sample included to avoid judging a single block as a single frame
        if len(start) and nominal_hz:
            times=np.ravel(np.column_stack([start,start+(frames-1)/nominal_hz]))
            indices=np.ravel(np.column_stack([idx,idx+frames-1]))
        else:
            times=start
            indices=idx
        if 'overflow' in df:
            if pd.to_numeric(df['overflow'],errors='coerce').fillna(0).gt(0).any():
                warnings.append('PortAudio overflow(s) reported; exact missing samples not reconstructable.')
        if len(idx)>1 and np.any(idx[1:]!=(idx[:-1]+frames[:-1])):
            warnings.append('Nonconsecutive saved sample indices detected.')
        if (not wav.exists()): warnings.append('WAV not found; duration/rate need independent confirmation.')
        else:
            import soundfile as sf
            count=sf.info(wav).frames
            if int(frames.sum())!=count: warnings.append(f'WAV length {count} samples differs from timestamp rows {int(frames.sum())}.')
        return Stream(spec['name'],p,kind,times,indices,nominal_hz,None,{'block_starts':start,'block_frames':frames,'block_indices':idx},tuple(warnings))
    tcol=_find_timestamp_column(df)
    if tcol is None: raise ValueError(f'No supported timestamp in {p}')
    t=_num(df,tcol)
    rec=_num(df,'local_receive_clock') if 'local_receive_clock' in df else None
    idxcol='frame_index' if kind=='camera' else None
    ix=_num(df,idxcol) if idxcol else None
    if kind=='camera':
        meta=p.with_name(p.stem.replace('_frames','')+'_metadata.json')
        if meta.exists():
            try:
                obj=json.loads(meta.read_text(encoding='utf-8'))
                if nominal_hz is None: nominal_hz=float(obj.get('writer_fps'))
            except (OSError,ValueError,TypeError): warnings.append('Could not read camera metadata.')
    if nominal_hz is None and kind=='sample':
        warnings.append('Sampling rate unspecified. Cadence metrics use observed median interval.')
    mask=np.isfinite(t)
    t=t[mask]
    if rec is not None: rec=rec[mask]
    if ix is not None: ix=ix[mask]
    values={}
    if channels:
        for col in channels:
            if col in df and col not in (tcol,'local_receive_clock'):
                values[col]=_num(df,col)[mask]
    return Stream(spec['name'],p,kind,t,ix,nominal_hz,rec,values,tuple(warnings))


def _f(x):
    return round(float(x),6) if np.isfinite(x) else None


def metrics(s):
    t=np.asarray(s.times,dtype=float)
    o={'stream':s.name,'kind':s.kind,'file':s.source.name,'points':len(t),'nominal_hz':s.nominal_hz,'warnings':list(s.warnings)}
    if not len(t):
        o['warnings'].append('No valid timestamps.'); return o
    o.update(start_lsl=_f(t[0]),end_lsl=_f(t[-1]),duration_s=_f(max(0,t[-1]-t[0])))
    dt=np.diff(t)
    if not len(dt): return o
    nonpositive=int(np.count_nonzero(dt<=0))
    o['nonpositive_intervals']=nonpositive
    positive=dt[dt>0]
    if not len(positive): return o
    # For audio use block boundaries but derive expected step from sample indices
    if s.indices is not None and s.nominal_hz and len(s.indices)==len(t):
        dx=np.diff(s.indices)
        keep=(dt>0)&(dx>0)
        intervals=dt[keep]
        expected=dx[keep]/s.nominal_hz
        nominal=np.median(expected) if len(expected) else None
        residual=intervals-expected
    else:
        nominal=(1/s.nominal_hz) if s.nominal_hz and s.nominal_hz>0 else np.median(positive)
        intervals=positive
        expected=np.full(len(intervals),nominal)
        residual=intervals-expected
    o['median_observed_interval_ms']=_f(np.median(positive)*1e3)
    o['interval_jitter_mad_ms']=_f(1.4826*np.median(np.abs(residual-np.median(residual)))*1e3) if len(residual) else None
    o['interval_jitter_p95_abs_ms']=_f(np.percentile(np.abs(residual),95)*1e3) if len(residual) else None
    o['max_observed_gap_s']=_f(np.max(positive))
    # A gap is a concern, not proof of dropped samples.
    if nominal and nominal>0:
        gap=(intervals>1.5*expected)&(expected>0)
        o['large_gap_count']=int(np.count_nonzero(gap))
        o['estimated_missing_intervals_lower_confidence']=int(np.sum(np.maximum(0,np.round(intervals[gap]/expected[gap]).astype(int)-1))) if np.any(gap) else 0
        o['gap_estimate_note']='Cadence-based estimate, not verified packet loss; device pauses and chunking can look identical.'
    if s.indices is not None and len(s.indices)==len(t):
        dx=np.diff(s.indices)
        o['counter_discontinuities']=int(np.count_nonzero(dx<=0))
        if s.kind=='camera':
            gaps=np.maximum(0,dx-1)
            o['missing_frame_indices']=int(gaps.sum())
            # CSV frame_index counts written frames, so zero gaps does not prove camera did not lose frames.
            o['warnings'].append('Camera frame_index counts saved frames; missed hardware frames can be invisible.')
        if s.kind=='audio':
            b=s.values
            if b is not None and len(b['block_indices'])>1:
                n=b['block_indices']; f=b['block_frames']
                o['saved_block_index_gaps']=int(np.count_nonzero(n[1:]!=n[:-1]+f[:-1]))
                o['warnings'].append('Audio block indices count saved samples, so dropped callbacks may be invisible without overflow flags.')
    # Sample index vs LSL clock drift estimates both capture clock and timestamping behavior
    if s.indices is not None and len(t)>=3 and s.nominal_hz:
        x=(s.indices-s.indices[0])/s.nominal_hz
    elif s.nominal_hz and len(t)>=3:
        x=np.arange(len(t))/s.nominal_hz
    else:
        x=None
    if x is not None and x[-1]>0 and np.all(np.diff(x)>=0):
        # Center x for stability, robust slope from median of non-overlapping sections
        slope=np.polyfit(x,t-t[0],1)[0]
        o['nominal_rate_drift_ppm']=_f((slope-1)*1e6)
        o['drift_note']='Sample-index versus timestamp slope, not inter-device clock drift.'
    if s.reception is not None and len(s.reception)==len(t):
        lag=(s.reception-t)
        lag=lag[np.isfinite(lag)]
        if len(lag):
            o['receive_minus_lsl_median_ms']=_f(np.median(lag)*1e3)
            o['receive_minus_lsl_p95_ms']=_f(np.percentile(lag,95)*1e3)
            o['receive_minus_lsl_jitter_mad_ms']=_f(1.4826*np.median(np.abs(lag-np.median(lag)))*1e3)
            o['warnings'].append('Reception lag is meaningful only when source and receiver timestamps share a corrected clock domain.')
    if s.kind=='camera' and s.nominal_hz and s.nominal_hz>0 and len(t)>1:
        # The camera writer may encode one frame per nominal 1/fps interval,
        # even if the physical acquisition cadence differs substantially.
        playback_duration=len(t)/s.nominal_hz
        captured_span=t[-1]-t[0]
        o['camera_nominal_playback_duration_s']=_f(playback_duration)
        o['camera_timestamp_span_s']=_f(captured_span)
        o['camera_playback_minus_capture_s']=_f(playback_duration-captured_span)
        if captured_span>0 and abs(playback_duration-captured_span)>max(.5,.02*captured_span):
            o['warnings'].append('Camera MP4 nominal playback duration differs from timestamp span; align using frame timestamp CSV, not MP4 playback time.')
    if nonpositive:o['warnings'].append('Timestamps have duplicates or run backwards.')
    return o


def load_markers(folder):
    p=Path(folder)/'markers.csv'
    if not p.exists(): return {}
    df=pd.read_csv(p)
    if not {'lsl_time','marker'}.issubset(df):return {}
    out={}
    for _,r in df.iterrows():
        try:out.setdefault(str(r['marker']),[]).append(float(r['lsl_time']))
        except (TypeError,ValueError):pass
    return out


def alignment(streams, folder):
    marker=load_markers(folder)
    all_t=[s.times for s in streams if len(s.times)]
    if not all_t:return [],{}
    start_marker=next((marker[k][0] for k in ('RECORDING_START','SESSION_START') if marker.get(k)), None)
    stop_marker=next((marker[k][-1] for k in ('RECORDING_STOP','SESSION_STOP','RECORDING_END') if marker.get(k)), None)
    reference_start=start_marker if start_marker is not None else min(t[0] for t in all_t)
    reference_end=stop_marker if stop_marker is not None else max(t[-1] for t in all_t)
    rows=[]
    for s in streams:
        if not len(s.times):continue
        rows.append({'stream':s.name,'first_vs_session_start_ms':_f((s.times[0]-reference_start)*1000),
          'last_vs_session_stop_ms':_f((s.times[-1]-reference_end)*1000),
          'start_lsl':_f(s.times[0]),'end_lsl':_f(s.times[-1]),
          'overlap_with_session_s':_f(max(0,min(s.times[-1],reference_end)-max(s.times[0],reference_start)))})
    return rows,{'reference_start':_f(reference_start),'reference_end':_f(reference_end),'start_from_marker':start_marker is not None,'end_from_marker':stop_marker is not None}


def pairwise_coverage(streams):
    rows=[]
    for i,a in enumerate(streams):
        if not len(a.times): continue
        for b in streams[i+1:]:
            if not len(b.times):continue
            rows.append({'stream_a':a.name,'stream_b':b.name,
                'first_sample_difference_ms':_f((a.times[0]-b.times[0])*1e3),
                'last_sample_difference_ms':_f((a.times[-1]-b.times[-1])*1e3),
                'overlap_s':_f(max(0,min(a.times[-1],b.times[-1])-max(a.times[0],b.times[0]))),
                'meaning':'Coverage difference only; NOT measured inter-stream synchronization offset.'})
    return rows


def event_anchor_offsets(path, selected_names):
    """Optional independently detected common physical events, CSV: stream,event,time_lsl.
    One event per label/stream, or ordered repeats identified with distinct labels.
    """
    if not path:return [],{}
    df=pd.read_csv(path)
    required={'stream','event','time_lsl'}
    if not required.issubset(df):raise ValueError(f'Event CSV needs columns {sorted(required)}')
    df=df[df['stream'].isin(selected_names)].copy()
    df['time_lsl']=pd.to_numeric(df['time_lsl'],errors='coerce')
    df=df.dropna(subset=['time_lsl'])
    if df.duplicated(['stream','event']).any():raise ValueError('Repeated event labels in the same stream: give each physical event a unique event ID.')
    pivot=df.pivot(index='event',columns='stream',values='time_lsl')
    streams=list(pivot.columns)
    results=[]
    for i,a in enumerate(streams):
        for b in streams[i+1:]:
            sub=pivot[[a,b]].dropna()
            if len(sub)==0:continue
            differences=(sub[a]-sub[b]).to_numpy()
            row={'stream_a':a,'stream_b':b,'matched_events':len(sub),'median_offset_ms':_f(np.median(differences)*1000),
                 'offset_jitter_mad_ms':_f(1.4826*np.median(np.abs(differences-np.median(differences)))*1000),
                 'p95_abs_offset_ms':_f(np.percentile(np.abs(differences),95)*1000)}
            if len(sub)>=3 and np.ptp(sub[b].to_numpy())>0:
                x=sub[b].to_numpy(); y=differences
                slope=np.polyfit(x-x[0],y,1)[0]
                row['relative_event_drift_ppm']=_f(slope*1e6)
                row['offset_at_first_event_ms']=_f((np.polyfit(x-x[0],y,1)[1])*1000)
            results.append(row)
    return results,{'anchor_events':len(pivot),'definition':'Differences are for matching physical events. Detection precision and latency must be assessed separately.'}


def analyze(folder, names, channels=None, rates=None, events=None):
    specs={s['name']:s for s in find_streams(folder)}
    selected=[load_stream(folder,specs[n],(channels or {}).get(n), (rates or {}).get(n)) for n in names]
    streams_stats=[metrics(s) for s in selected]
    rows,ref=alignment(selected,folder)
    pairs=pairwise_coverage(selected)
    anchors,anchor_meta=event_anchor_offsets(events,names)
    return {'session_path':str(Path(folder).resolve()),'streams':streams_stats,'alignment':rows,
      'reference':ref,'coverage_pairs':pairs,'event_offsets':anchors,'event_metadata':anchor_meta,
      'limitations':['First/last timestamp differences are NOT device latency or inter-device offset.',
      'Host timestamp of frame retrieval is NOT hardware exposure time.',
      'Nominal-rate drift estimates are not a substitute for common-event drift measurements.',
      'CSV LSL clock corrections must be applied before combining recordings from different computers.',
      'Without device sequence numbers or hardware counters, true sensor sample loss can be underestimated.']},selected


def save_report(report,streams,out):
    out=Path(out); out.mkdir(parents=True,exist_ok=True)
    (out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    for key in ('streams','alignment','coverage_pairs','event_offsets'):
        pd.DataFrame(report[key]).to_csv(out/(key+'.csv'),index=False)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(11,max(3,0.8*len(streams)+1)))
    if streams:
        origin=min((s.times[0] for s in streams if len(s.times)),default=0)
        for i,s in enumerate(streams):
            if not len(s.times):continue
            ax.barh(i,(s.times[-1]-s.times[0]),left=s.times[0]-origin,height=.45)
        ax.set_yticks(range(len(streams)),[s.name for s in streams]);ax.invert_yaxis()
    ax.set_xlabel('LSL seconds relative to earliest selected stream');ax.set_title('Recorded time coverage (not latency)')
    fig.tight_layout();fig.savefig(out/'coverage.png',dpi=150);plt.close(fig)
    fig,axs=plt.subplots(max(1,len(streams)),1,figsize=(11,max(3,2.4*len(streams))),squeeze=False)
    for i,s in enumerate(streams):
        ax=axs[i,0];t=s.times
        if len(t)>1:
            d=np.diff(t); x=t[1:]-t[0]
            step=max(1,len(d)//3000)
            ax.plot(x[::step],d[::step]*1000,lw=.65)
        ax.set_title(s.name);ax.set_ylabel('Interval (ms)');ax.set_xlabel('Time since first sample (s)')
    fig.tight_layout();fig.savefig(out/'intervals.png',dpi=150);plt.close(fig)
    with open(out/'summary.txt','w',encoding='utf-8') as f:
        f.write('Intelligent MicroSynch (IMS) synchronization QA\n\n')
        f.write('Start and end: coverage relative to session markers, not timing error.\n')
        f.write('Device-to-device offset requires matching physical events.\n\n')
        for m in report['streams']:
            f.write(f"{m['stream']}: {m.get('duration_s')} s, {m.get('points')} timestamp points, gaps {m.get('large_gap_count','n/a')}, jitter MAD {m.get('interval_jitter_mad_ms','n/a')} ms\n")
            for w in m['warnings']:f.write('   CAUTION: '+w+'\n')
        f.write('\n'+ '\n'.join(report['limitations'])+'\n')
    return out
