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


def limiter_gain(y: np.ndarray, sr: int, ceiling_db: float = CEILING_DB) -> np.ndarray:
    """The gain per frame that holds `y`'s peaks under `ceiling_db`."""
    ceiling = 10 ** (ceiling_db / 20)
    level = np.max(np.abs(y), axis=1)
    needed = np.minimum(1.0, ceiling / np.maximum(level, 1e-12))
    if needed.min() >= 1.0:
        return needed
    w = max(1, int(LIMITER_S * sr))
    # The smallest gain needed within w, averaged over w: smooth, and never more than needed anywhere.
    gain = uniform_filter1d(minimum_filter1d(needed, 2 * w + 1), 2 * w + 1)
    return np.minimum(gain, needed)


def write_raised(src: Path, dest: Path, gain_db: float, low_cut: tuple[float, float, float] | None = None,
                 removed_dest: Path | None = None) -> float:
    """Writes `src` raised by `gain_db` and limited to `dest`, in the same format, after the low cut
    (note, share of its pitch, dB; see lowcut) if any; returns the most the limiter took off, in dB.

    With `removed_dest`, what the low cut took out goes there, raised and limited alike, so the two
    played together sound as `src` would."""
    from vinst import lowcut

    info = sf.info(str(src))
    audio, sr = sf.read(str(src), always_2d=True, dtype="float64")
    taken = np.zeros_like(audio)
    if low_cut is not None:
        audio, taken = lowcut.removed(audio, sr, *low_cut)
    factor = 10 ** (gain_db / 20)
    # The knob plays anything from the cut alone to both together; held under the ceiling at both
    # ends, it is everywhere between, as the sum changes linearly with the knob.
    peaks = audio if removed_dest is None else np.hstack([audio, audio + taken])
    gain = limiter_gain(peaks * factor, sr)[:, None]
    _write(np.clip(audio * factor * gain, -1.0, 1.0), sr, info.subtype, info.format, dest)
    if removed_dest is not None:
        _write(np.clip(taken * factor * gain, -1.0, 1.0), sr, info.subtype, info.format, removed_dest)
    return float(-20 * np.log10(gain.min()))


def _write(audio: np.ndarray, sr: int, subtype: str, fmt: str, dest: Path) -> None:
    part = dest.with_name(dest.name + ".part")
    sf.write(str(part), audio, sr, subtype=subtype, format=fmt)
    part.replace(dest)
