"""Pure visualization helpers, independent of Tkinter and hardware."""

def aged_points(samples, now, seconds=5.0):
    """Return (age_seconds, amplitude) pairs, oldest first, newest at x=0.

    Samples are (LSL sample timestamp, value) pairs. The plot uses xlim(seconds, 0).
    """
    if seconds <= 0:
        raise ValueError('seconds must be positive')
    return [(now - t, y) for t, y in samples if 0 <= now - t <= seconds]
