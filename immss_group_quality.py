"""IMMSS grouped quality-review analysis.

Read the *exported reviewer quality reports* (.json preferred, .csv fallback).
This is not raw XDF analysis and does not infer missing samples from timestamp gaps.

Usage:
  python immss_group_quality.py --root "C:/path/to/QualityReports"
  python immss_group_quality.py --root "C:/path/to/QualityReports" --output "C:/path/to/Results"

With no --root a folder selection dialog is opened, if Tkinter is available.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
import traceback
from collections import Counter
from pathlib import Path, PureWindowsPath
from typing import Any

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import plotly.graph_objects as go
import plotly.express as px
from plotly.offline import get_plotlyjs

VERSION = "1.3.0"


def status(message: str, enabled: bool = True):
    if enabled:
        print(f"[IMMSS] {message}", flush=True)

REGULAR = ["Eye Tracking", "Camera 1", "Camera 2", "Camera 3", "Polar ECG", "Polar ACC", "Microphone"]
EXTRA = ["Polar HR", "Polar RR", "Eye Events", "Experiment Markers"]
ALL_DEVICES = REGULAR + EXTRA
CLASS_LABELS = {"OK":"Within references", "METADATA":"Metadata mismatch", "TIMING":"Timestamp irregularity", "RATE":"Rate out of range", "MULTIPLE":"Multiple flags", "NO_DATA":"No data", "EVENT":"Event-based / sparse", "NOT_REPORTED":"Not reported"}
FLAG_ORDER = ["OK", "METADATA", "TIMING", "RATE", "MULTIPLE", "NO_DATA", "EVENT", "NOT_REPORTED"]
CLASS_COLORS = ["#d5ede2", "#faecb4", "#ffd5a8", "#f5b3a9", "#de91a9", "#c7c7c7", "#b9d6ee", "#ffffff"]


def canonical_group(parts):
    for p in parts:
        token = re.sub(r"[\s_-]+", "", str(p)).casefold()
        if token == "expert": return "Expert"
        if token in ("novice1", "beginner1"): return "Novice 1"
        if token in ("novice2", "beginner2"): return "Novice 2"
    return "Unassigned"


def canon(name, kind=""):
    n = str(name or "").lower()
    kind = str(kind or "").lower()
    if "camera1" in n or "camera_1" in n: return "Camera 1"
    if "camera2" in n or "camera_2" in n: return "Camera 2"
    if "camera3" in n or "camera_3" in n: return "Camera 3"
    if "ecg" in n or kind == "ecg": return "Polar ECG"
    if "polar" in n and "acc" in n or kind == "accelerometer": return "Polar ACC"
    if "polar" in n and "_hr" in n or kind == "heartrate": return "Polar HR"
    if "polar" in n and "_rr" in n or kind == "rrinterval": return "Polar RR"
    if "eyeevents" in n or kind == "events": return "Eye Events"
    if "debugdata" in n or kind == "vog_ext1": return "Eye Debug"
    if "eyedata" in n or kind == "vog": return "Eye Tracking"
    if "marker" in n or kind == "markers": return "Experiment Markers"
    if "audio" in n or "mic" in n or kind == "audio": return "Microphone"
    return re.sub(r"^ims_", "", str(name), flags=re.I).replace("_", " ")


def number(x, default=np.nan):
    try:
        if x is None or str(x).strip() == "": return default
        n = float(x)
        return n if math.isfinite(n) else default
    except (ValueError, TypeError):
        return default


def integer(x, default=0):
    n = number(x)
    return int(n) if np.isfinite(n) else default


def session_key(obj: dict, input_file: Path) -> str:
    p = obj.get("session_folder", "")
    if p:
        # JSON stores Windows paths even when analyzed on other OSes.
        q = PureWindowsPath(str(p)).name
        return str(q) or input_file.stem
    return input_file.stem


def regularity_flag(d: dict, threshold=5.0) -> tuple[str, list[str]]:
    """Evidence categories; never claim verified data loss from these metrics."""
    if d["device"] not in REGULAR:
        if d["samples"] == 0: return "NO_DATA", []
        return "EVENT", []
    if d["samples"] == 0: return "NO_DATA", ["no samples"]
    flags=[]
    rate_err=d["abs_rate_error_pct"]
    if np.isfinite(rate_err) and rate_err > threshold: flags.append("RATE")
    if d["nonpositive_intervals"] > 0 or d["large_gap_count"] > 0: flags.append("TIMING")
    if d["device"] == "Eye Tracking" and np.isfinite(d["declared_hz"]) and np.isfinite(d["expected_hz"]) and d["declared_hz"] > 0:
        if abs(d["declared_hz"]-d["expected_hz"])/d["expected_hz"] *100 > threshold:
            flags.append("METADATA")
    if len(flags) > 1: return "MULTIPLE", flags
    if flags: return flags[0], flags
    return "OK", []


def unpack_report(p: Path, root: Path) -> tuple[dict,list[dict],list[dict],list[dict]]:
    """Return session info, stream rows, scene links, camera media."""
    if p.suffix.lower()==".json":
        with p.open(encoding="utf-8") as f: obj=json.load(f)
        raw=obj.get("streams", [])
    else:
        obj={}
        with p.open(encoding="utf-8-sig",newline="") as f: raw=list(csv.DictReader(f))
    parts = p.relative_to(root).parts[:-1] if p.is_relative_to(root) else p.parent.parts
    group=canonical_group(parts)
    sid=session_key(obj,p)
    # Avoid collapsing reports from different directories that happen to have the same sid.
    localdir=str(p.parent.relative_to(root)) if p.is_relative_to(root) else str(p.parent)
    key=f"{group}/{localdir}/{sid}"  # unique session identifier
    scope=str(obj.get("scope","unknown"))
    ws=number(obj.get("window_start_s")); we=number(obj.get("window_end_s"))
    duration=number(obj.get("window_duration_s"))
    common=number(obj.get("common_coverage_s"))
    threshold=number(obj.get("thresholds",{}).get("rate_error_percent"),5.0)
    if not np.isfinite(threshold): threshold=5.0
    session={"session_id":key,"session":sid,"group":group,"source_file":str(p.relative_to(root)) if p.is_relative_to(root) else str(p.name),"scope":scope,
             "lsl_start_s":ws,"lsl_end_s":we,"duration_s":duration,"common_overlap_s":common,
             "common_coverage_pct":100*common/duration if np.isfinite(common) and np.isfinite(duration) and duration>0 else np.nan,
             "rate_tolerance_pct":threshold, "clock_reference":obj.get("clock_reference","not supplied"),
             "scenepics_linked":None, "scene_match_count":np.nan}
    scenes=[]
    for sc in obj.get("scene_pictures",[]) or []:
        scenes.append({"session_id":key,"group":group,"session":sid,"name":sc.get("name"),"linked":bool(sc.get("linked")),"matched_images":integer(sc.get("matched_images")),"image_count":integer(sc.get("image_count")),"details":sc.get("details","")})
    if scenes:
        session["scenepics_linked"]=all(s["linked"] for s in scenes)
        session["scene_match_count"]=sum(s["matched_images"] for s in scenes)
    media=[]
    for c in obj.get("camera_media",[]) or []:
        media.append({"session_id":key,"group":group,"session":sid,"device":c.get("name"),"mp4_frame_count":integer(c.get("mp4_frame_count")),
                      "xdf_frame_entries":integer(c.get("xdf_frame_entries")),"encoded_fps_metadata":number(c.get("encoded_fps_metadata")),
                      "mapping":str(c.get("mapping","")),"warnings":" | ".join(c.get("warnings",[]) or [])})
    streams=[]
    for r in raw:
        device=canon(r.get("name"),r.get("type"))
        samples=integer(r.get("samples"))
        exp=number(r.get("expected_hz"))
        actual=number(r.get("actual_rate_hz"))
        declared=number(r.get("declared_hz"))
        err=100*(actual-exp)/exp if np.isfinite(actual) and np.isfinite(exp) and exp>0 else np.nan
        if np.isnan(err): err=number(r.get("rate_error_percent"))
        first=number(r.get("first_s")); last=number(r.get("last_s"))
        span=number(r.get("measured_span_s"))
        if not np.isfinite(span) and np.isfinite(first) and np.isfinite(last):span=max(0.,last-first)
        warn=r.get("warnings",[])
        if isinstance(warn,str):warn=[w.strip() for w in warn.split(" | ") if w.strip()]
        row={"session_id":key,"group":group,"session":sid,"device":device,"stream_name":r.get("name"),"stream_type":r.get("type"),
             "source_id":r.get("source_id"),"source_file":session["source_file"],"scope":scope,
             "samples":samples,"expected_hz":exp,"observed_hz":actual,"declared_hz":declared,
             "rate_error_pct":err,"abs_rate_error_pct":abs(err) if np.isfinite(err) else np.nan,
             "median_interval_ms":number(r.get("median_interval_ms")),"jitter_mad_ms":number(r.get("jitter_mad_ms")),
             "large_gap_count":integer(r.get("large_gap_count")),"largest_gap_ms":number(r.get("largest_gap_ms")),
             "reported_gap_events":len(r.get("gap_events", []) or []),
             "nonpositive_intervals":integer(r.get("nonpositive_intervals")),
             "measured_span_s":span,"first_lsl_s":first,"last_lsl_s":last,
             "coverage_pct":100*span/duration if np.isfinite(span) and np.isfinite(duration) and duration>0 else np.nan,
             "status_reported":str(r.get("status", "")),"warning_count":len(warn),"warnings":" | ".join(warn),
             "is_periodic":device in REGULAR,"is_event_stream":device not in REGULAR}
        row["quality_class"],flaglist=regularity_flag(row,threshold=threshold)
        row["quality_flags"]=" | ".join(flaglist)
        row["jitter_normalized_pct"]=100*row["jitter_mad_ms"]/(1000/exp) if np.isfinite(row["jitter_mad_ms"]) and np.isfinite(exp) and exp>0 else np.nan
        row["gaps_per_min"]=60*row["large_gap_count"]/span if np.isfinite(span) and span>0 else np.nan
        row["gap_fraction_pct"]=100*row["large_gap_count"] / max(1,samples-1) if samples>1 else np.nan
        row["gap_event_list_truncated"]=(row["reported_gap_events"] < row["large_gap_count"]) if p.suffix.lower()==".json" else None
        streams.append(row)
    return session,streams,scenes,media


def discover_reports(root: Path, output: Path | None = None):
    """Walk report directories without traversing huge microscope JPEG collections.

    Follows no symbolic folder links. Valid report files in participant/session
    folders are discovered even when other raw files share those folders.
    """
    sources = []
    ignored = {"__pycache__", ".git", "node_modules", "venv", ".venv"}
    root = root.resolve()
    output = output.resolve() if output else None
    for current, dirs, names in os.walk(root, followlinks=False):
        directory = Path(current)
        kept = []
        for name in dirs:
            candidate = directory / name
            if name.lower() in ignored or name.lower().startswith("scenepics_"):
                continue
            if output and (candidate == output or output in candidate.parents):
                continue
            if name == "IMMSS_Quality_Results":
                continue
            if candidate.is_symlink():
                continue
            kept.append(name)
        dirs[:] = kept
        for name in names:
            lower = name.lower()
            if lower.startswith("immss_data_quality") and lower.endswith((".json", ".csv")):
                sources.append(directory / name)
    json_files = sorted(p for p in sources if p.suffix.lower() == ".json")
    paired = {(p.parent, p.stem.lower()) for p in json_files}
    csv_files = sorted(p for p in sources if p.suffix.lower() == ".csv" and
                       (p.parent, p.stem.lower()) not in paired)
    return json_files + csv_files


def scan(root:Path, output:Path|None=None, verbose=False, *, include_gaps=False):
    root=root.resolve()
    sources=discover_reports(root, output)
    if not sources: raise ValueError(
        f"No immss_data_quality*.json/csv files found under {root}. "
        "Place exported reviewer quality reports in Expert/Novice folders."
    )
    status(f"Found {len(sources)} report file(s); reading metadata...", verbose)
    sessions=[]; streams=[]; scenes=[]; medias=[]; gaps=[]
    for count,p in enumerate(sources, start=1):
        if count == 1 or count % 10 == 0 or count == len(sources):
            status(f"Reading report {count}/{len(sources)}: {p.name}", verbose)
        try:
            sess,stream,scene,media=unpack_report(p,root)
            sessions.append(sess);streams.extend(stream);scenes.extend(scene);medias.extend(media)
            if include_gaps and p.suffix.lower() == ".json":
                # Re-read small exported reports only in this optional mode.
                with p.open(encoding="utf-8") as fh: obj=json.load(fh)
                for row in obj.get("streams", []) or []:
                    device=canon(row.get("name"),row.get("type"))
                    if device not in REGULAR: continue
                    for event in row.get("gap_events", []) or []:
                        at=number(event.get("lsl_s"))
                        duration=number(event.get("duration_ms"))
                        if np.isfinite(at) and np.isfinite(duration):
                            origin=number(sess.get("lsl_start_s"))
                            gaps.append({"session_id":sess["session_id"],
                                         "group":sess["group"],"session":sess["session"],
                                         "source_file":sess["source_file"],
                                         "device":device,"lsl_s":at,
                                         "session_relative_s":at-origin if np.isfinite(origin) else np.nan,
                                         "duration_ms":duration})
        except (OSError, ValueError,KeyError,TypeError,json.JSONDecodeError) as e:
            raise ValueError(f"Cannot read {p}: {e}") from e
    chosen={}; duplicate_notes=[]
    for i,sess in enumerate(sessions):
        key=sess["session_id"]
        score=(int(sess["scope"].lower()=="full"), sources[i].stat().st_mtime, i)
        if key in chosen: duplicate_notes.append(key)
        if key not in chosen or score>chosen[key][0]:chosen[key]=(score,i)
    keep_pairs={(sessions[i]["session_id"],sessions[i]["source_file"]) for _,i in chosen.values()}
    sessions=[sessions[i] for _,i in sorted(chosen.values(), key=lambda v:v[1])]
    streams=[v for v in streams if (v["session_id"],v["source_file"]) in keep_pairs]
    keep={s["session_id"] for s in sessions}
    scenes=[v for v in scenes if v["session_id"] in keep]
    medias=[v for v in medias if v["session_id"] in keep]
    gaps=[v for v in gaps if (v["session_id"],v["source_file"]) in keep_pairs]
    outputs=(pd.DataFrame(sessions),pd.DataFrame(streams),pd.DataFrame(scenes),
             pd.DataFrame(medias),duplicate_notes)
    if include_gaps:return (*outputs,pd.DataFrame(gaps))
    return outputs


def safe_median(values):
    vals=pd.to_numeric(pd.Series(values),errors="coerce").dropna()
    return float(vals.median()) if len(vals) else np.nan


def aggregate(session_df, stream_df):
    records=[]
    for _,se in session_df.iterrows():
        ss=stream_df[stream_df.session_id==se.session_id]
        rr=ss[ss.is_periodic]
        # Compliance only means rate tolerance, not full signal correctness.
        eligible=rr[np.isfinite(rr["abs_rate_error_pct"]) & (rr["samples"]>0)]
        compliant=(eligible["abs_rate_error_pct"]<=se.rate_tolerance_pct)
        record=se.to_dict()
        record.update({"periodic_stream_count":len(rr),"rate_evaluable_streams":len(eligible),
                       "rate_compliant_streams":int(compliant.sum()),
                       "rate_compliance_pct":float(100*compliant.mean()) if len(eligible) else np.nan,
                       "streams_checked":len(ss),"streams_flagged":int((rr.quality_class!="OK").sum()),
                       "periodic_nonpositive_intervals":int(rr.nonpositive_intervals.sum()),
                       "periodic_reported_gap_intervals":int(rr.large_gap_count.sum()),
                       "all_reported_warning_count":int(ss.warning_count.sum()),
                       "marker_samples":int(ss.loc[ss.device=="Experiment Markers","samples"].sum())})
        for item in ("Camera 1","Camera 2","Camera 3","Eye Tracking","Polar ECG","Polar ACC","Microphone"):
            vals=ss.loc[ss.device==item,"observed_hz"].dropna()
            record["observed_hz_"+re.sub(r"\W+","_",item)]=float(vals.iloc[0]) if len(vals) else np.nan
        records.append(record)
    overview=pd.DataFrame(records)
    rank={"Expert":0,"Novice 1":1,"Novice 2":2,"Unassigned":3}
    overview["_group_rank"]=overview["group"].map(rank).fillna(4)
    overview["_session_number"]=overview["session"].map(
        lambda x: int(re.search(r"(\d+)$",str(x)).group(1)) if re.search(r"(\d+)$",str(x)) else -1
    )
    overview=overview.sort_values(["_group_rank","_session_number","session"]).drop(
        columns=["_group_rank","_session_number"]
    ).reset_index(drop=True)
    groups=[]
    for group,g in overview.groupby("group",sort=False):
        groups.append({"group":group,"n_sessions":len(g),"median_duration_s":safe_median(g.duration_s),
                      "median_common_coverage_pct":safe_median(g.common_coverage_pct),
                      "median_rate_compliance_pct":safe_median(g.rate_compliance_pct),
                      "median_cam1_fps":safe_median(g.observed_hz_Camera_1),
                      "median_cam2_fps":safe_median(g.observed_hz_Camera_2),
                      "median_cam3_fps":safe_median(g.observed_hz_Camera_3),
                      "median_ecg_hz":safe_median(g.observed_hz_Polar_ECG),
                      "median_eye_hz":safe_median(g.observed_hz_Eye_Tracking),
                      "total_flagged_periodic_streams":int(g.streams_flagged.sum())})
    return overview,pd.DataFrame(groups)


def consolidate_matrix(stream_df, session_df, metric, fill=np.nan):
    keys=session_df.session_id.tolist()
    arr=pd.DataFrame(index=REGULAR,columns=keys,dtype=float if metric!="quality_class" else object)
    for (sess,device),block in stream_df[stream_df.device.isin(REGULAR)].groupby(["session_id","device"]):
        if len(block)==1:val=block.iloc[0][metric]
        elif metric=="quality_class":val="MULTIPLE"
        else:val=block[metric].median()
        arr.loc[device,sess]=val
    return arr


def display_label(sess: pd.Series):
    return f"{sess['group']} · {sess['session']}"


def chart_paper(fig, output, stem, *, svg=True):
    """Always close figures, including if writing a file raises an exception."""
    try:
        fig.savefig(output / f"{stem}.png", bbox_inches="tight", dpi=145,
                    facecolor="white")
        if svg:
            fig.savefig(output / f"{stem}.svg", bbox_inches="tight",
                        facecolor="white")
    finally:
        plt.close(fig)


def make_static_graphs(output, overview, streams, *, svg=True,
                       max_sessions_per_page=12, verbose=False):
    """Limit each figure's dimensions so large cohorts cannot exhaust RAM."""
    for first in range(0, len(overview), max_sessions_per_page):
        subset = overview.iloc[first:first + max_sessions_per_page].copy()
        ssub = streams[streams.session_id.isin(subset.session_id)]
        suffix = "" if len(overview) <= max_sessions_per_page else f"_page_{first // max_sessions_per_page + 1:02d}"
        status(f"Rendering figure page {first // max_sessions_per_page + 1} "
               f"({first + 1}–{min(first + len(subset), len(overview))} "
               f"of {len(overview)} sessions)", verbose)
        _make_static_graphs_page(output, subset, ssub, suffix=suffix, svg=svg)


def _make_static_graphs_page(output,overview,streams, *, suffix="",svg=True):
    labels=[display_label(s) for _,s in overview.iterrows()]
    sorted_idx=overview.session_id.tolist()
    # Rates within a modality are comparable with reference, so use percentage deviation.
    m=consolidate_matrix(streams,overview,"abs_rate_error_pct")
    a=m[sorted_idx].to_numpy(float)
    fig,ax=plt.subplots(figsize=(max(9,min(18,len(labels)*1.25)),5.4))
    masked=np.ma.masked_invalid(a)
    im=ax.imshow(masked,aspect="auto",cmap="YlOrRd",vmin=0,vmax=max(25.0,float(np.nanmax(a)) if np.isfinite(a).any() else 25.0))
    ax.set_yticks(range(len(REGULAR)),REGULAR)
    ax.set_xticks(range(len(labels)),labels,rotation=35,ha="right")
    ax.set_title("Absolute sampling-rate deviation from reference (%)")
    for y in range(len(REGULAR)):
        for x in range(len(labels)):
            if np.isfinite(a[y,x]):ax.text(x,y,f"{a[y,x]:.1f}%",ha="center",va="center",fontsize=8)
            else:ax.text(x,y,"—",ha="center",va="center",fontsize=8)
    fig.colorbar(im,ax=ax,label="Absolute rate deviation (%)",shrink=.85)
    chart_paper(fig,output,f"01_rate_error_matrix{suffix}",svg=svg)

    m=consolidate_matrix(streams,overview,"quality_class")
    codes=np.array([[FLAG_ORDER.index(v) if isinstance(v,str) and v in FLAG_ORDER else FLAG_ORDER.index("NOT_REPORTED") for v in m[c].tolist()] for c in sorted_idx]).T
    from matplotlib.colors import ListedColormap,BoundaryNorm
    fig,ax=plt.subplots(figsize=(max(9,min(18,len(labels)*1.25)),5.5))
    cmap=ListedColormap(CLASS_COLORS)
    ax.imshow(codes,aspect="auto",cmap=cmap,norm=BoundaryNorm(np.arange(-.5,len(FLAG_ORDER)+.5,1),len(FLAG_ORDER)))
    ax.set_yticks(range(len(REGULAR)),REGULAR)
    ax.set_xticks(range(len(labels)),labels,rotation=35,ha="right")
    for y in range(len(REGULAR)):
        for x in range(len(labels)):
            val=m.iloc[y,x]
            ax.text(x,y,str(val) if isinstance(val,str) else "—",ha="center",va="center",fontsize=8)
    ax.set_title("Device quality-flag matrix (diagnostic, not a quality score)")
    handles=[Patch(color=CLASS_COLORS[i],label=CLASS_LABELS[c]) for i,c in enumerate(FLAG_ORDER) if c in set(codes.flat)]
    fig.legend(handles=handles,loc="lower center",ncol=4,fontsize=8,frameon=False)
    fig.subplots_adjust(bottom=.25)
    chart_paper(fig,output,f"02_quality_flag_matrix{suffix}",svg=svg)

    fig,ax=plt.subplots(figsize=(max(9,min(18,len(labels)*1.25)),5))
    x=np.arange(len(labels))
    cameras=["Camera 1","Camera 2","Camera 3"]
    width=.23
    for k,cam in enumerate(cameras):
        yy=[]
        for s in sorted_idx:
            rows=streams[(streams.session_id==s)&(streams.device==cam)]
            yy.append(float(rows.observed_hz.iloc[0]) if len(rows) else np.nan)
        ax.bar(x+(k-1)*width,yy,width,label=cam)
    ax.axhline(25,linestyle="--",linewidth=1,color="black",label="25 fps reference")
    ax.set_ylabel("Observed camera metadata rate (fps)")
    ax.set_xticks(x,labels,rotation=35,ha="right")
    ax.set_ylim(0,max(28,ax.get_ylim()[1]))
    ax.legend(frameon=False,ncol=4)
    ax.set_title("Camera frame-metadata acquisition rates")
    chart_paper(fig,output,f"03_camera_fps{suffix}",svg=svg)

    fig,ax=plt.subplots(figsize=(max(9,min(18,len(labels)*1.25)),4.6))
    ax.bar(np.arange(len(labels))-.16,overview.common_coverage_pct, width=.32,label="Common overlap")
    ax.bar(np.arange(len(labels))+.16,overview.rate_compliance_pct,width=.32,label="Periodic rate compliance")
    ax.set_ylim(0,105)
    ax.set_xticks(range(len(labels)),labels,rotation=35,ha="right")
    ax.set_ylabel("Percentage (%)")
    ax.set_title("Session-level coverage and rate compliance")
    ax.legend(frameon=False)
    chart_paper(fig,output,f"04_overall_metrics{suffix}",svg=svg)

    fig,axes=plt.subplots(nrows=len(overview),ncols=1,figsize=(11,max(3.4,len(overview)*2.8)),sharex=False)
    if len(overview)==1:axes=[axes]
    for ax,(_,se) in zip(axes,overview.iterrows()):
        part=streams[(streams.session_id==se.session_id)&(streams.device.isin(REGULAR))]
        st=number(se.lsl_start_s)
        for y,dev in enumerate(REGULAR):
            lines=part[part.device==dev]
            for _,r in lines.iterrows():
                fs=number(r.first_lsl_s); ls=number(r.last_lsl_s)
                if np.isfinite(st) and np.isfinite(fs) and np.isfinite(ls):
                    ax.plot([fs-st,ls-st],[y,y],linewidth=7,solid_capstyle="butt")
        ax.set_yticks(range(len(REGULAR)),REGULAR,fontsize=8)
        ax.invert_yaxis()
        ax.set_xlim(0,max(1,number(se.duration_s,1)))
        ax.set_title(display_label(se),fontsize=10,loc="left")
        ax.set_xlabel("Seconds since this session's XDF start")
        ax.grid(axis="x",alpha=.25)
    fig.suptitle("Stream coverage within each XDF session (different sessions are not clock-matched)")
    fig.tight_layout()
    chart_paper(fig,output,f"05_xdf_coverage{suffix}",svg=svg)

    # Device-specific timing diagnostics; event-driven streams remain excluded.
    for metric, stem, title, unit in [
        ("jitter_mad_ms", "06_jitter_mad_ms", "Timestamp interval jitter (MAD)", "milliseconds"),
        ("gaps_per_min", "07_gaps_per_min", "Long timestamp-gap intervals per minute", "events/min"),
        ("largest_gap_ms", "08_largest_gap_ms", "Longest reported timestamp gap", "milliseconds"),
    ]:
        matrix=consolidate_matrix(streams,overview,metric)[sorted_idx].to_numpy(float)
        fig,ax=plt.subplots(figsize=(max(9,min(18,len(labels)*1.25)),5.1))
        # Nonlinear transform only for color, retain raw metric annotations.
        shown=np.log1p(np.clip(matrix,0,None))
        cmap=plt.get_cmap("YlOrRd").copy()
        cmap.set_bad("#edf1f5")
        im=ax.imshow(np.ma.masked_invalid(shown),aspect="auto",cmap=cmap)
        ax.set_yticks(range(len(REGULAR)),REGULAR)
        ax.set_xticks(range(len(labels)),labels,rotation=35,ha="right")
        ax.set_title(title + " — periodic streams only")
        for i in range(len(REGULAR)):
            for j in range(len(labels)):
                value=matrix[i,j]
                if np.isfinite(value):
                    label=f"{value:.2f}" if value<100 else f"{value:.0f}"
                    ax.text(j,i,label,ha="center",va="center",fontsize=8)
        fig.colorbar(im,ax=ax,label=f"log(1 + {unit})",shrink=.8)
        chart_paper(fig,output,f"{stem}{suffix}",svg=svg)


def plotly_figures(overview,streams):
    labels=[display_label(s) for _,s in overview.iterrows()]
    keys=overview.session_id.tolist()
    figs=[]
    def shared(fig,title,height=480):
        fig.update_layout(title=dict(text=title,font=dict(size=18)),height=height,margin=dict(l=10,r=15,t=55,b=20),
                          paper_bgcolor="white",plot_bgcolor="white",font=dict(family="Arial"))
        return fig
    classes=consolidate_matrix(streams,overview,"quality_class")
    z=np.array([[FLAG_ORDER.index(x) if x in FLAG_ORDER else FLAG_ORDER.index("NOT_REPORTED") for x in classes[k].fillna("NOT_REPORTED").tolist()] for k in keys]).T
    scale=[]
    for i,c in enumerate(CLASS_COLORS):
        left=i/len(CLASS_COLORS);right=(i+1)/len(CLASS_COLORS)
        scale.extend([[left,c],[right,c]])
    f=go.Figure(data=go.Heatmap(x=labels,y=REGULAR,z=z,zmin=0,zmax=len(CLASS_COLORS),colorscale=scale,
        text=[[str(classes.loc[dev,key]) if pd.notna(classes.loc[dev,key]) else "—" for key in keys] for dev in REGULAR],
        texttemplate="%{text}",textfont=dict(size=11),showscale=False, hovertemplate="%{y}<br>%{x}<br>Classification: %{text}<extra></extra>"))
    f.update_yaxes(autorange="reversed");figs.append(shared(f,"Diagnostic quality matrix (not a composite score)",420))

    rate=consolidate_matrix(streams,overview,"abs_rate_error_pct")
    f=go.Figure(data=go.Heatmap(x=labels,y=REGULAR,z=rate[keys].to_numpy(float),colorscale="YlOrRd",zmin=0,
                               colorbar=dict(title="Rate error (%)"),
                               text=np.where(np.isfinite(rate[keys].to_numpy(float)),np.char.mod("%.2f%%",np.nan_to_num(rate[keys].to_numpy(float))),"—"),
                               texttemplate="%{text}",hovertemplate="%{y}<br>%{x}<br>Absolute deviation %{z:.3f}%<extra></extra>"))
    f.update_yaxes(autorange="reversed");figs.append(shared(f,"Absolute reference-rate deviation (%)",420))

    f=go.Figure()
    for device in ["Camera 1","Camera 2","Camera 3"]:
        vals=[]
        for key in keys:
            matches=streams[(streams.session_id==key)&(streams.device==device)]
            vals.append(number(matches.observed_hz.iloc[0]) if len(matches) else np.nan)
        f.add_trace(go.Bar(x=labels,y=vals,name=device,hovertemplate="%{x}: %{y:.2f} fps<extra>%{fullData.name}</extra>"))
    f.add_hline(y=25,line_dash="dash",line_color="black",annotation_text="25 fps reference")
    f.update_layout(barmode="group",yaxis_title="Observed metadata rate (fps)")
    figs.append(shared(f,"Camera frame-metadata rates",420))

    f=go.Figure()
    f.add_trace(go.Bar(x=labels,y=overview.common_coverage_pct,name="Common XDF overlap / session span"))
    f.add_trace(go.Bar(x=labels,y=overview.rate_compliance_pct,name="Periodic streams within rate tolerance"))
    f.update_layout(barmode="group",yaxis=dict(title="Percent",range=[0,105]))
    figs.append(shared(f,"Overall session metrics",420))

    coverage=consolidate_matrix(streams,overview,"coverage_pct")
    f=go.Figure(go.Heatmap(x=labels,y=REGULAR,z=coverage[keys].to_numpy(float),colorscale="Blues",zmin=0,zmax=100,
                           colorbar=dict(title="Span (%)"),text=np.where(np.isfinite(coverage[keys].to_numpy(float)),np.char.mod("%.1f%%",np.nan_to_num(coverage[keys].to_numpy(float))),"—"),
                           texttemplate="%{text}",hovertemplate="%{y}<br>%{x}<br>First-to-last span / XDF span: %{z:.1f}%<extra></extra>"))
    f.update_yaxes(autorange="reversed");figs.append(shared(f,"XDF timestamp span coverage (not data completeness)",420))

    jitter=consolidate_matrix(streams,overview,"jitter_normalized_pct")
    f=go.Figure(go.Heatmap(x=labels,y=REGULAR,z=jitter[keys].to_numpy(float),colorscale="PuBu",colorbar=dict(title="MAD / period (%)"),
                           hovertemplate="%{y}<br>%{x}<br>Timestamp MAD / nominal interval: %{z:.2f}%<extra></extra>"))
    f.update_yaxes(autorange="reversed");figs.append(shared(f,"Jitter MAD relative to expected period (%)",420))

    # Group-level comparisons are descriptive only, no statistical inference.
    f=go.Figure()
    for group, g in overview.groupby("group",sort=True):
        vals=g.rate_compliance_pct.to_numpy(float)
        f.add_trace(go.Box(y=vals,name=f"{group} (n={len(g)})",boxpoints="all",jitter=.18,pointpos=-1.5))
    f.update_layout(yaxis=dict(title="Periodic rate-compliant streams (%)",range=[0,105]))
    figs.append(shared(f,"Group-level distribution of rate compliance (descriptive)",410))
    for metric, title, label in [
        ("jitter_mad_ms", "Timestamp jitter MAD by stream (ms)", "Jitter MAD (ms)"),
        ("gaps_per_min", "Long-gap intervals per minute by stream", "Intervals / minute"),
        ("largest_gap_ms", "Largest long gap (ms) by stream", "Longest gap (ms)"),
    ]:
        values=consolidate_matrix(streams,overview,metric)[keys].to_numpy(float)
        f=go.Figure(go.Heatmap(x=labels,y=REGULAR,z=values,colorscale="YlOrRd",
                     colorbar=dict(title=label),
                     hovertemplate="%{y}<br>%{x}<br>%{z:.3f}<extra></extra>"))
        f.update_yaxes(autorange="reversed")
        figs.append(shared(f,title,440))
    return figs


def gap_timeline_figure(gaps, overview, max_points=2500):
    """Interactive report-level gap events; timestamps relative to each XDF session.

    The source IMMSS reporter may export fewer gap_events than large_gap_count.
    This plot never treats the event list as complete without checking counts.
    """
    if gaps.empty:return None
    valid=gaps.copy()
    valid=valid[np.isfinite(valid.session_relative_s) & np.isfinite(valid.duration_ms)]
    if valid.empty:return None
    # To keep browser responsive, show the largest events and evenly spaced others.
    if len(valid)>max_points:
        high=valid.nlargest(max_points//2,"duration_ms")
        rest=valid.drop(high.index)
        take=rest.iloc[np.linspace(0,len(rest)-1,max_points-len(high),dtype=int)]
        valid=pd.concat([high,take],ignore_index=True)
    valid["label"]=valid["group"].astype(str)+" · "+valid["session"].astype(str)+" · "+valid["device"].astype(str)
    fig=go.Figure(go.Scattergl(x=valid.session_relative_s,y=valid.label,mode="markers",
        marker=dict(size=7,color=np.log1p(valid.duration_ms),colorscale="YlOrRd",showscale=True,
                    colorbar=dict(title="log1p(ms)")),
        customdata=valid[["duration_ms","lsl_s"]].to_numpy(),
        hovertemplate="%{y}<br>Session-relative t=%{x:.3f}s<br>Gap=%{customdata[0]:.2f}ms<br>XDF LSL=%{customdata[1]:.6f}s<extra></extra>"))
    fig.update_layout(title="Reported gap-event timeline (not confirmed dropped data)",
                      xaxis_title="Seconds since each session's XDF start",height=max(400,min(900,120+int(valid.label.nunique())*24)),
                      margin=dict(l=10,r=10,t=55,b=35),paper_bgcolor="white",plot_bgcolor="white")
    return fig


def html_escape(x):
    import html
    return html.escape(str(x),quote=True)


def make_html(output, overview, streams, groups, scenes, media, figures):
    """Small HTML and lazy-loaded interactive charts; Plotly JS is local/offline."""
    js_path = output / "plotly.min.js"
    if not js_path.is_file():
        js_path.write_text(get_plotlyjs(), encoding="utf-8")

    med_overlap = safe_median(overview.common_coverage_pct)
    med_rate = safe_median(overview.rate_compliance_pct)
    percent = lambda v: f"{v:.1f}%" if np.isfinite(v) else "N/A"
    metrics = [("Sessions", str(len(overview))),
               ("Group labels", str(overview.group.nunique())),
               ("Median common overlap", percent(med_overlap)),
               ("Median rate compliance", percent(med_rate))]
    summary = overview[["group", "session", "duration_s", "common_coverage_pct",
                        "rate_compliance_pct", "streams_flagged", "marker_samples"]].copy()
    summary.columns = ["Group", "Session", "XDF span (s)", "Common overlap (%)",
                       "Rate compliance (%)", "Periodic streams flagged", "Marker samples"]
    group = groups.rename(columns={"group": "Group", "n_sessions": "Sessions",
                                   "median_duration_s": "Median span (s)",
                                   "median_common_coverage_pct": "Median overlap (%)",
                                   "median_rate_compliance_pct": "Median rate compliant (%)",
                                   "median_cam1_fps": "Camera 1 median fps",
                                   "median_cam2_fps": "Camera 2 median fps",
                                   "median_cam3_fps": "Camera 3 median fps"})
    group = group[["Group", "Sessions", "Median span (s)", "Median overlap (%)",
                   "Median rate compliant (%)", "Camera 1 median fps",
                   "Camera 2 median fps", "Camera 3 median fps"]]
    notes = (
        "Data are exported IMMSS quality reports, not raw XDF. Timestamps within "
        "each session use its corrected XDF clock; independent sessions are NOT "
        "placed on a single physical clock. Rate agreement does not measure true "
        "signal completeness or cross-device synchronization accuracy. EyeEvents, "
        "HR, RR and markers are event-driven; their spacing is not packet loss. "
        "Audio/Polar timestamp gaps may represent chunking or timestamp "
        "reconstruction, not necessarily lost samples. MP4 frame counts can differ "
        "from XDF entries when recordings start at different times."
    )
    css = '''
    :root{--navy:#17324D;--blue:#2879AA;--teal:#16877B;--muted:#61758A}
    body{font:15px/1.45 system-ui,Segoe UI,Arial;background:#f5f7fa;color:#15283e;margin:0}
    header{background:#101c31;color:white;padding:24px max(22px,calc((100vw - 1240px)/2))}
    header h1{margin:0;font-size:25px}header p{color:#cad6e2;margin:5px 0 0}
    main{max-width:1240px;margin:auto;padding:20px}
    h2{font-size:19px;margin:27px 0 12px}
    .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
    .kpi{border-radius:10px;background:white;padding:15px;border:1px solid #e3e8ee}
    .kpi span{display:block;color:#52677e;font-size:12px}
    .kpi strong{display:block;font-size:27px}
    section{background:white;border:1px solid #e3e8ee;border-radius:10px;margin:14px 0;padding:12px 16px;overflow-x:auto}
    table{border-collapse:collapse;min-width:650px;width:100%;font-size:13px}
    th,td{text-align:left;padding:8px 9px;border-bottom:1px solid #edf0f2}
    thead{background:#edf2f7}tr:nth-child(even){background:#f8fafc}
    .note{background:#fff9e7;padding:14px;border-left:4px solid #c39d35}
    .plot{min-height:260px}footer{padding:25px 0;color:#607182;font-size:12px}
    header{background:linear-gradient(120deg,#122940,#254B67)}
    .kpi strong{color:#17324D}.kpi{border-left:3px solid #2879AA;box-shadow:0 2px 12px #17324D0A}
    .findings{display:grid;grid-template-columns:repeat(auto-fit,minmax(225px,1fr));gap:12px}
    .finding{background:#fff;border:1px solid #dce6ed;border-radius:12px;padding:17px 18px}
    .finding strong{display:block;margin-bottom:7px;color:#17324D;font-size:15px}
    .finding p{color:#52677e;font-size:13px;margin:0;line-height:1.55}
    section{border-radius:12px;box-shadow:0 2px 10px #17324D08}
    .toc{display:flex;flex-wrap:wrap;gap:10px;padding:12px 0}
    .toc a{text-decoration:none;color:#275B78;background:white;border:1px solid #cfdee8;padding:7px 12px;border-radius:8px;font-size:13px}
    .toc a:hover{background:#e7f2fa}
    .plothead{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#61758A;padding:0 3px 8px}
    button.expand{border:1px solid #b6cad8;background:#f4f9fc;color:#204E6A;cursor:pointer;border-radius:6px;padding:5px 10px}
    section.wide{position:relative;z-index:100;box-shadow:0 0 0 100vmax #16283A66;max-width:unset;width:min(95vw,1700px);margin-left:50%;transform:translateX(-50%)}
    section.wide .plot{min-height:580px}
    
    '''
    cards = ''.join(f'<div class="kpi"><span>{html_escape(k)}</span>'
                    f'<strong>{html_escape(v)}</strong></div>' for k,v in metrics)
    chunks = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
              '<meta name="viewport" content="width=device-width,initial-scale=1">',
              '<title>IMMSS | Data Quality Comparison</title>',
              f'<style>{css}</style><script src="plotly.min.js"></script></head><body>',
              '<header><h1>IMMSS | Multimodal data quality comparison</h1>',
              '<p>Expert · Novice 1 · Novice 2 — XDF quality reports</p></header>',
              f'<main><div class="kpis">{cards}</div>',
              '<nav class="toc"><a href="#findings">Key observations</a><a href="#overview">Session table</a><a href="#charts">Charts</a><a href="#interpretation">Interpretation</a></nav>',
              '<h2 id="overview">Overview</h2><section>',
              summary.to_html(index=False, float_format=lambda x:f"{x:.2f}",na_rep="—",border=0,escape=True),
              '</section><h2>Group summary</h2><section>',
              group.to_html(index=False,float_format=lambda x:f"{x:.2f}",na_rep="—",border=0,escape=True),
              '</section>']
    # Ground the prominent insights in the exact reports (descriptive only).
    obs=[]
    for dev in ("Camera 1","Camera 2","Camera 3"):
        selected=streams.loc[(streams.device==dev)&np.isfinite(streams.rate_error_pct),"rate_error_pct"]
        if len(selected):
            med=float(selected.median())
            if abs(med)>5:
                obs.append((f"{html_escape(dev)} rate",f"Median signed deviation: {med:+.1f}% relative to the per-report reference. This is a rate mismatch, not automatically dropped frames."))
    eye=streams[(streams.device=="Eye Tracking") & np.isfinite(streams.declared_hz) & np.isfinite(streams.expected_hz)]
    if len(eye) and ((eye.declared_hz-eye.expected_hz).abs()/(eye.expected_hz.replace(0,np.nan))*100>5).any():
        obs.append(("EyeData metadata", "Some files advertise a nominal eye-tracking rate different from the observed reference. Verify the LSL outlet metadata."))
    audio=streams.loc[streams.device=="Microphone","large_gap_count"]
    if len(audio) and (audio>0).any():
        obs.append(("Microphone timing", "Audio interval flags require chunk-level inspection; their counts are not evidence of microphone sample loss."))
    if not obs:
        obs.append(("Timing diagnostics", "No prominent cross-session rate mismatch was found using the configured references. Review individual warnings and gaps."))
    chunks.extend(['<h2 id="findings">Key observations from the uploaded reports</h2><div class="findings">'])
    for title,desc in obs[:4]:
        chunks.append(f'<div class="finding"><strong>{html_escape(title)}</strong><p>{html_escape(desc)}</p></div>')
    chunks.append('</div>')
    if overview.group.nunique() == 1 and overview.group.iloc[0] == "Unassigned":
        chunks.append('<p class="note">All reports are Unassigned. Put each report under '
                      'Expert / Novice 1 / Novice 2 and rerun. No group was guessed.</p>')
    if not np.isfinite(med_overlap):
        chunks.append('<p class="note">Some source files are CSV-only: their original '
                      'XDF session start/end and coverage are not exported. '
                      'Provide the matching JSON reports for coverage and timeline charts.</p>')
    chunks.append('<h2 id="charts">Interactive visual diagnostics</h2><p>All panels refer to their own XDF sessions. Colors indicate diagnostic magnitude, not surgical skill or validated synchronization accuracy. Charts load as you scroll.</p>')
    for i, fig in enumerate(figures):
        spec = fig.to_json().replace('</', '<\\/')
        chunks.append(f'<section><div class="plothead"><span>Interactive quality view {i+1} / {len(figures)}</span><button class="expand" type="button" onclick="expandView(this)">Expand / restore</button></div><div id="graph_{i}" class="plot" '
                      f'data-chart="chart_data_{i}">Scroll here to load chart...</div>'
                      f'<script type="application/json" id="chart_data_{i}">{spec}</script></section>')
    chunks += ['<h2 id="interpretation">Interpretation and cautions</h2><section><p>', html_escape(notes), '</p></section>']
    if len(scenes):
        chunks += ['<h2>Microscope scene-picture links</h2><section>',
                   scenes[["session","group","name","linked","matched_images","image_count"]].to_html(index=False,border=0,escape=True),
                   '</section>']
    if len(media):
        chunks += ['<h2>MP4 / XDF frame inventories</h2><section>',
                   media[["session","group","device","mp4_frame_count","xdf_frame_entries","mapping"]].to_html(index=False,border=0,escape=True),
                   '</section>']
    chunks += ['<footer>IMMSS quality comparison · no raw XDF decoding</footer></main>',
               '<script>function expandView(btn){const panel=btn.closest("section");panel.classList.toggle("wide");const plot=panel.querySelector(".plot");if(window.Plotly && plot.data) Plotly.Plots.resize(plot);}</script>',
               '<script>const observer = new IntersectionObserver((entries)=>{for(const entry of entries){',
               'if(!entry.isIntersecting)continue;const el=entry.target;observer.unobserve(el);',
               'const s=document.getElementById(el.dataset.chart);const p=JSON.parse(s.textContent);',
               'Plotly.newPlot(el,p.data,p.layout,{responsive:true,displaylogo:false});',
               '}},{rootMargin:"500px"});',
               'document.querySelectorAll("[data-chart]").forEach(el=>observer.observe(el));</script>',
               '</body></html>']
    (output / "IMMSS_Quality_Dashboard.html").write_text(''.join(chunks),encoding="utf-8")


def produce(root:Path, output:Path, *, mode="full", pagesize=12, verbose=False):
    root = root.expanduser().resolve()
    output = output.expanduser().resolve()
    if output == root:
        raise ValueError("Output directory must be separate from source folder")
    output.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    status("Scanning Expert / Novice folders...",verbose)
    overview, streams, scenes, media, dups, gaps = scan(root, output, verbose=verbose, include_gaps=True)
    over, groups = aggregate(overview,streams)
    jsons = sum(str(file).lower().endswith(".json") for file in over.source_file)
    csvs = len(over) - jsons
    status(f"Loaded {len(over)} session(s), {len(streams)} stream rows "
           f"({jsons} JSON, {csvs} CSV-only).", verbose)
    if csvs:
        status("NOTE: CSV-only reports do not contain original full-session "
               "XDF start/end, so coverage is unavailable for those sessions.", verbose)
    if dups:
        status(f"Used one full/latest report for each session "
               f"({len(set(dups))} duplicate session keys found).", verbose)

    def save(df, filename):
        if df is not None and len(df):
            df.to_csv(output / filename,index=False,float_format="%.9f")
    status("Writing per-session metrics and comparison matrices...",verbose)
    save(over,"session_overview.csv")
    save(groups,"group_summary.csv")
    save(streams,"stream_metrics.csv")
    save(gaps,"gap_events_reported.csv")
    # Counts may exceed exported event lists; preserve both numbers for QA.
    save(streams[["session","group","device","large_gap_count","reported_gap_events",
                  "gap_event_list_truncated","largest_gap_ms","gaps_per_min","jitter_mad_ms",
                  "jitter_normalized_pct","nonpositive_intervals"]],"timing_quality_summary.csv")
    save(scenes,"microscope_links.csv")
    save(media,"camera_media_inventory.csv")
    for metric, filename in [("abs_rate_error_pct","matrix_absolute_rate_error_pct.csv"),
                              ("jitter_mad_ms","matrix_jitter_mad_ms.csv"),
                              ("coverage_pct","matrix_timestamp_span_pct.csv"),
                              ("quality_class","matrix_quality_flags.csv"),("gaps_per_min","matrix_gap_intervals_per_min.csv"),
                              ("largest_gap_ms","matrix_largest_gap_ms.csv"),
                              ("jitter_normalized_pct","matrix_jitter_pct_of_period.csv")]:
        matrix=consolidate_matrix(streams,over,metric)
        matrix.columns=[display_label(se) for _,se in over.iterrows()]
        matrix.rename_axis("Stream").to_csv(output/filename,float_format="%.6f")
    issues=streams[streams.warning_count>0][["group","session","device","stream_name","quality_class","status_reported","warnings"]]
    save(issues,"warnings.csv")

    if mode in ("full","quick"):
        status("Preparing visual insight maps and interactive dashboard...",verbose)
        from expressive_visuals import make_expressive_interactive
        figures=make_expressive_interactive(over,streams,gaps)
        figures.extend(plotly_figures(over,streams))
        gapfig=gap_timeline_figure(gaps,over)
        if gapfig is not None:
            figures.append(gapfig)
        make_html(output,over,streams,groups,scenes,media,figures)
        status("Interactive dashboard written (charts load as you scroll).",verbose)
        del figures
    if mode=="full":
        status("Exporting paginated PNG/SVG figures (may be CPU-intensive)...",verbose)
        make_static_graphs(output,over,streams,max_sessions_per_page=pagesize,verbose=verbose)
        status("Exporting expressive rate, jitter, session and gap views...",verbose)
        from expressive_visuals import make_expressive_static
        make_expressive_static(output,over,streams,gaps,page_size=pagesize,verbose=verbose)
    metadata={"version":VERSION,"sessions":len(over),"groups":groups.to_dict(orient="records"),
              "reports_are_quality_summaries_not_raw_xdf":True,
              "duplicate_reports_ignored":sorted(set(dups)),
              "csv_only_reports_missing_session_clock":csvs,
              "comparisons_are_descriptive_only":True,"mode":mode,
              "unassigned_reports":int((over.group=="Unassigned").sum())}
    (output/"analysis_manifest.json").write_text(
        json.dumps(metadata,indent=2,default=lambda x:None if isinstance(x,float) and not math.isfinite(x) else x),
        encoding="utf-8")
    status(f"Finished in {time.perf_counter()-t0:.1f} seconds; outputs at {output}",verbose)
    return metadata


def main():
    parser=argparse.ArgumentParser(description="IMMSS group data-quality matrix and summary dashboard")
    parser.add_argument("--root",type=Path,help="Folder containing Expert, Novice 1, Novice 2")
    parser.add_argument("--output",type=Path,help="Results folder (default: root/IMMSS_Quality_Results)")
    parser.add_argument("--mode",choices=["full","quick","tabular"],default="quick",
                        help="full: PNG/SVG + interactive HTML + CSV; quick: HTML + CSV; tabular: CSV only")
    parser.add_argument("--page-size",type=int,default=12,
                        help="Maximum sessions per static chart page (default 12)")
    parser.add_argument("--debug",action="store_true",help="Print Python traceback on failure")
    args=parser.parse_args()
    if args.page_size<1 or args.page_size>30:
        parser.error("--page-size must be from 1 to 30")
    root=args.root
    if root is None:
        status("Select the report root folder in the dialog (it may open behind Spyder)...")
        try:
            from tkinter import Tk,filedialog
            top=Tk();top.withdraw();top.attributes("-topmost", True);top.update()
            root_text=filedialog.askdirectory(title="Select IMMSS quality-report folder", parent=top)
            top.destroy()
            if not root_text:
                print("[IMMSS] Folder selection cancelled.")
                return
            root=Path(root_text)
        except Exception as e:
            parser.error(f"Supply --root path: {e}")
    if not root.is_dir():
        parser.error(f"Directory not found: {root}")
    destination=args.output or (root/"IMMSS_Quality_Results")
    try:
        result=produce(root,destination,mode=args.mode,pagesize=args.page_size,verbose=True)
    except Exception as e:
        print(f"[IMMSS] ERROR: {type(e).__name__}: {e}",file=sys.stderr,flush=True)
        if args.debug:
            traceback.print_exc()
        raise SystemExit(1) from e
    if result["unassigned_reports"]:
        print(f"NOTICE: {result['unassigned_reports']} unassigned report(s) (not in Expert/Novice folder).")
    if args.mode != "tabular":
        print(f"Dashboard: {destination/'IMMSS_Quality_Dashboard.html'}")
    print(f"CSV metrics: {destination}")
    if args.mode=="full":
        print(f"PNG/SVG figures: {destination}")


if __name__=="__main__":
    main()
