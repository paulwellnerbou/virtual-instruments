"""Lowering what lies below a note's own pitch, e.g. a ukulele body's low resonance after a pluck.

A plucked string sounds at its pitch and above; what's below in its first moments is the pluck
exciting the body. A fixed filter can't separate the two over a whole range, as the resonance under a high
note lies where the pitch of a low one is, so the cut follows each note.
"""

from typing import TYPE_CHECKING

import numpy as np
from scipy.signal import butter, sosfiltfilt

if TYPE_CHECKING:
    from vinst.spec import Instrument

ORDER = 6  # steep, so the note's pitch just above the cut is left alone (< 0.3 dB at 0.75×)
# How much of the cut loudness is evened out for, and the soundfont has: the Low cut knob at 50 %, so
# neither end of the knob is far from even.
EVEN_SHARE = 0.5


def applies(inst: "Instrument") -> bool:
    """Whether the instrument's notes have a pitch to cut below: not drum-kit style sounds, which only
    have a key, nor a single sample, whose key says nothing about its pitch."""
    from vinst.spec import InstrumentType

    return inst.range.mode != "sounds" and inst.meta.type != InstrumentType.SINGLE_SAMPLE


def frequency(midi: float, below: float) -> float:
    """Where the cut starts: `below` times the note's pitch."""
    return below * 440.0 * 2 ** ((midi - 69) / 12)


def reduce(audio: np.ndarray, sr: int, midi: float, below: float, reduction_db: float) -> np.ndarray:
    """`audio` with what lies below `below` times the note's pitch lowered by `reduction_db`.

    The low part is taken out without shifting any phase (filtered forwards and backwards) and
    subtracted in part, so above the cut the sample is unchanged and below it lowered by exactly the
    amount, not removed.
    """
    return removed(audio, sr, midi, below, reduction_db)[0]


def removed(audio: np.ndarray, sr: int, midi: float, below: float, reduction_db: float) -> tuple[np.ndarray, np.ndarray]:
    """(`audio` after the cut, what the cut took out), adding up to `audio` exactly: so the second,
    played along with the first at any volume, blends between the cut and the uncut sample."""
    cut = frequency(midi, below)
    if reduction_db <= 0 or cut >= sr / 2 * 0.9 or len(audio) < 64:
        return audio, np.zeros_like(audio)
    sos = butter(ORDER, cut, btype="lowpass", fs=sr, output="sos")
    taken = (1 - 10 ** (-reduction_db / 20)) * sosfiltfilt(sos, audio, axis=0)
    return audio - taken, taken


def curve(below: float, reduction_db: float, shares: list[float]) -> list[float]:
    """How much quieter the cut makes each frequency, in dB (0 or less), at `shares` of the note's
    pitch; the same for every note, as the cut follows the pitch."""
    from scipy.signal import sosfreqz

    sr, pitch = 48000, 1000.0
    sos = butter(ORDER, below * pitch, btype="lowpass", fs=sr, output="sos")
    _, h = sosfreqz(sos, worN=[x * pitch for x in shares], fs=sr)
    gain = 1 - (1 - 10 ** (-reduction_db / 20)) * np.abs(h) ** 2  # filtered both ways: |H|²
    return [float(20 * np.log10(max(g, 1e-6))) for g in gain]

