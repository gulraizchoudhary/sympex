
"""IMMSS pooled sensor-acquisition quality, independent of participant expertise.

Paper-ready variant with:
- minimal figure text
- larger typography (~18 pt baseline)
- reduced whitespace between panels
- compact multi-panel layout suitable for posters and papers

Usage:
    python immss_pooled_quality.py --root "C:/QualityReports"
    python immss_pooled_quality.py --root "C:/QualityReports" --transparent

Requires: numpy, matplotlib
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from pathlib import Path, PureWindowsPath

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

VERSION = '2.1-paper-ready'
STREAMS = [
    'Eye tracking', 'Camera 1', 'Camera 2', 'Camera 3',
    'Polar ECG', 'Ultramic audio',
]
TIMING_STREAMS = [d for d in STREAMS if d != 'Ultramic audio']
RATE_TOLERANCE = 5.0

FONT_BASE = 18
FONT_TICK = 16
FONT_SMALL = 14
FONT_TITLE = 24
FONT_PANEL = 18
FONT_KPI_VALUE = 22
FONT_KPI_LABEL = 14


def num(value, default=math.nan):
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def ident(stream):
    name = re.sub(r'[^a-z0-9]', '', str(stream.get('name', '')).lower())
    typ = str(stream.get('type', '')).lower()
    if name == 'eyedata' or typ == 'vog':
        return 'Eye tracking'
    if name == 'debugdata' or typ == 'vog_ext1':
        return 'Eye debug'
    for n in (1, 2, 3):
        if f'camera{n}' in name:
            return f'Camera {n}'
    if 'ecg' in name or typ == 'ecg':
        return 'Polar ECG'
    if ('polar' in name and 'acc' in name) or typ == 'accelerometer':
        return 'Polar ACC'
    if 'ultramic' in name or typ == 'audio' or 'audio' in name:
        return 'Ultramic audio'
    return None


def discover(root, output):
    root, output = root.resolve(), output.resolve()
    result = []
    for base, dirs, names in os.walk(root):
        p = Path(base)
        dirs[:] = sorted(
            d for d in dirs
            if not d.lower().startswith(('scenepics_', 'immss_pooled_', 'immss_poster_', 'immss_quality_results'))
            and d.lower() not in {'__pycache__', '.git', '.venv', 'venv', 'node_modules'}
            and (p / d).resolve() != output
        )
        for name in sorted(names):
            if name.lower().startswith('immss_data_quality') and name.lower().endswith('.json'):
                result.append(p / name)
    return sorted(result)


def get_records(report):
    selected = {}
    for row in report.get('streams', []):
        dev = ident(row)
        if dev not in STREAMS:
            continue
        if dev not in selected or num(row.get('samples'), 0) > num(selected[dev].get('samples'), 0):
            selected[dev] = row
    return selected


def read_reports(root, output):
    files = discover(root, output)
    if not files:
        raise ValueError('No immss_data_quality*.json files found. Export FULL-session JSON from IMMSS first.')
    sessions = {}
    bad = []
    for i, path in enumerate(files, 1):
        print(f'[Read {i}/{len(files)}] {path.relative_to(root)}', flush=True)
        try:
            with path.open(encoding='utf-8-sig') as f:
                report = json.load(f)
            if str(report.get('scope', '')).lower() != 'full':
                bad.append(f'{path}: not a full-session report')
                continue
            if not isinstance(report.get('streams'), list):
                bad.append(f'{path}: no streams list')
                continue
            t0, t1 = num(report.get('window_start_s')), num(report.get('window_end_s'))
            if not (math.isfinite(t0) and math.isfinite(t1) and t1 > t0):
                bad.append(f'{path}: invalid XDF session bounds')
                continue
            rows = get_records(report)
            sid = str(report.get('session_folder') or '').strip()
            session_key = 'xdf:' + sid.replace('\\', '/').rstrip('/').casefold() if sid else 'path:' + str(path.resolve()).casefold()
            session_name = PureWindowsPath(sid).name if sid else path.parent.name
            out = {
                'key': session_key, 'name': session_name, 'source': str(path),
                'xdf_start_s': t0, 'xdf_end_s': t1, 'duration_s': t1 - t0,
                'expected_tolerance_pct': num((report.get('thresholds') or {}).get('rate_error_percent'), RATE_TOLERANCE),
                'metrics': {},
            }
            for dev in STREAMS:
                st = rows.get(dev)
                if not st or num(st.get('samples'), 0) <= 0:
                    out['metrics'][dev] = None
                    continue
                actual, expected = num(st.get('actual_rate_hz')), num(st.get('expected_hz'))
                first, last = num(st.get('first_s')), num(st.get('last_s'))
                span = max(0.0, last - first) if math.isfinite(first) and math.isfinite(last) and last >= first else math.nan
                mad = num(st.get('jitter_mad_ms'))
                deviation = 100 * (actual / expected - 1) if math.isfinite(actual) and math.isfinite(expected) and expected > 0 else math.nan
                out['metrics'][dev] = dict(
                    expected_hz=expected,
                    observed_hz=actual,
                    rate_dev_pct=deviation,
                    rate_compliant=abs(deviation) <= RATE_TOLERANCE if math.isfinite(deviation) else None,
                    first_s=first,
                    last_s=last,
                    span_s=span,
                    span_coverage_pct=100 * span / (t1 - t0) if math.isfinite(span) else math.nan,
                    jitter_mad_ms=mad,
                    jitter_pct_period=mad * expected / 10.0 if math.isfinite(mad) and math.isfinite(expected) and expected > 0 else math.nan,
                    gap_count=num(st.get('large_gap_count')),
                    gap_per_min=60 * num(st.get('large_gap_count')) / span if math.isfinite(span) and span > 0 else math.nan,
                    samples=int(num(st.get('samples'), 0)),
                )
            if all(out['metrics'][dev] is not None and math.isfinite(out['metrics'][dev]['first_s']) and math.isfinite(out['metrics'][dev]['last_s']) for dev in STREAMS):
                left = max(out['metrics'][dev]['first_s'] for dev in STREAMS)
                right = min(out['metrics'][dev]['last_s'] for dev in STREAMS)
                out['overlap_s'] = max(0.0, right - left)
                out['overlap_pct'] = 100 * out['overlap_s'] / (t1 - t0)
            else:
                out['overlap_s'] = math.nan
                out['overlap_pct'] = math.nan
            vals = [out['metrics'][dev]['rate_compliant'] for dev in STREAMS if out['metrics'][dev] is not None and out['metrics'][dev]['rate_compliant'] is not None]
            out['rate_compliance_pct'] = 100 * sum(vals) / len(vals) if vals else math.nan
            if session_key not in sessions or path.stat().st_mtime > sessions[session_key][0]:
                sessions[session_key] = (path.stat().st_mtime, out)
        except (json.JSONDecodeError, OSError, TypeError, KeyError) as exc:
            bad.append(f'{path}: {exc}')
    for message in bad:
        print('WARNING:', message, flush=True)
    records = [v[1] for v in sessions.values()]
    if not records:
        raise ValueError('No valid full-session reports found.')
    records.sort(key=lambda s: (s['name'].lower(), s['source']))
    print(f'Using {len(records)} unique full sessions ({len(files)} JSON files inspected).', flush=True)
    return records, bad, len(files)


def stats(values):
    v = np.asarray([num(x) for x in values], dtype=float)
    v = v[np.isfinite(v)]
    return dict(
        n=len(v),
        mean=float(v.mean()) if len(v) else math.nan,
        sd=float(v.std(ddof=1)) if len(v) > 1 else math.nan,
        minimum=float(v.min()) if len(v) else math.nan,
        maximum=float(v.max()) if len(v) else math.nan,
    )


def pooled(sessions):
    summary = {}
    for dev in STREAMS:
        available = [s['metrics'][dev] for s in sessions if s['metrics'][dev] is not None]
        summary[dev] = dict(n_sessions=len(available))
        for key in ['expected_hz', 'observed_hz', 'rate_dev_pct', 'span_coverage_pct', 'jitter_mad_ms', 'jitter_pct_period', 'gap_per_min', 'gap_count']:
            summary[dev][key] = stats([r[key] for r in available])
    return dict(
        n_sessions=len(sessions),
        duration_s=stats([s['duration_s'] for s in sessions]),
        overlap_pct=stats([s['overlap_pct'] for s in sessions]),
        rate_compliance_pct=stats([s['rate_compliance_pct'] for s in sessions]),
        devices=summary,
    )


def csv_value(v):
    return f'{v:.7f}' if isinstance(v, (float, np.floating)) and math.isfinite(v) else ('' if v is None or isinstance(v, (float, np.floating)) else v)


def write_outputs(sessions, agg, output, bad, nfiles):
    output.mkdir(parents=True, exist_ok=True)
    dfields = ['sensor', 'sessions_with_data']
    mfields = ['expected_hz', 'observed_hz', 'rate_dev_pct', 'span_coverage_pct', 'jitter_mad_ms', 'jitter_pct_period', 'gap_per_min', 'gap_count']
    for m in mfields:
        dfields += [f'{m}_{f}' for f in ('n', 'mean', 'sd', 'minimum', 'maximum')]
    with (output / 'pooled_sensor_summary.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=dfields)
        writer.writeheader()
        for dev in STREAMS:
            row = dict(sensor=dev, sessions_with_data=agg['devices'][dev]['n_sessions'])
            for m in mfields:
                for k in ('n', 'mean', 'sd', 'minimum', 'maximum'):
                    row[f'{m}_{k}'] = csv_value(agg['devices'][dev][m][k])
            writer.writerow(row)
    with (output / 'per_session_stream_metrics.csv').open('w', newline='', encoding='utf-8-sig') as f:
        fields = ['session', 'source', 'sensor', 'xdf_start_s', 'xdf_end_s', 'duration_s', 'samples', 'expected_hz', 'observed_hz', 'rate_dev_pct', 'rate_compliant', 'span_coverage_pct', 'jitter_mad_ms', 'jitter_pct_period', 'gap_count', 'gap_per_min']
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in sessions:
            for dev in STREAMS:
                r = s['metrics'][dev]
                row = dict(session=s['name'], source=s['source'], sensor=dev, xdf_start_s=s['xdf_start_s'], xdf_end_s=s['xdf_end_s'], duration_s=s['duration_s'])
                if r:
                    row.update({key: r.get(key) for key in fields if key in r})
                w.writerow({k: csv_value(row.get(k)) for k in fields})
    with (output / 'pooled_session_summary.csv').open('w', newline='', encoding='utf-8-sig') as f:
        fields = ['session', 'source', 'xdf_start_s', 'xdf_end_s', 'duration_s', 'common_8stream_overlap_pct', 'rate_compliance_pct']
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for s in sessions:
            vals = [s['name'], s['source'], s['xdf_start_s'], s['xdf_end_s'], s['duration_s'], s['overlap_pct'], s['rate_compliance_pct']]
            w.writerow({k: csv_value(v) for k, v in zip(fields, vals)})
    meta = {
        'version': VERSION,
        'sessions': len(sessions),
        'report_files_inspected': nfiles,
        'source_files': [s['source'] for s in sessions],
        'ignored_reports': bad,
        'mean_session_duration_s': agg['duration_s'],
        'mean_eight_stream_common_overlap_pct': agg['overlap_pct'],
        'mean_session_rate_compliance_pct': agg['rate_compliance_pct'],
        'aggregation': 'unweighted arithmetic mean across distinct full-session reports; no Expert/Novice stratification',
        'periodic_streams': STREAMS,
        'jitter_gap_plots_exclude': 'Ultramic audio (reconstructed chunk timestamps)',
        'rate_reference_tolerance_pct': RATE_TOLERANCE,
    }
    def safe_json(x):
        if isinstance(x, dict):
            return {k: safe_json(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [safe_json(v) for v in x]
        if isinstance(x, (float, np.floating)) and not math.isfinite(x):
            return None
        if isinstance(x, np.integer):
            return int(x)
        return x
    (output / 'methods_and_summary.json').write_text(json.dumps(safe_json(meta), indent=2, allow_nan=False), encoding='utf-8')


def setup_paper_style():
    plt.rcParams.update({
        'font.size': FONT_BASE,
        'axes.titlesize': FONT_PANEL,
        'axes.labelsize': FONT_BASE,
        'xtick.labelsize': FONT_TICK,
        'ytick.labelsize': FONT_TICK,
        'legend.fontsize': FONT_SMALL,
        'axes.spines.top': False,
        'axes.spines.right': False,
        'axes.linewidth': 1.0,
        'grid.alpha': 0.18,
        'grid.linewidth': 0.8,
    })


def _point_panel(ax, data, labels, field, title, xlab, color, xlim=None, xline=None, band=None):
    ypos = np.arange(len(labels))
    for i, dev in enumerate(labels):
        values = np.array([s['metrics'][dev][field] if s['metrics'][dev] else math.nan for s in data], float)
        values = values[np.isfinite(values)]
        if len(values):
            offs = np.linspace(-0.14, 0.14, len(values)) if len(values) > 1 else [0.0]
            ax.scatter(values, np.full(len(values), i) + offs, color=color, alpha=0.28, s=70, zorder=3, linewidths=0)
            m = values.mean()
            sd = values.std(ddof=1) if len(values) > 1 else math.nan
            if np.isfinite(sd):
                ax.plot([m - sd, m + sd], [i, i], color=color, lw=3.0, zorder=4)
                ax.plot([m - sd, m - sd], [i - 0.10, i + 0.10], color=color, lw=2.0, zorder=4)
                ax.plot([m + sd, m + sd], [i - 0.10, i + 0.10], color=color, lw=2.0, zorder=4)
            ax.scatter([m], [i], marker='D', color=color, s=115, zorder=5, edgecolors='white', linewidths=1.0)
    if band is not None:
        ax.axvspan(band[0], band[1], color='#bce0d4', alpha=0.22, zorder=0)
    if xline is not None:
        ax.axvline(xline, lw=1.5, ls='--', color='#7f8f99', alpha=0.95, zorder=1)
    ax.set_yticks(ypos, labels)
    ax.set_ylim(len(labels) - 0.48, -0.48)
    ax.set_xlabel(xlab)
    #ax.set_title(title, loc='left', fontweight='bold', pad=10)
    if xlim:
        ax.set_xlim(*xlim)
    ax.grid(axis='x')
    ax.set_axisbelow(True)
    ax.spines['left'].set_visible(False)
    ax.tick_params(axis='y', length=0, pad=8)


def create_figure(sessions, agg, output, transparent=False):
    setup_paper_style()
    navy = '#234262'
    teal = '#137c82'
    orange = '#c46c2a'
    purple = '#7c62a0'

    face = 'none' if transparent else 'white'
    fig = plt.figure(figsize=(18, 11), facecolor=face)
    grid = GridSpec(2, 2, figure=fig, left=0.08, right=0.985, top=0.78, bottom=0.09, wspace=0.18, hspace=0.20)
    a = fig.add_subplot(grid[0, 0])
    b = fig.add_subplot(grid[0, 1])
    c = fig.add_subplot(grid[1, 0])
    d = fig.add_subplot(grid[1, 1])

   # fig.text(0.08, 0.955, 'IMMSS Multimodal Stream Quality', fontsize=FONT_TITLE, fontweight='bold', color=navy)

    duration = agg['duration_s']
    overlap = agg['overlap_pct']
    compliant = agg['rate_compliance_pct']
    kpis = [
        (f'{len(sessions)}', 'Sessions'),
        (f"{duration['mean']:.1f} s" if duration['n'] else '—', 'Mean duration'),
        (f"{overlap['mean']:.1f}%" if overlap['n'] else '—', 'Mean overlap'),
        (f"{compliant['mean']:.1f}%" if compliant['n'] else '—', 'Rate compliant'),
    ]
    xx = [0.08, 0.31, 0.54, 0.77]
    for x, (value, label) in zip(xx, kpis):
        fig.text(x, 0.885, value, fontsize=FONT_KPI_VALUE, fontweight='bold', color=navy)
        fig.text(x, 0.858, label, fontsize=FONT_KPI_LABEL, color='#566879')

    _point_panel(
        a, sessions, STREAMS, 'rate_dev_pct',
        'A  Rate deviation', 'Deviation from reference (%)',
        navy, xlim=(-25, 8), xline=0, band=(-5, 5)
    )

    labels_b = STREAMS + ['Common overlap']
    for i, dev in enumerate(labels_b):
        vals = [
            s['overlap_pct'] if dev == 'Common overlap' else (s['metrics'][dev]['span_coverage_pct'] if s['metrics'][dev] else math.nan)
            for s in sessions
        ]
        valid = np.asarray([v for v in vals if math.isfinite(v)], float)
        if not len(valid):
            continue
        offs = np.linspace(-0.12, 0.12, len(valid)) if len(valid) > 1 else [0]
        b.scatter(valid, np.full(len(valid), i) + offs, s=70, alpha=0.30, color=teal, zorder=3, linewidths=0)
        mean = float(valid.mean())
        sd = float(valid.std(ddof=1)) if len(valid) > 1 else math.nan
        if np.isfinite(sd):
            b.plot([mean - sd, mean + sd], [i, i], color=teal, lw=3.0, zorder=4)
        b.scatter(mean, i, s=115, marker='D', color=teal, edgecolors='white', linewidths=1.0, zorder=5)
    b.set_yticks(np.arange(len(labels_b)), labels_b)
    b.set_ylim(len(labels_b) - 0.48, -0.48)
    b.set_xlim(85, 102)
    b.axvline(100, ls='--', color='#9babb6', lw=1.5)
    #b.set_title('B  Span coverage', loc='left', fontweight='bold', pad=10)
    b.set_xlabel('Recorded span (%)')
    b.grid(axis='x')
    b.set_axisbelow(True)
    b.spines['left'].set_visible(False)
    b.tick_params(axis='y', length=0, pad=8)

    _point_panel(
        c, sessions, TIMING_STREAMS, 'jitter_pct_period',
        'C  Timestamp jitter', 'MAD / expected period (%)',
        orange, xlim=(0, 12), xline=0
    )
    _point_panel(
        d, sessions, TIMING_STREAMS, 'gap_per_min',
        'D  Gaps', 'Flagged intervals per minute',
        purple, xlim=(0, 72), xline=0
    )

    for fmt in ('png', 'svg'):
        dest = output / f'IMMSS_Pooled_Quality.{fmt}'
        fig.savefig(
            dest,
            dpi=300 if fmt == 'png' else None,
            transparent=transparent,
            facecolor=fig.get_facecolor(),
            bbox_inches='tight',
            pad_inches=0.03,
        )
    plt.close(fig)


def main(argv=None):
    p = argparse.ArgumentParser(description='Pool IMMSS XDF quality metrics across all sessions (ignores Expert/Novice labels).')
    p.add_argument('--root', type=Path, help='Folder with full-session immss_data_quality*.json files, recursively')
    p.add_argument('--output', type=Path, help='Output directory (default root/IMMSS_Pooled_Results)')
    p.add_argument('--transparent', action='store_true', help='Use transparent background for poster PNG/SVG')
    a = p.parse_args(argv)
    root = a.root
    if root is None:
        from tkinter import Tk, filedialog
        w = Tk(); w.withdraw(); w.attributes('-topmost', True)
        selected = filedialog.askdirectory(title='Choose folder containing IMMSS quality JSON reports'); w.destroy()
        if not selected:
            return
        root = Path(selected)
    root = root.expanduser().resolve()
    if not root.is_dir():
        p.error(f'Folder not found: {root}')
    output = (a.output or root / 'IMMSS_Pooled_Results').expanduser().resolve()
    if output == root:
        p.error('Output directory must not equal input root')
    print('Scanning reports...', flush=True)
    sessions, bad, nfiles = read_reports(root, output)
    agg = pooled(sessions)
    output.mkdir(parents=True, exist_ok=True)
    write_outputs(sessions, agg, output, bad, nfiles)
    print('Rendering pooled paper-ready figure...', flush=True)
    create_figure(sessions, agg, output, transparent=a.transparent)
    print(f"Done. {len(sessions)} sessions; mean overlap={agg['overlap_pct']['mean']:.2f}%; mean rate compliance={agg['rate_compliance_pct']['mean']:.2f}%", flush=True)
    print('Results:', output, flush=True)
    return agg


if __name__ == '__main__':
    main()
