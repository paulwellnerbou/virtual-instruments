"""Reverb on send buses: the dry sound untouched, the reverb fed with less of each note's upper partials.

DecentSampler's own reverb turns a pluck's click into a hiss and keeps the bright start of a note
ringing. Instead, every sample also goes to a bus for its pitch band; the bus holds a convolution
reverb whose impulse response is DecentSampler's reverb (same room and damping), or one of your own,
with a high shelf above about three times the band's pitch and a low-pass. The Reverb knob sets the
buses' volume. An own impulse response without that filtering plays on one bus for all samples: a
convolution effect on the instrument would crossfade the dry sound away as the knob goes up.
"""

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, lfilter, resample_poly, sosfilt

IR_DIR = "IRs"
IR_TYPES = (".wav", ".aif", ".aiff", ".flac")  # what an impulse response of your own may be
IR_RATE = 48000
IR_MAX_S = 10.0
IR_FADE_S = 0.5  # an own impulse response longer than IR_MAX_S fades out over this before it's cut
IR_COPY = "reverb-ir"  # the start of the name of an own impulse response's copy in the instrument's folder
DOCS_URL = "https://github.com/paulwellnerbou/virtual-instruments/blob/main/docs/recording-impulse-responses.md"
IR_FLOOR_DB = -70.0  # the tail is cut where what's left is this far below the whole
SHELF_DB = -9.0
SHELF_ABOVE = 2.9  # times the band's pitch: the fundamental stays, the octave a little, the partials above less
LOWPASS_HZ = 5000.0
BAND_SEMITONES = 3
MAX_BUSES = 16  # DecentSampler's limit
# DecentSampler scales every impulse response by this, whatever its level: the files are written louder.
CONVOLUTION_SCALE = 0.25

# JUCE's reverb (a Freeverb), which DecentSampler's reverb effect is: delay lengths at 44.1 kHz.
_COMBS = (1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617)
_ALLPASSES = (556, 441, 341, 225)
_STEREO_SPREAD = 23
_INPUT_GAIN = 0.015
_WET_SCALE = 3.0 * 10 ** (-6 / 20)  # JUCE's wet scale, less the 6 dB DecentSampler takes off (measured)


def _comb(x: np.ndarray, n: int, feedback: float, damp: float) -> np.ndarray:
    """A feedback comb of delay `n` with a one-pole low-pass in its loop; a block of `n` at a time,
    as each block only needs the one before."""
    out = np.zeros_like(x)
    prev = np.zeros(n)  # what went into the delay one block ago
    zi = np.zeros(1)
    for start in range(0, x.size, n):
        o = prev[: min(n, x.size - start)]
        low, zi = lfilter([1 - damp], [1, -damp], o, zi=zi)
        out[start:start + o.size] = o
        prev = x[start:start + o.size] + feedback * low
    return out


def _allpass(x: np.ndarray, n: int) -> np.ndarray:
    out = np.zeros_like(x)
    prev = np.zeros(n)
    for start in range(0, x.size, n):
        o = prev[: min(n, x.size - start)]
        inp = x[start:start + o.size]
        out[start:start + o.size] = o - inp
        prev = inp + 0.5 * o
    return out


def builtin_reverb_ir(room: float, damping: float, seconds: float, rate: int = IR_RATE) -> np.ndarray:
    """(2, frames): what DecentSampler's reverb at full wet level adds per unit of a centred mono signal."""
    feedback, damp = room * 0.28 + 0.7, damping * 0.4
    x = np.zeros(int(seconds * rate)); x[0] = 2 * _INPUT_GAIN  # left + right in
    out = []
    for spread in (0, _STEREO_SPREAD):
        y = sum(_comb(x, rate * (t + spread) // 44100, feedback, damp) for t in _COMBS)
        for t in _ALLPASSES:
            y = _allpass(y, rate * (t + spread) // 44100)
        out.append(y)
    return _WET_SCALE * np.array(out)


@dataclass(frozen=True)
class Band:
    """Pitches (MIDI notes) sharing a bus; their reverb's shelf sits above the middle one."""

    lo: int
    hi: int

    @property
    def shelf_hz(self) -> float:
        return SHELF_ABOVE * 440.0 * 2 ** (((self.lo + self.hi) / 2 - 69) / 12)


def sample_pitch(lo: int, hi: int) -> int:
    """The pitch a sample's reverb is filtered for: the middle of the keys it plays, not its root,
    as a sample may play well above or below it."""
    return (lo + hi) // 2


def bands(pitches: list[int]) -> list[Band]:
    """Bands of a few semitones over the samples' pitches, wider if more than DecentSampler's buses."""
    pitches = sorted(set(pitches))
    if not pitches:
        return []
    width = max(BAND_SEMITONES, math.ceil((pitches[-1] - pitches[0] + 1) / MAX_BUSES))
    out = []
    for start in range(pitches[0], pitches[-1] + 1, width):
        inside = [r for r in pitches if start <= r < start + width]
        if inside:
            out.append(Band(inside[0], inside[-1]))
    return out


def band_of(pitch: int, bands: list[Band]) -> int:
    """The band a sample's reverb goes through: its own, else the nearest (extras, release noises)."""
    return min(range(len(bands)), key=lambda i: 0 if bands[i].lo <= pitch <= bands[i].hi
               else min(abs(pitch - bands[i].lo), abs(pitch - bands[i].hi)))


def ir_path(index: int) -> str:
    return f"{IR_DIR}/reverb-{index + 1}.wav"


def _shelf(x: np.ndarray, freq: float, gain_db: float, rate: int) -> np.ndarray:
    low = sosfilt(butter(2, min(freq, 0.45 * rate), "lowpass", fs=rate, output="sos"), x, axis=-1)
    return low + (x - low) * 10 ** (gain_db / 20)


def _trimmed(ir: np.ndarray) -> np.ndarray:
    energy = np.sum(ir ** 2, axis=0)[::-1].cumsum()[::-1]  # what's left from each frame on
    keep = np.flatnonzero(energy > energy[0] * 10 ** (IR_FLOOR_DB / 10))
    return ir[:, : (keep[-1] + 1 if keep.size else 1)]


def band_ir(base: np.ndarray, band: Band | None, rate: int = IR_RATE) -> np.ndarray:
    """(frames, 2) float32: the reverb of `base` (see builtin_reverb_ir) through the band's shelf and the
    low-pass; without a band, unfiltered."""
    ir = base
    if band is not None:
        ir = _shelf(ir, band.shelf_hz, SHELF_DB, rate)
        ir = sosfilt(butter(4, LOWPASS_HZ, "lowpass", fs=rate, output="sos"), ir, axis=-1)
    return (_trimmed(ir) / CONVOLUTION_SCALE).T.astype(np.float32)


def own_ir(path: Path) -> np.ndarray:
    """(2, frames): an impulse response file, stereo at IR_RATE."""
    try:
        data, rate = sf.read(str(path), always_2d=True, dtype="float64")
    except (RuntimeError, sf.LibsndfileError) as err:
        raise ValueError(f"Can't read the impulse response {path.name}: {err}") from err
    data = (np.repeat(data, 2, axis=1) if data.shape[1] == 1 else data[:, :2]).T
    if rate != IR_RATE:
        g = math.gcd(int(rate), IR_RATE)
        data = resample_poly(data, IR_RATE // g, int(rate) // g, axis=-1)
    if not np.any(data):
        raise ValueError(f"The impulse response {path.name} is silent.")
    # A tail sinking into room noise never gets trimmed, and every second of it costs size and CPU per band.
    limit = int(IR_MAX_S * IR_RATE)
    if data.shape[1] > limit:
        fade = int(IR_FADE_S * IR_RATE)
        data = data[:, :limit].copy()
        data[:, -fade:] *= np.linspace(1.0, 0.0, fade)
    return data


def too_long(path: Path) -> bool:
    """Whether an impulse response file is cut to IR_MAX_S when building."""
    return sf.info(str(path)).duration > IR_MAX_S


def _energy(ir: np.ndarray) -> float:
    return float(np.sum(ir.astype(np.float64) ** 2))


def write_irs(bundle: Path, bands: list[Band], room: float, damping: float, own: Path | None = None,
              filtered: bool = True) -> list[str]:
    """Writes one impulse response per band into the bundle, from DecentSampler's reverb with this room and
    damping or from an own impulse response, `filtered` per band or not; returns their paths inside it.
    An own one is made as loud as DecentSampler's after the same filtering, so the knob sets the same
    amount whichever plays."""
    folder = bundle / IR_DIR
    folder.mkdir(parents=True, exist_ok=True)
    builtin = builtin_reverb_ir(room, damping, IR_MAX_S)
    base = own_ir(own) if own else builtin
    paths = [ir_path(i) for i in range(len(bands))]
    for path, band in zip(paths, bands):
        ir = band_ir(base, band if filtered else None)
        if own:
            ir *= math.sqrt(_energy(band_ir(builtin, band if filtered else None)) / _energy(ir))
        sf.write(bundle / path, ir, IR_RATE, subtype="PCM_24")
    for old in folder.glob("reverb-*.wav"):
        if f"{IR_DIR}/{old.name}" not in paths:
            old.unlink()
    return paths
