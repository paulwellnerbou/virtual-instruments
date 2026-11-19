"""Making quiet recordings play louder without clipping.

A plucked note's peak is a click of a millisecond or less, often 10 dB above the note itself, so
raising the level until that click reaches full scale leaves the note quiet. Here the level is
raised as far as the note needs, and the clicks are held under a ceiling by a limiter too short to
hear on them.
"""

from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.ndimage import minimum_filter1d, uniform_filter1d

CEILING_DB = -1.0
LIMITER_S = 0.002  # how far ahead and behind the limiter looks; also how fast it lets go


def limit(audio: np.ndarray, sr: int, gain_db: float, ceiling_db: float = CEILING_DB) -> tuple[np.ndarray, float]:
    """`audio` (frames × channels) raised by `gain_db`, with peaks held under `ceiling_db`; and the most
    the limiter took off, in dB."""
    y = audio * 10 ** (gain_db / 20)
    ceiling = 10 ** (ceiling_db / 20)
    level = np.max(np.abs(y), axis=1)
    needed = np.minimum(1.0, ceiling / np.maximum(level, 1e-12))
    if needed.min() >= 1.0:
        return y, 0.0
    w = max(1, int(LIMITER_S * sr))
    # The smallest gain needed within w, averaged over w: smooth, and never more than needed anywhere.
    gain = uniform_filter1d(minimum_filter1d(needed, 2 * w + 1), 2 * w + 1)
    gain = np.minimum(gain, needed)
    return np.clip(y * gain[:, None], -1.0, 1.0), float(-20 * np.log10(gain.min()))


def write_raised(src: Path, dest: Path, gain_db: float) -> float:
    """Writes `src` raised by `gain_db` and limited to `dest`, in the same format; returns the most the
    limiter took off, in dB."""
    info = sf.info(str(src))
    audio, sr = sf.read(str(src), always_2d=True, dtype="float64")
    out, reduced = limit(audio, sr, gain_db)
    part = dest.with_name(dest.name + ".part")
    sf.write(str(part), out, sr, subtype=info.subtype, format=info.format)
    part.replace(dest)
    return reduced
