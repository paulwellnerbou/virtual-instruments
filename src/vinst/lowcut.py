"""Lowering what lies below a note's own pitch, e.g. the thump of a pluck on a ukulele's body.

A plucked string sounds at its pitch and above; what's below in its first moments is the pluck
exciting the body. A fixed filter can't separate the two over a whole range, as the thump of a high
note lies where the pitch of a low one is, so the cut follows each note.
"""

import numpy as np
from scipy.signal import butter, sosfiltfilt

ORDER = 6  # steep, so the note's pitch just above the cut is left alone (< 0.3 dB at 0.75×)


def frequency(midi: float, below: float) -> float:
    """Where the cut starts: `below` times the note's pitch."""
    return below * 440.0 * 2 ** ((midi - 69) / 12)


def reduce(audio: np.ndarray, sr: int, midi: float, below: float, reduction_db: float) -> np.ndarray:
    """`audio` with what lies below `below` times the note's pitch lowered by `reduction_db`.

    The low part is taken out without shifting any phase (filtered forwards and backwards) and
    subtracted in part, so above the cut the sample is unchanged and below it lowered by exactly the
    amount, not removed.
    """
    cut = frequency(midi, below)
    if reduction_db <= 0 or cut >= sr / 2 * 0.9 or len(audio) < 64:
        return audio
    sos = butter(ORDER, cut, btype="lowpass", fs=sr, output="sos")
    low = sosfiltfilt(sos, audio, axis=0)
    return audio - (1 - 10 ** (-reduction_db / 20)) * low
