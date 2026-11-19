"""Lowering what lies below a note's own pitch, e.g. the thump of a pluck on a ukulele's body.

A plucked string sounds at its pitch and above; what's below in its first moments is the pluck
exciting the body. A fixed filter can't separate the two over a whole range, as the thump of a high
note lies where the pitch of a low one is, so the cut follows each note.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt

if TYPE_CHECKING:
    from vinst.spec import Instrument

ORDER = 6  # steep, so the note's pitch just above the cut is left alone (< 0.3 dB at 0.75×)


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
    cut = frequency(midi, below)
    if reduction_db <= 0 or cut >= sr / 2 * 0.9 or len(audio) < 64:
        return audio
    sos = butter(ORDER, cut, btype="lowpass", fs=sr, output="sos")
    low = sosfiltfilt(sos, audio, axis=0)
    return audio - (1 - 10 ** (-reduction_db / 20)) * low


def loudness_change(path: Path, midi: float, below: float, reduction_db: float) -> tuple[float, float]:
    """How much the cut lowers a sample's loudness, in dB: over its attack and over its held part
    (as slicing measures them), so evening out and the level can work with what's played."""
    from vinst.slicing import LOUDNESS_WINDOW_S, body_level, db

    audio, sr = sf.read(str(path), always_2d=True, dtype="float64")
    x = audio[:, 0]
    y = reduce(x, sr, midi, below, reduction_db)
    n = int(LOUDNESS_WINDOW_S * sr)

    def attack(z: np.ndarray) -> float:
        return float(db(np.sqrt(np.mean(z[:n] ** 2))))

    return round(attack(y) - attack(x), 2), round(body_level(y, sr) - body_level(x, sr), 2)
