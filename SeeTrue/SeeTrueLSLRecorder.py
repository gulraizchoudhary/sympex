
# -*- coding: utf-8 -*-

"""
SeeTrue Eye Tracker - Independent LSL CSV Recorder

Features:
- Connects to SeeTrue EyeData LSL stream
- Starts recording immediately
- Automatically increments CSV recording number
- Saves original LSL timestamps
- Uses SeeTrue channel labels when available
- Does not require SeeTrue's recording button
- Stops using Ctrl+C
"""

import csv
import re
from pathlib import Path

from pylsl import StreamInlet, resolve_byprop


class SeeTrueLSLRecorder:

    def __init__(
        self,
        user_data_dir,
        user_name
    ):

        self.user_dir = (
            Path(user_data_dir) / user_name
        )

        self.user_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.inlet = None
        self.output_path = None
        self.sample_count = 0

    def get_next_session_index(self):
        """
        Find the next available recording index.

        Existing recordings are never overwritten.
        """

        indices = set()

        for path in self.user_dir.iterdir():

            match = re.fullmatch(
                r"EyeData_LSL_(\d+)\.csv",
                path.name
            )

            if match:
                indices.add(int(match.group(1)))

        index = 0

        while index in indices:
            index += 1

        return index

    def get_channel_labels(self, info):

        labels = []

        ch = (
            info.desc()
            .child("channels")
            .child("channel")
        )

        for i in range(info.channel_count()):

            label = ""

            if not ch.empty():
                label = ch.child_value("label")
                ch = ch.next_sibling("channel")

            labels.append(
                label or f"Channel_{i + 1}"
            )

        return labels

    def connect(self):

        print("Searching for SeeTrue EyeData stream...")

        streams = resolve_byprop(
            "name",
            "EyeData",
            timeout=5.0
        )

        if not streams:
            raise RuntimeError(
                "SeeTrue EyeData LSL stream not found."
            )

        if len(streams) > 1:
            raise RuntimeError(
                "Multiple EyeData LSL streams found."
            )

        self.inlet = StreamInlet(
            streams[0],
            max_buflen=120,
            recover=True
        )

        info = self.inlet.info(timeout=5.0)

        print("\nConnected successfully!")
        print("Stream:", info.name())
        print("Channels:", info.channel_count())
        print(
            "Sampling rate:",
            info.nominal_srate(),
            "Hz"
        )

        return info

    def record(self):

        try:

            # Connect to LSL
            info = self.connect()

            # Automatically select recording number
            index = self.get_next_session_index()

            self.output_path = (
                self.user_dir /
                f"EyeData_LSL_{index}.csv"
            )

            labels = self.get_channel_labels(info)

            print("\nRecording index:", index)
            print("Output:", self.output_path)

            with self.output_path.open(
                "x",
                newline="",
                encoding="utf-8"
            ) as file:

                writer = csv.writer(
                    file,
                    delimiter=";"
                )

                writer.writerow(
                    ["LSL_timestamp"] + labels
                )

                file.flush()

                print("\nRecording started!")
                print("Press Ctrl+C to stop.\n")

                while True:

                    sample, timestamp = (
                        self.inlet.pull_sample(
                            timeout=0.2
                        )
                    )

                    if sample is None:
                        continue

                    writer.writerow(
                        [f"{timestamp:.9f}"] +
                        list(sample)
                    )

                    self.sample_count += 1

                    # Flush every 50 samples
                    if self.sample_count % 50 == 0:
                        file.flush()

                    # Print status every 500 samples
                    if self.sample_count % 500 == 0:
                        print(
                            f"Recorded "
                            f"{self.sample_count} samples"
                        )

        except KeyboardInterrupt:

            print("\nRecording stopped by user.")

        finally:

            if self.inlet is not None:
                self.inlet.close_stream()

            print(
                "\nTotal samples:",
                self.sample_count
            )

            if self.output_path is not None:
                print(
                    "Saved to:",
                    self.output_path
                )


if __name__ == "__main__":

    recorder = SeeTrueLSLRecorder(
        user_data_dir=r"C:\Users\chgul\Documents",
        user_name="New Test"
    )

    recorder.record()
