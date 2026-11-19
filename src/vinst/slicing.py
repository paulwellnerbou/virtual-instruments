"""Cut takes into single hits and analyse them.

All signals of a take are cut at the same positions (found on a mix of all signals), so the
piezo / mic / ... samples of one hit stay phase-aligned.
"""

from collections.abc import Callable
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from vinst import denoise, loops
from vinst.plan import Take, plan_takes, recording_passes, slicing_setup
from vinst.review import Hit, Manifest, TakeResult, samples_dir
from vinst.spec import Instrument

HOP = 256
PREROLL_S = 0.005
FADE_OUT_S = 0.05
MIN_RISE_DB = 6.0  # an onset must be this much louder than just before it
MIN_ABOVE_NOISE_DB = 15.0
TAIL_ABOVE_NOISE_DB = 6.0
MIN_GAP_S = 0.15
LOUDNESS_WINDOW_S = 0.3
ENVELOPE_POINTS = 400
MIN_NOISE_FLOOR_DB = -90.0
SUBTYPES = {16: "PCM_16", 24: "PCM_24", 32: "PCM_32"}

Progress = Callable[[int, int, str], None]


def db(x: float | np.ndarray) -> float | np.ndarray:
    return 20 * np.log10(np.maximum(x, 1e-10))


def frame_rms_db(y: np.ndarray) -> np.ndarray:
    return db(librosa.feature.rms(y=y, frame_length=2 * HOP, hop_length=HOP, center=True)[0])


def load_signals(paths: list[Path]) -> tuple[list[np.ndarray], int]:
    signals, rates = [], set()
    for p in paths:
        data, sr = sf.read(p, always_2d=True, dtype="float64")
        signals.append(data.mean(axis=1))
        rates.add(sr)
    if len(rates) != 1:
        raise ValueError(f"signals have different sample rates: {sorted(rates)}")
    n = min(len(s) for s in signals)
    return [s[:n] for s in signals], rates.pop()


def mix(signals: list[np.ndarray]) -> np.ndarray:
    return sum(s / max(np.max(np.abs(s)), 1e-9) for s in signals) / len(signals)


def detect_onsets(ref: np.ndarray, sr: int, noise_rel_db: float) -> list[int]:
    env = librosa.onset.onset_strength(y=ref, sr=sr, hop_length=HOP)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP, backtrack=True,
                                        units="frames", wait=int(MIN_GAP_S * sr / HOP), delta=0.05)
    level = frame_rms_db(ref)
    ref_noise = noise_rel_db + float(db(np.max(np.abs(ref))))
    out: list[int] = []
    for f in frames:
        before = level[max(0, f - 6): max(1, f - 1)]
        after = level[f: f + 10]
        if after.size == 0:
            continue
        rise = after.max() - (before.min() if before.size else -120)
        if rise >= MIN_RISE_DB and after.max() >= ref_noise + MIN_ABOVE_NOISE_DB:
            sample = int(f * HOP)
            if not out or sample - out[-1] >= MIN_GAP_S * sr:
                out.append(sample)
    return out


def cut_points(ref: np.ndarray, sr: int, onsets: list[int],
               noise_rel_db: float) -> list[tuple[int, int, bool]]:
    """(start, end, cut_off) per onset; cut_off means the hit was still ringing at the next onset."""
    level = frame_rms_db(ref)
    ref_noise = noise_rel_db + float(db(np.max(np.abs(ref))))
    pre = int(PREROLL_S * sr)
    cuts = []
    for i, onset in enumerate(onsets):
        start = max(0, onset - pre)
        limit = (onsets[i + 1] - pre) if i + 1 < len(onsets) else len(ref)
        f0, f1 = start // HOP, max(start // HOP + 1, limit // HOP)
        loud = np.nonzero(level[f0:f1] > ref_noise + TAIL_ABOVE_NOISE_DB)[0]
        last_loud = (f0 + loud[-1] + 1) * HOP if loud.size else start
        cut_off = last_loud >= limit - HOP
        end = min(last_loud + int(0.1 * sr), limit) if loud.size else limit
        cuts.append((start, int(end), bool(cut_off)))
    return cuts


def fade_frames(length: int, sr: int) -> int:
    return min(int(FADE_OUT_S * sr), length // 5)


def body_level(seg: np.ndarray, sr: int) -> float:
    """RMS level of the held part: after the attack, before the fade-out."""
    a = min(int(LOUDNESS_WINDOW_S * sr), len(seg) // 4)
    body = seg[a: len(seg) - fade_frames(len(seg), sr)]
    if body.size == 0:
        body = seg
    return round(float(db(np.sqrt(np.mean(body ** 2)))), 2)


def finish(segment: np.ndarray, sr: int) -> np.ndarray:
    seg = segment - segment.mean()
    fade = fade_frames(len(seg), sr)
    if fade > 1:
        seg[-fade:] *= 0.5 * (1 + np.cos(np.linspace(0, np.pi, fade)))
    fade_in = min(int(0.001 * sr), len(seg) // 10)
    if fade_in > 1:
        seg[:fade_in] *= np.linspace(0, 1, fade_in)
    return seg


def detect_pitch(seg: np.ndarray, sr: int, expected: int) -> float | None:
    a, b = int(0.04 * sr), int(0.64 * sr)
    window = seg[a:b]
    fmin, fmax = float(librosa.midi_to_hz(expected - 5)), float(librosa.midi_to_hz(expected + 5))
    frame = 1 << int(np.ceil(np.log2(3 * sr / fmin)))
    if len(window) < frame:
        return None
    # YIN with parabolic interpolation gives sub-cent resolution; the narrow band around the
    # expected note rules out octave errors, so no probabilistic voicing (pYIN) is needed.
    f0 = librosa.yin(window, fmin=fmin, fmax=fmax, sr=sr, frame_length=frame, hop_length=frame // 4)
    midi = librosa.hz_to_midi(f0[np.isfinite(f0)])
    if midi.size < 3:
        return None
    centre = np.median(midi)
    stable = midi[np.abs(midi - centre) < 0.3]
    if stable.size < 0.6 * midi.size:  # pitch wanders: noise, buzz or a slide
        return None
    coarse = float(np.median(stable))
    return refine_pitch(window, sr, coarse)


def refine_pitch(window: np.ndarray, sr: int, coarse_midi: float) -> float:
    """Spectral peak of the fundamental near the YIN estimate.

    YIN reads a few cents sharp on decaying notes; the peak of a windowed spectrum is unbiased
    by the decay. Falls back to the coarse value if the fundamental is too weak.
    """
    n = 1 << 18
    spec = np.abs(np.fft.rfft(window * np.hanning(len(window)), n))
    hz = float(librosa.midi_to_hz(coarse_midi))
    lo, hi = (int(hz * f * n / sr) for f in (2 ** (-0.5 / 12), 2 ** (0.5 / 12)))
    if hi - lo < 3:
        return coarse_midi
    k = lo + int(np.argmax(spec[lo:hi]))
    if spec[k] < 0.1 * spec.max() or not (lo < k < hi - 1):
        return coarse_midi
    a, b, c = np.log(spec[k - 1: k + 2] + 1e-12)
    offset = 0.5 * (a - c) / (a - 2 * b + c)
    return float(librosa.hz_to_midi((k + offset) * sr / n))


def envelope(ref: np.ndarray) -> list[float]:
    buckets = np.array_split(np.abs(ref), ENVELOPE_POINTS)
    peaks = np.array([b.max() if b.size else 0 for b in buckets])
    return [round(float(v), 3) for v in peaks / max(peaks.max(), 1e-9)]


def noise_floor(signal_id: str, takes_dir: Path) -> float | None:
    """RMS level of a signal's noise-floor recording, if there is one."""
    path = takes_dir / f"noisefloor_{signal_id}.wav"
    if not path.exists():
        return None
    data, _ = sf.read(path, always_2d=True, dtype="float64")
    return float(db(np.sqrt(np.mean(data.mean(axis=1) ** 2))))


def noise_profiles(inst: Instrument, takes_dir: Path) -> dict[str, np.ndarray]:
    """Per-signal noise profiles for denoising, from noisefloor_<signal>.wav."""
    profiles = {}
    for sig in inst.signals:
        path = takes_dir / f"noisefloor_{sig.id}.wav"
        if not path.exists():
            raise ValueError(f"Denoising is on, but {path.name} is missing (record the noise floor).")
        data, _ = sf.read(path, always_2d=True, dtype="float64")
        profiles[sig.id] = denoise.noise_profile(data.mean(axis=1))
    return profiles


def fingerprint(paths: list[Path]) -> str:
    return ";".join(f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in paths)


def process_take(inst: Instrument, take: Take, takes_dir: Path, out_dir: Path,
                 noise_db: float | None, profiles: dict[str, np.ndarray] | None = None) -> TakeResult:
    """Slice and analyse one take. The first signal is the reference for pitch and loudness."""
    result = TakeResult(number=take.number, base=take.base_name, pass_id=take.pass_id, title=take.title,
                        kind=take.kind, status="ok", expected_hits=take.hits)
    by_id = {s.id: s for s in inst.signals}
    take_signals = [by_id[i] for i in take.signals]
    # Hit ids must differ between passes: the same note is recorded once per pass.
    id_base = take.key.replace("~", "_")
    paths = [takes_dir / f for f in take.files]
    present = [p.exists() for p in paths]
    if not any(present):
        result.status, result.message = "missing", "not recorded yet"
        return result
    if not all(present):
        result.status = "error"
        result.message = "missing: " + ", ".join(p.name for p, ok in zip(paths, present) if not ok)
        return result
    result.source = fingerprint(paths)
    try:
        signals, sr = load_signals(paths)
    except Exception as e:  # unreadable file: report it on the take, keep going with the rest
        result.status, result.message = "error", str(e)
        return result
    ref = mix(signals)
    result.sample_rate, result.length = sr, len(ref)
    if take.kind == "noise_floor":
        # No waveform: normalised noise would look like a solid block.
        result.status = "noise_floor"
        result.message = f"{len(ref) / sr:.0f} s"
        return result
    result.envelope = envelope(ref)

    primary_level = float(db(np.max(np.abs(signals[0]))))
    floor = noise_db if noise_db is not None else float(np.percentile(frame_rms_db(signals[0]), 10))
    # Already denoised takes have (near) digital silence; without a lower bound tails would be
    # kept down to -200 dB.
    floor = max(floor, MIN_NOISE_FLOOR_DB)
    # detect_onsets works on the normalised mix; express the floor relative to the primary's peak.
    rel_floor = floor - primary_level
    onsets = detect_onsets(ref, sr, rel_floor)
    cuts = cut_points(ref, sr, onsets, rel_floor)

    assigned = len(cuts) == take.hits and take.kind == "note"
    if len(cuts) != take.hits:
        result.status = "count_mismatch"
        result.message = f"found {len(cuts)} hits, expected {take.hits}"
    rr = inst.dynamics.round_robins
    markers = next((m for m in (loops.read_wav_loops(p) for p in paths) if m), []) \
        if inst.processing.loops and take.kind == "note" else []
    out_dir.mkdir(parents=True, exist_ok=True)
    subtype = SUBTYPES.get(inst.recording.bit_depth, "PCM_24")

    for i, (start, end, cut_off) in enumerate(cuts):
        if assigned:
            layer, rr_n = inst.dynamics.layers[i // rr], i % rr + 1
            hit_id = f"{id_base}_{layer}_rr{rr_n}"
        else:
            layer, rr_n = None, None
            hit_id = f"{id_base}_{i + 1:02d}"
        files, peaks = {}, {}
        for sig, audio in zip(take_signals, signals):
            seg = audio[start:end]
            peaks[sig.id] = round(float(db(np.max(np.abs(seg)))), 2)
            name = f"{hit_id}_{sig.id}.wav"
            # Analysis above and below uses the original audio; only the written sample is cleaned.
            out = (denoise.reduce(seg, profiles[sig.id], inst.processing.denoise_reduction_db)
                   if profiles else seg.copy())
            sf.write(out_dir / name, finish(out, sr), sr, subtype=subtype)
            files[sig.id] = name
        win = signals[0][start: start + int(LOUDNESS_WINDOW_S * sr)]
        hit = Hit(id=hit_id, index=i, layer=layer, rr=rr_n, start=start, end=end, files=files,
                  rms_db=round(float(db(np.sqrt(np.mean(win ** 2)))), 2),
                  body_db=body_level(signals[0][start:end], sr), peak_db=peaks)
        if any(p >= -0.02 for p in peaks.values()):
            hit.flags.append("clipped")
        if cut_off and take.kind == "note":
            hit.flags.append("cut_off")
        if take.kind == "note" and take.note is not None and take.note.sound is None:
            # Not the mix: a delayed external mic would comb-filter it around the fundamental.
            detected = detect_pitch(signals[0][start:end], sr, take.note.midi)
            if detected is None:
                hit.flags.append("no_pitch")
            else:
                hit.midi_detected = round(detected, 3)
                hit.cents = round((detected - take.note.midi) * 100, 1)
                if abs(hit.cents) > 50:
                    hit.flags.append("wrong_note")
                elif abs(hit.cents) > 20:
                    hit.flags.append("out_of_tune")
        if inst.processing.loops and take.kind == "note":
            add_loop(inst, hit, signals[0][start:end], sr, markers, take)
        result.hits.append(hit)

    if assigned and len(inst.dynamics.layers) > 1:
        check_layers(result, inst.dynamics.layers)
    unused = [m for m in markers if not any(h.start <= m[0] and m[1] < h.end for h in result.hits)]
    if unused:
        where = ", ".join(f"{a / sr:.2f}–{b / sr:.2f} s" for a, b in unused)
        note = f"loop marker {where} doesn't lie inside one hit and was ignored"
        result.message = f"{result.message}; {note}" if result.message else note
    return result


def add_loop(inst: Instrument, hit: Hit, seg: np.ndarray, sr: int, markers: list[tuple[int, int]],
             take: Take) -> None:
    """Loop markers from the take win; otherwise search the hit (the primary signal)."""
    loop = loops.markers_for_hit(markers, hit.start, hit.end)
    if loop is None:
        freq = float(librosa.midi_to_hz(hit.midi_detected if hit.midi_detected is not None
                                        else take.note.midi)) if take.note else None
        crossfade = int(inst.processing.loop_crossfade_ms / 1000 * sr)
        # Stay clear of the fade-out finish() applies to the end of every sample.
        loop = loops.find_loop(seg, sr, freq, tail_guard=int((FADE_OUT_S + 0.05) * sr), crossfade=crossfade)
    if loop is None:
        hit.flags.append("no_loop")
        return
    hit.loop_start, hit.loop_end, hit.loop_score, hit.loop_source = loop.start, loop.end, loop.score, loop.source
    if loop.source == "auto" and loop.score < loops.POOR_MATCH:
        hit.flags.append("loop_poor")
    if loop.end >= len(seg) - fade_frames(len(seg), sr):
        hit.flags.append("loop_in_fade")


def check_layers(result: TakeResult, layers: list[str]) -> None:
    medians = {
        layer: float(np.median([h.rms_db for h in result.hits if h.layer == layer])) for layer in layers
    }
    for h in result.hits:
        nearest = min(medians, key=lambda layer: abs(medians[layer] - h.rms_db))
        if nearest != h.layer:
            h.flags.append("layer_mismatch")
    ordered = [medians[layer] for layer in layers]
    if any(b <= a for a, b in zip(ordered, ordered[1:])):
        result.message = "velocity layers don't get louder in order"


def remove_stale_files(previous: Manifest, current: Manifest) -> int:
    """Delete sample files the previous run wrote that the current run no longer produces.

    Only files listed in the previous manifest are touched, so a samples folder shared with
    other audio is safe.
    """
    keep = {Path(current.samples_dir) / f for t in current.takes for h in t.hits for f in h.files.values()}
    removed = 0
    for t in previous.takes:
        for h in t.hits:
            for f in h.files.values():
                path = Path(previous.samples_dir) / f
                if path not in keep and path.is_file():
                    path.unlink()
                    removed += 1
    return removed


def process(inst: Instrument, previous: Manifest | None = None, progress: Progress | None = None) -> Manifest:
    problems = inst.problems()
    if problems:
        raise ValueError("Fix these first: " + " ".join(problems))
    if not inst.recording.takes_dir:
        raise ValueError("Set the takes folder first (step 5).")
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    if not takes_dir.is_dir():
        raise ValueError(f"Takes folder not found: {takes_dir}")
    out_dir = samples_dir(inst)
    assert out_dir is not None
    # Each pass has its own setup (e.g. a different pickup blend), so its own noise floor.
    noise = {p.id: noise_floor(p.signals[0].id, takes_dir) for p in recording_passes(inst)}
    profiles = noise_profiles(inst, takes_dir) if inst.processing.denoise else None
    # A rejection belongs to the recorded audio: drop it if the take was re-recorded.
    rejected = {
        (t.key, t.source): {h.id for h in t.hits if h.rejected} for t in previous.takes
    } if previous else {}
    loop_off = {
        (t.key, t.source): {h.id for h in t.hits if h.loop_off} for t in previous.takes
    } if previous else {}

    takes = plan_takes(inst)
    manifest = Manifest(samples_dir=str(out_dir), setup=slicing_setup(inst),
                        noise_floor_db=next(iter(noise.values()), None), denoised=profiles is not None)
    for i, take in enumerate(takes):
        if progress:
            progress(i, len(takes), take.title)
        result = process_take(inst, take, takes_dir, out_dir, noise[take.pass_id], profiles)
        keep = rejected.get((result.key, result.source), set())
        no_loop = loop_off.get((result.key, result.source), set())
        for h in result.hits:
            h.rejected = h.id in keep
            h.loop_off = h.id in no_loop
        manifest.takes.append(result)
    if previous:
        remove_stale_files(previous, manifest)
    if progress:
        progress(len(takes), len(takes), "done")
    return manifest
