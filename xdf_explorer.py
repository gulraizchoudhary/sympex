# -*- coding: utf-8 -*-
"""
Created on Mon Nov  2 18:59:30 2026

@author: chgul
"""


import pyxdf
import numpy as np
from pathlib import Path

# Change this to your XDF file path
XDF_FILE = r"C:\YourFolder\recording.xdf"

def explore_xdf(file_path):
    print("\n" + "=" * 75)
    print("XDF FILE EXPLORER")
    print("=" * 75)
    print("File:", Path(file_path).name)

    streams, file_header = pyxdf.load_xdf(file_path)

    print(f"\nTotal streams: {len(streams)}")

    for i, stream in enumerate(streams, 1):
        info = stream["info"]
        timestamps = np.asarray(
            stream["time_stamps"], dtype=float
        )
        values = stream["time_series"]

        def get_info(key, default="Unknown"):
            v = info.get(key, [default])
            return v[0] if v else default

        print("\n" + "-" * 75)
        print(f"STREAM {i}")
        print("-" * 75)

        print("Name:", get_info("name"))
        print("Type:", get_info("type"))
        print("Source ID:", get_info("source_id"))
        print("Hostname:", get_info("hostname"))
        print("Channel count:", get_info("channel_count"))
        print("Nominal rate:", get_info("nominal_srate"), "Hz")
        print("Sample count:", len(timestamps))
        print("Data shape:", np.shape(values))

        # Timestamp information
        if len(timestamps):
            print("\nTIMESTAMPS")
            print("First:", timestamps[0])
            print("Last:", timestamps[-1])
            print(
                "Duration:",
                round(timestamps[-1] - timestamps[0], 4),
                "seconds"
            )

            if len(timestamps) > 1:
                dt = np.diff(timestamps)
                positive = dt[dt > 0]

                print("Nonpositive intervals:", np.sum(dt <= 0))

                if len(positive):
                    print("Median interval:", np.median(positive))
                    print(
                        "Observed rate:",
                        round(1 / np.median(positive), 3),
                        "Hz"
                    )

        # Channel information
        print("\nCHANNEL METADATA")
        desc = info.get("desc", [{}])

        try:
            channels = (
                desc[0]
                .get("channels", [{}])[0]
                .get("channel", [])
            )

            for j, ch in enumerate(channels):
                label = ch.get("label", [f"Channel {j+1}"])[0]
                unit = ch.get("unit", [""])[0]
                print(f"  {j+1}: {label} {unit}")

        except (KeyError, IndexError, TypeError):
            print("No channel metadata available")

        # First samples
        print("\nFIRST 5 SAMPLES")
        for sample, ts in zip(values[:5], timestamps[:5]):
            print(f"LSL {ts:.6f}: {sample}")

    print("\n" + "=" * 75)
    print("EXPLORATION COMPLETE")
    print("=" * 75)


if __name__ == "__main__":
    base_address = "C:/Users/chgul/Desktop/Sympex_KUH_tympanstomy_04-11-2026/Combined_Matti_sessions"
    explore_xdf(base_address + "/sub-MAtti4_ses-S001_task-Default_run-001_eeg_old2.xdf")
