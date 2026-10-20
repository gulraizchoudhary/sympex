# -*- coding: utf-8 -*-
"""
Created on Mon Oct 19 09:57:25 2026

@author: chgul
"""


# SeeTrue Eye Tracker - LSL Example
# Updated for newer pylsl versions

from pylsl import StreamInlet, resolve_byprop
import os

if os.name == "nt":
    import msvcrt


def getInfo(res):

    if not res:
        print("Stream not found.")
        return

    inlet = StreamInlet(res[0])

    try:
        inf = inlet.info(timeout=5.0)

        print(
            "=== STREAM: %s ===\n" % inf.name()
        )

        print(
            "Manufacturer:",
            inf.desc().child_value("manufacturer")
        )
        print("Channels:", inf.channel_count())
        print("Format:", inf.channel_format())
        print("Sampling rate:", inf.nominal_srate())
        print("Source device:", inf.source_id())
        print("Version:", inf.version())

        print("\nChannel labels:")

        ch = (
            inf.desc()
            .child("channels")
            .child("channel")
        )

        for k in range(inf.channel_count()):

            if ch.empty():
                break

            print(
                f"Channel {k + 1}: "
                f"{ch.child_value('label')}"
            )

            ch = ch.next_sibling()

        print("=" * 50)

    finally:
        inlet.close_stream()


def listenToContinuousStream(inlet):

    print(
        "Listening to %s" % inlet.info().name()
    )

    print("Press any key in the console to stop.")

    try:
        while True:

            sample, timestamp = inlet.pull_sample(
                timeout=0.5
            )

            if sample is not None:
                print(
                    "%s : %s" % (timestamp, sample)
                )

            if os.name == "nt" and msvcrt.kbhit():
                msvcrt.getch()
                break

    except KeyboardInterrupt:
        print("Stopped by user.")

    finally:
        inlet.close_stream()


def listenToEventStream(inlet):

    print(
        "Listening to %s" % inlet.info().name()
    )

    try:
        while True:

            sample, timestamp = inlet.pull_sample(
                timeout=0.5
            )

            if sample is not None:
                print(
                    "%s : %s" % (
                        timestamp,
                        sample[0]
                    )
                )

            if os.name == "nt" and msvcrt.kbhit():
                msvcrt.getch()
                break

    except KeyboardInterrupt:
        print("Stopped by user.")

    finally:
        inlet.close_stream()


# MAIN PROGRAM

def main():

    print("Searching for SeeTrue LSL streams...")

    res_eyedata = resolve_byprop(
        "name", "EyeData", timeout=5.0
    )

    res_debugdata = resolve_byprop(
        "name", "DebugData", timeout=5.0
    )

    res_eventsdata = resolve_byprop(
        "name", "EyeEvents", timeout=5.0
    )

    streams = {
        "1": ("EyeData", res_eyedata),
        "2": ("DebugData", res_debugdata),
        "3": ("EyeEvents", res_eventsdata)
    }

    for name, result in streams.values():

        if result:
            print(f"\n[CONNECTED] {name}")
            getInfo(result)
        else:
            print(f"\n[NOT FOUND] {name}")

    action = input(
        "\nSelect stream to listen:\n"
        "1 - EyeData\n"
        "2 - DebugData\n"
        "3 - EyeEvents\n"
        "0 - Exit\n"
        "Selection: "
    ).strip()

    if action == "0":
        print("Thanks, bye!")
        return

    if action not in streams:
        print("Invalid selection.")
        return

    name, result = streams[action]

    if not result:
        print(f"{name} is unavailable.")
        return

    inlet = StreamInlet(result[0])

    if action == "3":
        listenToEventStream(inlet)
    else:
        listenToContinuousStream(inlet)

    print("Thanks, bye!")


if __name__ == "__main__":
    main()
