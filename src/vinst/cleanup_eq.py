"""Clean-up EQ: filters that take hum, buzz or a click's ring out of the sliced samples of chosen takes.

Filtered forwards and backwards (zero phase), as the low cut is, so nothing in a sample moves in time.
That squares each filter's response, so bands with a gain are designed with half of it: a peak of -12 dB
is -12 dB, not -24. Highpass and lowpass become 24 dB per octave, -6 dB at their frequency.
The whole take is filtered before it's cut, so a narrow notch has rung in by the time a hit starts.
"""

import json

import numpy as np
from scipy.signal import sosfiltfilt, sosfreqz

from vinst.spec import CleanupEq, EqFilter, Instrument

NYQUIST_SHARE = 0.49  # bands this close to half the sample rate are left out: they'd be unstable

# What a band starts with when its kind is picked: (Hz, gain dB, Q).
STARTS: dict[str, tuple[float, float, float]] = {
    "peak": (1000.0, -12.0, 4.0),
    "notch": (1000.0, 0.0, 10.0),
    "low_shelf": (120.0, -6.0, 0.707),
    "high_shelf": (8000.0, -6.0, 0.707),
    "highpass": (60.0, 0.0, 0.707),
    "lowpass": (16000.0, 0.0, 0.707),
    "hum": (50.0, -30.0, 20.0),
}
KIND_LABELS = {
    "peak": "Peak", "notch": "Notch", "low_shelf": "Low shelf", "high_shelf": "High shelf",
    "highpass": "Highpass", "lowpass": "Lowpass", "hum": "Hum + harmonics",
}
HAS_GAIN = {"peak", "low_shelf", "high_shelf", "hum"}


def _biquad(kind: str, hz: float, gain_db: float, q: float, sr: int) -> list[float]:
    """One second-order section (b0, b1, b2, 1, a1, a2), from the Audio EQ Cookbook."""
    w0 = 2 * np.pi * hz / sr
    cos, alpha = np.cos(w0), np.sin(w0) / (2 * q)
    a = 10 ** (gain_db / 40)
    if kind == "peak":
        b, den = [1 + alpha * a, -2 * cos, 1 - alpha * a], [1 + alpha / a, -2 * cos, 1 - alpha / a]
    elif kind == "notch":
        b, den = [1, -2 * cos, 1], [1 + alpha, -2 * cos, 1 - alpha]
    elif kind == "lowpass":
        b, den = [(1 - cos) / 2, 1 - cos, (1 - cos) / 2], [1 + alpha, -2 * cos, 1 - alpha]
    elif kind == "highpass":
        b, den = [(1 + cos) / 2, -(1 + cos), (1 + cos) / 2], [1 + alpha, -2 * cos, 1 - alpha]
    elif kind in ("low_shelf", "high_shelf"):
        s = 1 if kind == "low_shelf" else -1
        r = 2 * np.sqrt(a) * alpha
        b = [a * ((a + 1) - s * (a - 1) * cos + r), s * 2 * a * ((a - 1) - s * (a + 1) * cos),
             a * ((a + 1) - s * (a - 1) * cos - r)]
        den = [(a + 1) + s * (a - 1) * cos + r, -s * 2 * ((a - 1) + s * (a + 1) * cos),
               (a + 1) + s * (a - 1) * cos - r]
    else:
        raise ValueError(f"unknown filter kind {kind!r}")
    return [b[0] / den[0], b[1] / den[0], b[2] / den[0], 1.0, den[1] / den[0], den[2] / den[0]]


def fits(f: EqFilter, sr: int) -> int:
    """How many of a band's frequencies lie low enough for `sr`: 0 or 1, for hum up to its harmonics."""
    if f.kind == "hum":
        return sum(f.hz * k < NYQUIST_SHARE * sr for k in range(1, f.harmonics + 1))
    return int(f.hz < NYQUIST_SHARE * sr)


def left_out(f: EqFilter, sr: int) -> str:
    """What of a band the sample rate can't hold, in words; "" if all of it fits."""
    n, top = fits(f, sr), round(NYQUIST_SHARE * sr)
    if f.kind == "hum" and 0 < n < f.harmonics:
        return f"Only {n} of the {f.harmonics} lie below {top} Hz; the others are left out."
    return "" if n else f"Above {top} Hz, which {sr / 1000:g} kHz can't hold; left out."


def sections(filters: list[EqFilter], sr: int) -> np.ndarray:
    """All bands as second-order sections, for filtering forwards and backwards."""
    rows = []
    for f in filters:
        if not (f.hz > 0 and f.q > 0 and np.isfinite(f.gain_db)):  # e.g. half typed: no filter, not NaN
            continue
        # Half the gain: filtering twice doubles it.
        if f.kind == "hum":
            rows += [_biquad("peak", f.hz * k, f.gain_db / 2, f.q, sr) for k in range(1, f.harmonics + 1)
                     if f.hz * k < NYQUIST_SHARE * sr]
        elif f.hz < NYQUIST_SHARE * sr:
            rows.append(_biquad(f.kind, f.hz, f.gain_db / 2, f.q, sr))
    return np.array(rows, dtype=float).reshape(-1, 6)


def apply(audio: np.ndarray, sr: int, filters: list[EqFilter]) -> np.ndarray:
    sos = sections(filters, sr)
    if not len(sos) or len(audio) <= 3 * (2 * len(sos) + 1):
        return audio
    return sosfiltfilt(sos, audio, axis=0)


def response_db(filters: list[EqFilter], sr: int, freqs: np.ndarray) -> np.ndarray:
    """What the filters do at `freqs`, in dB, filtered forwards and backwards as `apply` does."""
    sos = sections(filters, sr)
    if not len(sos):
        return np.zeros(len(freqs))
    _, h = sosfreqz(sos, worN=np.asarray(freqs, dtype=float), fs=sr)
    return 2 * 20 * np.log10(np.maximum(np.abs(h), 1e-12))


def shape_profile(profile: np.ndarray, sr: int, filters: list[EqFilter], n_fft: int) -> np.ndarray:
    """A denoise noise profile (power per bin) as it is after the filters, so denoising an EQ'd hit
    doesn't take out hum that the EQ removed already."""
    if not filters:
        return profile
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    return profile * 10 ** (response_db(filters, sr, freqs) / 10)


def applying(inst: Instrument, take_key: str) -> list[CleanupEq]:
    """The clean-up EQs a take uses."""
    return [eq for eq in inst.processing.cleanup_eqs
            if eq.filters and (eq.every_take or take_key in eq.takes)]


def filters_for(inst: Instrument, take_key: str, signal_id: str) -> list[EqFilter]:
    """The bands applied to one signal of a take, in the order of its EQs."""
    return [f for eq in applying(inst, take_key) if not eq.signals or signal_id in eq.signals for f in eq.filters]


def take_setup(inst: Instrument, take_key: str, signal_ids: list[str]) -> str:
    """What a take's samples got from the clean-up EQ; "" without one. Names aren't part of it:
    renaming an EQ doesn't change a sample."""
    used = {sig: [f.model_dump(mode="json") for f in filters_for(inst, take_key, sig)] for sig in signal_ids}
    used = {sig: bands for sig, bands in used.items() if bands}
    return json.dumps(used, sort_keys=True) if used else ""


def signal_names(inst: Instrument, ids: list[str]) -> str:
    names = {s.id: s.name for s in inst.signals}
    return ", ".join(names[i] for i in ids if i in names)


def missing_signals(inst: Instrument, eq: CleanupEq) -> list[str]:
    """Signals an EQ is for that the instrument no longer has (removed or renamed): it skips them."""
    ids = {s.id for s in inst.signals}
    return [s for s in eq.signals if s not in ids]


def describe(inst: Instrument, take_key: str, signal_ids: list[str]) -> str:
    """E.g. "Mains hum (Piezo), Click 3 kHz", for the take's hits."""
    out = []
    for eq in applying(inst, take_key):
        sigs = [s for s in signal_ids if not eq.signals or s in eq.signals]
        if sigs:
            out.append(eq.name + (f" ({signal_names(inst, sigs)})" if eq.signals and len(signal_ids) > 1 else ""))
    return ", ".join(out)


def forget_missing(inst: Instrument, recorded: set[str]) -> bool:
    """Drops picks of recordings that are gone, so one made later under the same name doesn't get the
    EQ unasked. Whether any were dropped."""
    dropped = False
    for eq in inst.processing.cleanup_eqs:
        kept = [k for k in eq.takes if k in recorded]
        if kept != eq.takes:
            eq.takes, dropped = kept, True
    return dropped


def move_take(inst: Instrument, old_key: str, new_key: str) -> bool:
    """A recording got another key (moved to another note): its EQs go along. Whether any did."""
    moved = False
    for eq in inst.processing.cleanup_eqs:
        if old_key in eq.takes:
            eq.takes = [new_key if k == old_key else k for k in eq.takes]
            moved = True
    return moved
