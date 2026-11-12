"""Export SeeTrue EyeData from an XDF recording to CSV.

Usage:
    pip install pyxdf numpy
    python export_eyedata_xdf.py
    python export_eyedata_xdf.py "C:\\path\\to\\recording.xdf"

The XDF's corrected LSL timestamps are exported separately from the vendor
`timeStamp` field. Only EyeData streams are decoded, avoiding memory-intensive
loading of audio streams in the same XDF.
"""

import argparse
import csv
from pathlib import Path

import numpy as np

DEFAULT_XDF = Path(
    r"C:/Users/chgul/Desktop/Sympex_KUH_tympanstomy_04-11-2026/Combined_Matti_sessions"
    r"/sub-MAtti4_ses-S001_task-Default_run-001_eeg_old2.xdf"
)

DEFAULT_COLUMNS = [
    "frameNumber",
    "timeStamp",  # SeeTrue device timestamp (milliseconds)
    "scenePictureNumber",
    "PoR.x",
    "PoR.y",
    "gazePoint.x",
    "gazePoint.y",
    "pupilAreaInMM2Right",
    "pupilAreaInMM2Left",
    "pupilAreaInPixelsRight",
    "pupilAreaInPixelsLeft",
    "score",
    "scoreRight",
    "scoreLeft",
]


def first_value(container, key, default=None):
    value = container.get(key, [default])
    if isinstance(value, (list, tuple)):
        return value[0] if value else default
    return value


def channel_labels(info, count):
    """Read channel names from XDF metadata, falling back by channel index."""
    try:
        metadata = first_value(info, "desc", {}) or {}
        channels_container = first_value(metadata, "channels", {}) or {}
        channels = channels_container.get("channel", [])
        if isinstance(channels, dict):
            channels = [channels]
    except (TypeError, AttributeError):
        channels = []

    labels = []
    for index in range(count):
        fallback = (
            DEFAULT_COLUMNS[index]
            if index < len(DEFAULT_COLUMNS)
            else f"channel_{index + 1}"
        )
        try:
            label = first_value(channels[index], "label", fallback) or fallback
        except (IndexError, TypeError, AttributeError):
            label = fallback
        label = str(label)
        # CSV column names need to be distinct, even with faulty device metadata.
        if label in labels:
            label = f"{label}_{index + 1}"
        labels.append(label)
    return labels


def export_eye_stream(stream, destination):
    info = stream["info"]
    timestamps = np.asarray(stream["time_stamps"], dtype=np.float64)
    samples = np.asarray(stream["time_series"])
    if samples.ndim == 1 and samples.size == 0:
        raise ValueError("EyeData stream contains no samples")
    if samples.ndim != 2:
        raise ValueError(f"Unexpected EyeData shape: {samples.shape}")
    if samples.shape[0] != len(timestamps):
        raise ValueError("EyeData sample count does not match timestamp count")

    labels = channel_labels(info, samples.shape[1])
    origin = float(timestamps[0])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["lsl_timestamp_s", "relative_time_s", *labels])
        for timestamp, values in zip(timestamps, samples):
            writer.writerow([
                f"{float(timestamp):.9f}",
                f"{float(timestamp - origin):.9f}",
                *values.tolist(),
            ])

    print(f"Saved: {destination}")
    print(f"  Stream: {first_value(info, 'name')}  ({first_value(info, 'source_id')})")
    print(f"  Rows: {len(timestamps):,}; channels: {samples.shape[1]}")
    print(f"  LSL interval: {timestamps[0]:.6f} – {timestamps[-1]:.6f} s")
    if "scenePictureNumber" in labels:
        col = labels.index("scenePictureNumber")
        print("  First five scene-picture IDs:", samples[:5, col].tolist())
    return destination


def export_from_xdf(xdf_path, output_path=None):
    try:
        import pyxdf
    except ImportError as exc:
        raise RuntimeError("Install pyxdf first: pip install pyxdf numpy") from exc

    xdf_path = Path(xdf_path)
    if not xdf_path.is_file():
        raise FileNotFoundError(f"XDF file not found: {xdf_path}")

    print("Reading EyeData only (skipping audio, cameras, etc.)...")
    streams, _ = pyxdf.load_xdf(
        str(xdf_path),
        select_streams=[{"name": "EyeData"}],
        synchronize_clocks=True,
        dejitter_timestamps=True,
    )
    if not streams:
        raise ValueError("No EyeData stream found in this XDF")

    outputs = []
    for number, stream in enumerate(streams, start=1):
        if output_path and len(streams) == 1:
            destination = Path(output_path)
        else:
            suffix = f"_{number}" if len(streams) > 1 else ""
            destination = xdf_path.with_name(f"{xdf_path.stem}_EyeData{suffix}.csv")
        outputs.append(export_eye_stream(stream, destination))
    return outputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export SeeTrue EyeData from XDF to CSV")
    parser.add_argument("xdf", nargs="?", default=str(DEFAULT_XDF), help="XDF file path")
    parser.add_argument("--output", help="Destination CSV path (single EyeData stream only)")
    args = parser.parse_args()
    try:
        export_from_xdf(args.xdf, args.output)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
