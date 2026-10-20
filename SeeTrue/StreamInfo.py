# -*- coding: utf-8 -*-
"""
Created on Mon Oct 19 11:07:45 2026

@author: chgul
"""


from pylsl import StreamInlet, resolve_streams

streams = resolve_streams(wait_time=5.0)

for stream in streams:

    if stream.name() not in (
        "EyeData", "DebugData", "EyeEvents"
    ):
        continue

    inlet = StreamInlet(stream)

    try:
        info = inlet.info(timeout=5.0)

        print("\n" + "=" * 65)
        print("STREAM:", info.name())
        print("TYPE:", info.type())
        print("RATE:", info.nominal_srate())
        print("CHANNELS:", info.channel_count())
        print("=" * 65)

        channel = (
            info.desc()
            .child("channels")
            .child("channel")
        )

        labels = []

        for i in range(info.channel_count()):

            label = (
                channel.child_value("label")
                if not channel.empty()
                else ""
            )

            labels.append(label or f"Channel_{i+1}")

            if not channel.empty():
                channel = channel.next_sibling("channel")

        # Display one actual sample
        sample, timestamp = inlet.pull_sample(
            timeout=3.0
        )

        print("\nCHANNEL MAPPING:")

        for i, label in enumerate(labels):
            value = (
                sample[i]
                if sample is not None
                else "No sample received"
            )

            print(f"{i+1:02d}. {label}: {value}")

        if sample is not None:
            print("\nLSL timestamp:", timestamp)

    finally:
        inlet.close_stream()
