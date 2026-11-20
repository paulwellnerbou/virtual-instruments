"""The instrument's equalizer: bands shaped in the builder, each band's gain a knob in DecentSampler.

DecentSampler plays the bands as its own filter effects. The same biquads (the audio EQ cookbook's,
which DecentSampler's are taken to be) draw the builder's curve, play it in the browser and go into
the soundfont, which has no effects of its own.
"""

import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf
from scipy.signal import sosfilt, sosfreqz

if TYPE_CHECKING:
    from vinst.spec import EqBand

GAIN_TYPES = ("peak", "low_shelf", "high_shelf")
TYPES = {"low_shelf": "Low shelf", "peak": "Peak", "high_shelf": "High shelf", "highpass": "Low cut",
         "lowpass": "High cut", "notch": "Notch"}
KNOB_DB = 12.0  # a gain knob turns at least this far either way


def has_gain(band: "EqBand") -> bool:
    return band.type in GAIN_TYPES


def where(band: "EqBand") -> str:
    f = band.freq
    return f"{f / 1000:.3g} kHz" if f >= 1000 else f"{f:.0f} Hz"


def label(band: "EqBand") -> str:
    """The band's name, or else where it is."""
    return band.name.strip() or where(band)


def knob_range(band: "EqBand") -> float:
    """How far the band's knob turns either way, in dB: far enough to reach its gain."""
    return max(KNOB_DB, math.ceil(abs(band.gain_db) / 6) * 6)


def gain_table(span: float) -> str:
    """DecentSampler translationTable from the knob's dB to the filter's linear gain, in 1 dB steps."""
    n = int(span)
    return ";".join(f"{d},{10 ** (d / 20):.5f}" for d in range(-n, n + 1))


def biquad(kind: str, freq: float, gain_db: float, q: float, sr: float) -> list[float]:
    """One second-order section [b0, b1, b2, 1, a1, a2], from the Audio EQ Cookbook; `gain_db` counts
    for the peak and the shelves only."""
    w = 2 * math.pi * freq / sr
    cw, sw = math.cos(w), math.sin(w)
    alpha = sw / (2 * q)
    a = 10 ** (gain_db / 40)
    if kind == "peak":
        b, aa = [1 + alpha * a, -2 * cw, 1 - alpha * a], [1 + alpha / a, -2 * cw, 1 - alpha / a]
    elif kind == "low_shelf":
        k = 2 * math.sqrt(a) * alpha
        b = [a * ((a + 1) - (a - 1) * cw + k), 2 * a * ((a - 1) - (a + 1) * cw), a * ((a + 1) - (a - 1) * cw - k)]
        aa = [(a + 1) + (a - 1) * cw + k, -2 * ((a - 1) + (a + 1) * cw), (a + 1) + (a - 1) * cw - k]
    elif kind == "high_shelf":
        k = 2 * math.sqrt(a) * alpha
        b = [a * ((a + 1) + (a - 1) * cw + k), -2 * a * ((a - 1) + (a + 1) * cw), a * ((a + 1) + (a - 1) * cw - k)]
        aa = [(a + 1) - (a - 1) * cw + k, 2 * ((a - 1) - (a + 1) * cw), (a + 1) - (a - 1) * cw - k]
    elif kind == "highpass":
        b, aa = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2], [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "lowpass":
        b, aa = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2], [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "notch":
        b, aa = [1, -2 * cw, 1], [1 + alpha, -2 * cw, 1 - alpha]
    else:
        raise ValueError(f"Unknown filter kind {kind!r}")
    return [*(x / aa[0] for x in b), 1.0, aa[1] / aa[0], aa[2] / aa[0]]


def coefficients(band: "EqBand", sr: float) -> np.ndarray:
    """`band`'s section at `sr`; a band too close to half the sample rate plays at its edge."""
    return np.array(biquad(band.type, min(band.freq, sr * 0.49), band.gain_db, band.q, sr))


def sections(bands: list["EqBand"], sr: float) -> np.ndarray:
    return np.array([coefficients(b, sr) for b in bands if b.on]).reshape(-1, 6)


def response_db(bands: list["EqBand"], freqs: np.ndarray, sr: float = 48000) -> np.ndarray:
    """The bands' combined gain at `freqs`, in dB."""
    sos = sections(bands, sr)
    if not len(sos):
        return np.zeros(len(freqs))
    _, h = sosfreqz(sos, worN=np.asarray(freqs, dtype=float), fs=sr)
    return 20 * np.log10(np.maximum(np.abs(h), 1e-12))


def apply(audio: np.ndarray, sr: float, bands: list["EqBand"]) -> np.ndarray:
    """`audio` (frames first) through the bands, forwards only, as a player's filters run."""
    sos = sections(bands, sr)
    return sosfilt(sos, audio, axis=0) if len(sos) else audio


SPECTRUM_FILES = 48
SPECTRUM_SECONDS = 2.0
SPECTRUM_POINTS = 160


def spectrum(files: list[Path]) -> list[tuple[float, float]]:
    """(frequency, dB below the loudest) of the files' average spectrum, on a log scale from 20 Hz to
    20 kHz: what the EQ is shaping. Spread over the files so low and high notes both count."""
    picked = files if len(files) <= SPECTRUM_FILES else [files[round(i * (len(files) - 1) / (SPECTRUM_FILES - 1))]
                                                         for i in range(SPECTRUM_FILES)]
    edges = np.geomspace(20, 20000, SPECTRUM_POINTS + 1)
    total = np.zeros(SPECTRUM_POINTS)
    count = 0
    for path in picked:
        try:
            sr = sf.info(str(path)).samplerate
            audio, _ = sf.read(str(path), always_2d=True, dtype="float64", frames=int(SPECTRUM_SECONDS * sr))
        except (OSError, RuntimeError):
            continue
        x = audio.mean(axis=1)
        if len(x) < 256:
            continue
        power = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
        freqs = np.fft.rfftfreq(len(x), 1 / sr)
        idx = np.searchsorted(edges, freqs) - 1
        ok = (idx >= 0) & (idx < SPECTRUM_POINTS)
        sums = np.bincount(idx[ok], weights=power[ok], minlength=SPECTRUM_POINTS)
        hits = np.bincount(idx[ok], minlength=SPECTRUM_POINTS)
        # Each file weighs the same, however loud it is.
        band = np.where(hits > 0, sums / np.maximum(hits, 1), np.nan)
        peak = np.nanmax(band)
        if peak > 0:
            total += np.nan_to_num(band / peak)
            count += 1
    if not count:
        return []
    db = 10 * np.log10(np.maximum(total / count, 1e-12))
    db -= db.max()
    centres = np.sqrt(edges[:-1] * edges[1:])
    # Short samples leave the lowest bins without a single frequency of their own.
    return [(round(float(f), 1), round(float(d), 1)) for f, d, t in zip(centres, db, total) if t > 0]
