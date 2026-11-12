"""Expressive, scientifically cautious figures for IMMSS quality-report summaries.

This module intentionally consumes reviewer-exported summary metrics. It does not
infer sample loss or inter-device offset/drift from timestamp-gap counts.
"""
from __future__ import annotations

import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm, LogNorm
from matplotlib.ticker import MaxNLocator
import numpy as np
import pandas as pd
import plotly.graph_objects as go

NAVY = '#17324D'
MUTED = '#61758A'
BLUE = '#2879AA'
CORAL = '#CB6549'
TEAL = '#16877B'
AMBER = '#D19B3D'
PAPER = '#FFFFFF'
SOFT = '#EDF3F7'
PERIODIC = ['Eye Tracking', 'Eye Debug', 'Camera 1', 'Camera 2', 'Camera 3',
            'Polar ECG', 'Polar ACC', 'Microphone']


def _label(row):
    return f"{row['group']} · {row['session']}"


def _mat(streams, overview, metric):
    keys = overview.session_id.tolist()
    df = pd.DataFrame(index=PERIODIC, columns=keys, dtype=float)
    filtered = streams[streams.device.isin(PERIODIC)]
    for (session, device), rows in filtered.groupby(['session_id', 'device']):
        if session in df.columns:
            vals = pd.to_numeric(rows[metric], errors='coerce').dropna()
            if len(vals):
                df.loc[device, session] = float(vals.median())
    return df


def _style(ax):
    ax.set_facecolor(PAPER)
    ax.spines[['top','right']].set_visible(False)
    ax.spines[['left','bottom']].set_color('#BDCBD5')
    ax.tick_params(axis='both', colors=NAVY, labelsize=9, length=0, pad=7)
    ax.grid(axis='x', color='#DCE6ED', alpha=.8, linewidth=.7)
    ax.set_axisbelow(True)


def _save(fig, output, filename, *, svg=True):
    try:
        fig.savefig(Path(output)/f'{filename}.png', dpi=165, bbox_inches='tight', facecolor=PAPER)
        if svg:
            fig.savefig(Path(output)/f'{filename}.svg', bbox_inches='tight', facecolor=PAPER)
    finally:
        plt.close(fig)


def _heatmap(ax, arr, *, cmap, vmin, vmax, rows, columns, annotation, colorbar_label, center=None):
    a=np.asarray(arr,dtype=float)
    colormap=plt.get_cmap(cmap).copy()
    colormap.set_bad(SOFT)
    normalizer=TwoSlopeNorm(vmin=vmin, vcenter=center, vmax=vmax) if center is not None else None
    im=ax.imshow(np.ma.masked_invalid(a), aspect='auto', cmap=colormap,
                 norm=normalizer, vmin=None if normalizer else vmin,
                 vmax=None if normalizer else vmax)
    ax.set_xticks(np.arange(len(columns)), columns, rotation=32, ha='right', fontsize=9)
    ax.set_yticks(np.arange(len(rows)),rows, fontsize=9)
    ax.tick_params(length=0, colors=NAVY)
    for spine in ax.spines.values(): spine.set_visible(False)
    for r in range(a.shape[0]):
        for c in range(a.shape[1]):
            v=a[r,c]
            msg=annotation(v) if np.isfinite(v) else '—'
            color='white' if np.isfinite(v) and ((center is not None and abs(v)>.67*vmax) or (center is None and v>vmin+.75*(vmax-vmin))) else NAVY
            ax.text(c,r,msg,ha='center',va='center',fontsize=8,color=color,weight='semibold')
    cb=ax.figure.colorbar(im,ax=ax,fraction=.025,pad=.02)
    cb.set_label(colorbar_label,color=MUTED,fontsize=9)
    cb.ax.tick_params(labelsize=8,colors=MUTED)


def make_expressive_static(output, overview, streams, gaps, *, page_size=12, svg=True, verbose=False):
    """Create bounded-size readable figures without re-reading raw XDF files."""
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    if overview.empty: return []
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.titlesize':12,
                         'axes.labelsize':10,'svg.fonttype':'none'})
    output_names=[]
    for first in range(0,len(overview),page_size):
        ov=overview.iloc[first:first+page_size].copy()
        ss=streams[streams.session_id.isin(ov.session_id)]
        keys=ov.session_id.tolist(); labels=[_label(r) for _,r in ov.iterrows()]
        suffix='' if len(overview)<=page_size else f'_page_{first//page_size+1:02d}'
        width=max(9.5, min(16, 1.35*len(ov)+4.8))

        # Signed deviation explicitly distinguishes slower than expected from faster.
        signed=_mat(ss,ov,'rate_error_pct').to_numpy(dtype=float)
        fig,ax=plt.subplots(figsize=(width,5.5),layout='constrained')
        cap=max(21, min(40,math.ceil(np.nanmax(np.abs(signed))/5)*5)) if np.isfinite(signed).any() else 25
        _heatmap(ax,signed,cmap='RdBu_r',vmin=-cap,vmax=cap,center=0,rows=PERIODIC,columns=labels,
                 annotation=lambda v:f'{v:+.1f}%',colorbar_label='Signed rate deviation (%)')
        ax.set_title('Sampling-rate deviation  |  blue = slower · red = faster',loc='left',weight='bold',color=NAVY,pad=16)
        name=f'09_signed_rate_deviation{suffix}'; _save(fig,output,name,svg=svg);output_names.append(name)

        # Compact scorecard with three independent metrics (no composite score).
        fig,axes=plt.subplots(1,3,figsize=(max(11,width),4.3),layout='constrained')
        metrics=[('common_coverage_pct','Common XDF overlap','% of XDF time span',TEAL),
                 ('rate_compliance_pct','Rate agreement','% of evaluated streams',BLUE),
                 ('streams_flagged','Streams with diagnostic flags','No. of periodic streams',CORAL)]
        yy=np.arange(len(ov))[::-1]
        for ax,(metric,title,unit,col) in zip(axes,metrics):
            _style(ax)
            nums=pd.to_numeric(ov[metric],errors='coerce').to_numpy(float)
            if 'pct' in metric:
                ax.set_xlim(0,107);ax.axvspan(95,100,color=SOFT,alpha=.6,zorder=0)
            else:
                ax.set_xlim(0,max(1,np.nanmax(nums)+1) if np.isfinite(nums).any() else 1)
            for idx,(y,val) in enumerate(zip(yy,nums)):
                if np.isfinite(val):
                    ax.hlines(y,0,val,color=col,linewidth=4,alpha=.6)
                    ax.scatter([val],[y],color=col,s=36,zorder=3)
                    ax.annotate(f'{val:.1f}%' if 'pct' in metric else f'{int(val)}',(val,y),xytext=(5,2),textcoords='offset points',fontsize=8,color=NAVY)
                else:
                    ax.text(.04,y,'N/A',fontsize=8,color=MUTED)
            ax.set_yticks(yy,labels if ax is axes[0] else ['']*len(yy))
            ax.set_ylim(-.5,len(yy)-.5)
            ax.set_title(title,loc='left',color=NAVY,weight='bold')
            ax.set_xlabel(unit,color=MUTED)
        fig.suptitle('Session quality at a glance  |  independent metrics',color=NAVY,weight='bold',fontsize=14)
        name=f'10_session_scorecard{suffix}'; _save(fig,output,name,svg=svg);output_names.append(name)

        # Normalized jitter alongside gap rates, audio separated so it cannot flatten other rows.
        jitter=_mat(ss,ov,'jitter_normalized_pct').to_numpy(float)
        gapsmin=_mat(ss,ov,'gaps_per_min').to_numpy(float)
        fig,axs=plt.subplots(1,2,figsize=(max(12,width+1.5),5.8),layout='constrained')
        jcap=max(10,min(60,np.nanpercentile(jitter[np.isfinite(jitter)],95)*1.2)) if np.isfinite(jitter).any() else 10
        _heatmap(axs[0],jitter,cmap='YlGnBu',vmin=0,vmax=jcap,rows=PERIODIC,columns=labels,
                 annotation=lambda v:f'{v:.1f}%',colorbar_label='% of expected interval')
        axs[0].set_title('Timing variation  |  normalized MAD',color=NAVY,weight='bold',loc='left',pad=16)
        # Log is ONLY a color transform. Labels are actual intervals/min.
        encoded=np.log1p(np.clip(gapsmin,0,None))
        max_log=max(1,np.nanmax(encoded)) if np.isfinite(encoded).any() else 1
        _heatmap(axs[1],encoded,cmap='YlOrRd',vmin=0,vmax=max_log,rows=PERIODIC,columns=labels,
                 annotation=lambda v:'N/A' if not np.isfinite(v) else f'{np.expm1(v):.1f}',
                 colorbar_label='ln(1 + gaps/min) — color only')
        axs[1].set_title('Long intervals  |  gaps per minute',color=NAVY,weight='bold',loc='left',pad=16)
        fig.suptitle('Acquisition regularity by device  |  no inferred packet loss',color=NAVY,weight='bold',fontsize=14)
        name=f'11_timing_atlas{suffix}'; _save(fig,output,name,svg=svg);output_names.append(name)

    # Gap-event map: marker x uses time relative to each recording's XDF start.
    if gaps is not None and not gaps.empty:
        df=gaps.copy()
        df=df[np.isfinite(pd.to_numeric(df.session_relative_s,errors='coerce')) &
              np.isfinite(pd.to_numeric(df.duration_ms,errors='coerce'))]
        if len(df):
            if len(df)>1800:
                most=df.nlargest(900,'duration_ms')
                rest=df.drop(most.index)
                sample=rest.iloc[np.linspace(0,len(rest)-1,min(900,len(rest)),dtype=int)]
                df=pd.concat([most,sample])
            df['label']=df['session'].astype(str)+' · '+df['device'].astype(str)
            groups=sorted(df['label'].unique())
            if len(groups)>45: groups=groups[:45]
            df=df[df.label.isin(groups)]
            ypos={key:i for i,key in enumerate(groups)}
            fig,ax=plt.subplots(figsize=(11,max(4.6,min(11,len(groups)*.30+.8))),layout='constrained')
            _style(ax)
            y=df.label.map(ypos).to_numpy(float)
            size=np.clip(9+7*np.log1p(df.duration_ms.to_numpy(float)),10,85)
            ax.scatter(df.session_relative_s, y,s=size,c=CORAL,alpha=.42,edgecolors='none',rasterized=True)
            ax.set_yticks(range(len(groups)),groups,fontsize=8)
            ax.invert_yaxis()
            ax.set_xlabel('Seconds from this recording’s corrected XDF start',color=MUTED)
            ax.set_title('Reported long-interval events  |  marker size represents duration',loc='left',color=NAVY,weight='bold')
            name='12_reported_gap_events';_save(fig,output,name,svg=svg);output_names.append(name)
    return output_names


def make_expressive_interactive(overview,streams,gaps):
    """Additional interactive Plotly charts with rate direction and corrected time axes."""
    if overview.empty:return []
    keys=overview.session_id.tolist(); labels=[_label(r) for _,r in overview.iterrows()]
    charts=[]

    signed=_mat(streams,overview,'rate_error_pct').to_numpy(float)
    cap=max(21,min(40,math.ceil(np.nanmax(np.abs(signed))/5)*5)) if np.isfinite(signed).any() else 25
    fig=go.Figure(go.Heatmap(x=labels,y=PERIODIC,z=signed,zmin=-cap,zmax=cap,zmid=0,
        colorscale=[[0,'#2879AA'],[.5,'#F6F9FB'],[1,'#CB6549']],
        text=[[f'{v:+.1f}%' if np.isfinite(v) else '—' for v in row] for row in signed],
        texttemplate='%{text}',
        hovertemplate='<b>%{y}</b><br>%{x}<br>Observed rate deviation: %{z:+.2f}%<extra></extra>',
        colorbar=dict(title='Slower ← % → Faster')))
    fig.update_layout(title='Rate deviations: slower versus faster than reference',height=530,margin=dict(l=25,r=25,t=65,b=100),
                      paper_bgcolor='white',plot_bgcolor='white',font=dict(family='Arial',color=NAVY))
    fig.update_yaxes(autorange='reversed')
    charts.append(fig)

    # Chart preserves units explicitly. A scatter lets the operator explore rate vs gaps
    # without treating these quantities as a scientific composite score.
    ss=streams[streams.device.isin(PERIODIC)].copy()
    ss=ss[np.isfinite(ss.abs_rate_error_pct)&np.isfinite(ss.gaps_per_min)]
    fig=go.Figure()
    for device,block in ss.groupby('device',sort=False):
        fig.add_trace(go.Scatter(x=block.abs_rate_error_pct,y=block.gaps_per_min,mode='markers',name=device,
            marker=dict(size=11,opacity=.78,line=dict(width=1,color='white')),
            text=block['group'].astype(str)+' · '+block['session'].astype(str),
            customdata=block[['jitter_mad_ms','largest_gap_ms','gaps_per_min']].to_numpy(),
            hovertemplate='<b>%{fullData.name}</b><br>%{text}<br>Absolute rate deviation %{x:.2f}%'
                          '<br>Long intervals %{customdata[2]:.2f}/min<br>Jitter MAD %{customdata[0]:.3f} ms'
                          '<br>Longest gap %{customdata[1]:.1f} ms<extra></extra>'))
    fig.update_layout(title='Rate agreement versus long intervals (different diagnostics)',
                      xaxis_title='Absolute rate deviation (%)',yaxis_title='Long intervals / min (log scale)',
                      yaxis_type='log',height=510,margin=dict(l=25,r=25,t=65,b=65),
                      legend=dict(orientation='h',y=-.25),paper_bgcolor='white',plot_bgcolor='white',
                      font=dict(family='Arial',color=NAVY))
    # Plotly log axes cannot show zero; move exact zeros to tiny positive display only
    for trace in fig.data:
        trace.y=[max(float(v),.02) for v in trace.y]
    fig.add_annotation(x=.99,y=.99,xref='paper',yref='paper',text='0 gaps/min displayed at 0.02 for visibility',showarrow=False,
                       font=dict(size=10,color=MUTED),xanchor='right')
    charts.append(fig)

    # Normalized jitter heatmap for comparing different sampling frequencies.
    j=_mat(streams,overview,'jitter_normalized_pct').to_numpy(float)
    fig=go.Figure(go.Heatmap(x=labels,y=PERIODIC,z=j,colorscale='YlGnBu',
        text=[[f'{v:.1f}%' if np.isfinite(v) else '—' for v in row] for row in j],texttemplate='%{text}',
        hovertemplate='<b>%{y}</b><br>%{x}<br>Interval MAD / reference period %{z:.3f}%<extra></extra>',
        colorbar=dict(title='% of reference period')))
    fig.update_layout(title='Normalized timestamp jitter by sensor',height=500,margin=dict(l=25,r=25,t=65,b=90),
                      paper_bgcolor='white',plot_bgcolor='white',font=dict(family='Arial',color=NAVY))
    fig.update_yaxes(autorange='reversed')
    charts.append(fig)
    return charts
