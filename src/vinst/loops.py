"""Loop points for sustained samples: from loop markers in the takes, or found automatically."""

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.signal import correlate

MATCH_WINDOW_S = 0.02  # the audio just before the loop end must resemble the audio just before the start
MIN_LOOP_S = 0.25
MIN_PERIODS = 10
POOR_MATCH = 0.9
END_CANDIDATES = 6


@dataclass
class Loop:
    start: int  # first frame of the loop, relative to the sample
    end: int  # last frame of the loop (inclusive, as in WAV "smpl" chunks and DecentSampler)
    score: float  # 1.0 for markers; normalised correlation for found loops
    source: str  # "marker" | "auto"


def read_wav_loops(path: Path) -> list[tuple[int, int]]:
    """(start, end) of every loop in a WAV file's "smpl" chunk, e.g. loop markers saved by WaveLab.

    Walks the chunk headers and skips the audio, so a long take isn't read just for these bytes.
    """
    try:
        f = path.open("rb")
    except OSError:
        return []
    with f:
        head = f.read(12)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            return []
        while True:
            header = f.read(8)
            if len(header) < 8:
                return []
            chunk, size = header[:4], struct.unpack("<I", header[4:])[0]
            if chunk != b"smpl":
                f.seek(size + (size & 1), 1)  # chunks are padded to an even length
                continue
            body = f.read(size)
            if len(body) < 36:
                return []
            count = struct.unpack("<I", body[28:32])[0]
            loops = []
            for k in range(count):
                off = 36 + 24 * k
                if off + 24 > len(body):
                    break
                _cue, _type, start, end, _frac, _plays = struct.unpack("<6I", body[off:off + 24])
                if end > start:
                    loops.append((start, end))
            return loops


def write_wav_loops(path: Path, loops: list[tuple[int, int]], sample_rate: int) -> None:
    """Append a "smpl" chunk with the given loops (used to create test takes)."""
    data = bytearray(path.read_bytes())
    header = struct.pack("<9I", 0, 0, int(1e9 / sample_rate), 60, 0, 0, 0, len(loops), 0)
    body = header + b"".join(struct.pack("<6I", i, 0, s, e, 0, 0) for i, (s, e) in enumerate(loops))
    data += b"smpl" + struct.pack("<I", len(body)) + body
    data[4:8] = struct.pack("<I", len(data) - 8)
    path.write_bytes(bytes(data))


def markers_for_hit(markers: list[tuple[int, int]], start: int, end: int) -> Loop | None:
    """The first take-level loop that lies inside the hit [start, end), relative to the hit."""
    for ls, le in markers:
        if start <= ls and le < end:
            return Loop(ls - start, le - start, 1.0, "marker")
    return None


def _sustain_region(seg: np.ndarray, sr: int, tail_guard: int) -> tuple[int, int]:
    """Skip the attack and the decay into silence: keep where the level is near its median."""
    hop = int(0.01 * sr)
    frames = np.array([np.sqrt(np.mean(seg[i:i + hop] ** 2)) + 1e-12 for i in range(0, len(seg) - hop, hop)])
    if frames.size < 3:
        return 0, 0
    level = 20 * np.log10(frames)
    a = max(int(0.15 * sr), len(seg) // 5)
    b = len(seg) - tail_guard
    inner = level[a // hop: max(a // hop + 1, b // hop)]
    if inner.size == 0:
        return 0, 0
    median = np.median(inner)
    loud = np.nonzero(level[: b // hop] > median - 6)[0]
    if loud.size == 0:
        return 0, 0
    return a, min(b, (loud[-1] + 1) * hop)


def find_loop(seg: np.ndarray, sr: int, freq: float | None, tail_guard: int, crossfade: int) -> Loop | None:
    """Search the sustained part for the loop whose end joins most smoothly back to its start."""
    a, b = _sustain_region(seg, sr, tail_guard)
    w = int(MATCH_WINDOW_S * sr)
    min_len = int(max(MIN_LOOP_S, MIN_PERIODS / freq if freq else 0) * sr)
    a = max(a, w, crossfade)
    if b - a < min_len + w:
        return None
    best: Loop | None = None
    spacing = max(1, int(0.03 * sr))
    for end in range(b, max(a + min_len, b - END_CANDIDATES * spacing), -spacing):
        ref = seg[end - w:end]
        ref_energy = float(np.dot(ref, ref))
        lo, hi = a - w, end - min_len  # windows ending at candidate loop starts in [a, end - min_len]
        if hi - lo <= w or ref_energy <= 0:
            continue
        x = seg[lo:hi]
        corr = correlate(x, ref, mode="valid", method="fft")
        energy = np.convolve(x * x, np.ones(w), mode="valid")
        ncc = corr / np.sqrt(np.maximum(energy * ref_energy, 1e-20))
        # Prefer long loops: they repeat less audibly. Among near-equal matches take the earliest.
        k = int(np.argmax(ncc >= ncc.max() - 0.01))
        score = float(ncc[k])
        if best is None or score > best.score + 0.005:
            best = Loop(start=lo + k + w, end=end - 1, score=round(score, 4), source="auto")
    return best


def loop_preview(audio: np.ndarray, loop: Loop, sr: int, repeats: int = 3, crossfade: int = 0) -> np.ndarray:
    """The sample played up to the loop end, then the loop `repeats` more times, faded out."""
    body = audio[loop.start: loop.end + 1]
    parts = [audio[: loop.end + 1]]
    xf = min(crossfade, len(body) // 4, loop.start)
    for _ in range(repeats):
        piece = body.copy()
        if xf > 1:
            # Approximates Decent Sampler's crossfade: audio from before the loop start fades in.
            ramp = np.linspace(0, 1, xf)
            pre = audio[loop.start - xf: loop.start]
            parts[-1] = parts[-1].copy()
            parts[-1][-xf:] = parts[-1][-xf:] * np.sqrt(1 - ramp) + pre * np.sqrt(ramp)
        parts.append(piece)
    out = np.concatenate(parts)
    fade = min(int(0.2 * sr), len(out) // 4)
    out[-fade:] *= np.linspace(1, 0, fade)
    return out
