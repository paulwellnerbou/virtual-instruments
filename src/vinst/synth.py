"""Synthetic takes that follow a spec's recording plan, for trying the import/review step before recording."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from vinst.plan import Take, plan_takes
from vinst.spec import Instrument

LAYER_AMPS = [0.12, 0.3, 0.6, 0.85]


@dataclass
class Mistakes:
    """Deliberate errors so the review step has something to flag."""

    wrong_note_take: int | None = None  # take number with one hit a semitone sharp
    missing_hit_take: int | None = None  # take number with one hit left out
    clipped_take: int | None = None  # take number with one clipped hit


def pluck(freq: float, amp: float, seconds: float, sr: int, rng: np.random.Generator) -> np.ndarray:
    n = int(seconds * sr)
    t = np.arange(n) / sr
    out = np.zeros(n)
    for k in range(1, 16):
        f = freq * k * (1 + 0.0004 * k * k)  # slight inharmonicity
        if f > sr / 2.2:
            break
        decay = 1.2 + 0.45 * k  # higher partials die faster
        out += (1 / k) * np.exp(-decay * t) * np.sin(2 * np.pi * f * t + rng.uniform(0, 2 * np.pi))
    burst = int(0.004 * sr)
    out[:burst] += rng.normal(0, 0.3, burst) * np.linspace(1, 0, burst)
    out *= np.minimum(1, t / 0.002)  # 2 ms attack, avoids a click
    return amp * out / np.max(np.abs(out))


def held(freq: float, amp: float, seconds: float, sr: int, rng: np.random.Generator) -> np.ndarray:
    """A sustained tone (bowed / blown): soft attack, steady level with slight vibrato, short release."""
    n = int(seconds * sr)
    t = np.arange(n) / sr
    vibrato = 1 + 0.002 * np.sin(2 * np.pi * rng.uniform(4.5, 5.5) * t)
    phase = 2 * np.pi * np.cumsum(freq * vibrato) / sr
    out = sum((0.8 ** k) * np.sin(k * phase + rng.uniform(0, 2 * np.pi)) for k in range(1, 10))
    out *= np.minimum(1, t / 0.08) * np.minimum(1, (seconds - t) / 0.15)
    return amp * out / np.max(np.abs(out))


def knock(amp: float, sr: int, rng: np.random.Generator) -> np.ndarray:
    n = int(0.4 * sr)
    t = np.arange(n) / sr
    body = np.sin(2 * np.pi * 180 * t) * np.exp(-25 * t) + rng.normal(0, 0.2, n) * np.exp(-60 * t)
    return amp * body / np.max(np.abs(body))


def render_take(inst: Instrument, take: Take, ring: float, rng: np.random.Generator,
                mistakes: Mistakes) -> list[np.ndarray]:
    sr = inst.recording.sample_rate
    gap = 1.0
    hits: list[np.ndarray] = []
    if take.kind == "note" and take.note is not None:
        layers, rr = inst.dynamics.layers, inst.dynamics.round_robins
        for i in range(take.hits):
            if take.number == mistakes.missing_hit_take and i == take.hits - 1:
                continue
            layer = i // rr
            amp = LAYER_AMPS[min(layer, len(LAYER_AMPS) - 1)] if len(layers) > 1 else 0.5
            amp *= rng.uniform(0.9, 1.1)
            if take.number == mistakes.clipped_take and i == take.hits - 1:
                amp = 1.6
            midi = take.note.midi + rng.uniform(-0.08, 0.08)
            if take.number == mistakes.wrong_note_take and i == 1:
                midi += 1
            tone = held if inst.meta.type in ("sustained", "single_sample") else pluck
            hits.append(tone(440 * 2 ** ((midi - 69) / 12), amp, ring, sr, rng))
    elif take.kind == "extra":
        hits = [knock(rng.uniform(0.2, 0.6), sr, rng) for _ in range(take.hits)]
    else:
        n = int((inst.recording.noise_floor_seconds or 5) * sr)
        hits = []
        silence = np.zeros(n)

    parts: list[np.ndarray] = [np.zeros(int(0.5 * sr))]
    for h in hits:
        parts += [h, np.zeros(int(gap * sr))]
    dry = np.concatenate(parts) if hits else silence

    out = []
    for idx, _ in enumerate(take.signals):
        noise = rng.normal(0, 10 ** (-70 / 20), dry.size)
        if idx == 0:
            sig = dry
        else:
            # Darker, slightly delayed second signal, like an internal mic next to a piezo.
            kernel = np.ones(6 + 4 * idx) / (6 + 4 * idx)
            sig = np.convolve(dry, kernel, mode="full")[: dry.size]
            sig = np.concatenate([np.zeros(idx * 12), sig[: dry.size - idx * 12]]) * 0.8
        out.append(np.clip(sig + noise, -1, 1))
    return out


def write_takes(inst: Instrument, folder: Path, ring: float = 1.5, seed: int = 1,
                mistakes: Mistakes | None = None) -> int:
    """Write every planned take file into `folder`; returns the number of files written."""
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    takes = plan_takes(inst)
    if mistakes is None:
        note_takes = [t.number for t in takes if t.kind == "note"]
        mistakes = Mistakes(
            wrong_note_take=note_takes[1] if len(note_takes) > 1 else None,
            missing_hit_take=note_takes[3] if len(note_takes) > 3 else None,
            clipped_take=note_takes[5] if len(note_takes) > 5 else None,
        )
    count = 0
    for take in takes:
        for path, audio in zip(take.files, render_take(inst, take, ring, rng, mistakes)):
            sf.write(folder / path, audio, inst.recording.sample_rate, subtype="PCM_16")
            count += 1
    return count
