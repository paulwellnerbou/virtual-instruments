"""Cut takes into single hits and analyse them.

All signals of a take are cut at the same positions (found on a mix of all signals), so the
piezo / mic / ... samples of one hit stay phase-aligned.
"""

import glob
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from vinst import aims, cleanup_eq, denoise, loops, takes_meta
from vinst.plan import (
    Take,
    combined_base,
    more_base,
    more_file,
    note_lanes,
    plan_takes,
    recording_passes,
    slicing_setup,
)
from vinst.review import Hit, Manifest, TakeResult, check_lanes, samples_dir, settle
from vinst.spec import SUSTAINED_TYPES, Instrument

# Raised whenever hits are found differently, so older results are offered for slicing again.
# 1: attack judged over ATTACK_S, starts moved to the main rise.
# 2: a hit's tail ends where the next sound begins, not at the next attack.
# 3: starts found to the sample, PREROLL_S before the attack, not frames before it; also
#    those set by hand.
HIT_FINDER = 3
HOP = 256
PREROLL_S = 0.002
FADE_OUT_S = 0.05
MIN_RISE_DB = 6.0  # an onset must be this much louder than just before it
MIN_ABOVE_NOISE_DB = 15.0
TAIL_ABOVE_NOISE_DB = 6.0
MIN_GAP_S = 0.15
# How long after an onset its loudness is judged. Onsets are placed where the rise begins, which can
# be a finger touching the string well before the pluck peaks.
ATTACK_S = 0.15
# A sample starts where its signal first comes this close to the attack's peak: the pluck, not the
# touch before it. Played from a key, any silence before the attack is latency.
MAIN_RISE_DB = 20.0
# A bowed or blown note's slow rise is its sound: only the silence before it is left out.
SUSTAINED_RISE_DB = 40.0
# ... and is this much louder than the loudest of BEFORE_S before it, so a ringing tail under a soft
# hit isn't its attack. BEFORE_S holds a full period of a low note.
ATTACK_ABOVE_BEFORE_DB = 6.0
BEFORE_S = 0.02
MIN_CUT_S = 0.05  # shorter cuts set by hand are refused, and a snap never makes one
SAME_CUT_S = 0.05  # a hit cut this close to where it was is the same hit
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
    """Where each hit's attack begins (in frames of `ref`)."""
    return [attack for attack, _ in detect_sounds(ref, sr, noise_rel_db)]


def detect_sounds(ref: np.ndarray, sr: int, noise_rel_db: float,
                  rise_db: float = MAIN_RISE_DB) -> list[tuple[int, int]]:
    """Per hit: where its attack begins, and where its sound begins, which can be earlier (a touch of
    the string, the hand moving in). The sample starts at the attack; the hit before it ends before
    the sound."""
    env = librosa.onset.onset_strength(y=ref, sr=sr, hop_length=HOP)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP, backtrack=True,
                                        units="frames", wait=int(MIN_GAP_S * sr / HOP), delta=0.05)
    level = frame_rms_db(ref)
    ref_noise = noise_rel_db + float(db(np.max(np.abs(ref))))
    out: list[tuple[int, int]] = []
    for f in frames:
        begins = int(f * HOP)
        before = level[max(0, f - 6): max(1, f - 1)]
        after = level[f: f + max(10, int(ATTACK_S * sr / HOP))]
        if after.size == 0:
            continue
        rise = after.max() - (before.min() if before.size else -120)
        if rise >= MIN_RISE_DB and after.max() >= ref_noise + MIN_ABOVE_NOISE_DB:
            sample = attack_start(ref, sr, begins, rise_db)
            if not out or sample - out[-1][0] >= MIN_GAP_S * sr:
                out.append((sample, min(begins, sample)))
    return out


def rise_db(inst: Instrument) -> float:
    return SUSTAINED_RISE_DB if inst.meta.type in SUSTAINED_TYPES else MAIN_RISE_DB


def attack_start(ref: np.ndarray, sr: int, begins: int, rise: float = MAIN_RISE_DB) -> int:
    """The sample where the attack of an onset found at `begins` starts. Frames are too coarse for it:
    a frame early is 5 ms of silence before the pluck."""
    a = max(0, begins - HOP)  # RMS frames are centred: the rise can begin half a frame before
    b = begins + int(ATTACK_S * sr)
    # A soft hit over a louder tail only rises above it.
    found = attack_in(ref, sr, a, b, rise)
    found = attack_in(ref, sr, a, b, rise, above_before_db=0.0) if found is None else found
    return begins if found is None else found


def attack_in(ref: np.ndarray, sr: int, a: int, b: int, rise: float = MAIN_RISE_DB,
              above_before_db: float = ATTACK_ABOVE_BEFORE_DB) -> int | None:
    """The first sample in [a, b) within `rise` dB of the peak there and `above_before_db` above what
    came just before; None in a sound that doesn't rise."""
    window = np.abs(ref[a:b])
    if window.size == 0:
        return None
    before = np.abs(ref[max(0, a - int(BEFORE_S * sr)): a])
    threshold = max(window.max() * 10 ** (-rise / 20),
                    (before.max() if before.size else 0) * 10 ** (above_before_db / 20))
    above = np.nonzero(window >= threshold)[0]
    return a + int(above[0]) if above.size else None


def snap_to_attack(ref: np.ndarray, sr: int, start: int, end: int, rise: float = MAIN_RISE_DB) -> int:
    """A start set by hand, moved forward to PREROLL_S before the attack that follows it: dragged on a
    whole recording's waveform, it lands milliseconds early. Never earlier than it was set, and never
    so late that the cut gets shorter than MIN_CUT_S."""
    found = attack_in(ref, sr, start, min(end, start + int(ATTACK_S * sr)), rise)
    if found is None:
        return start
    snapped = max(start, found - int(PREROLL_S * sr))
    return snapped if end - snapped >= MIN_CUT_S * sr else start


def hand_start(inst: Instrument, take: TakeResult, start: int, end: int) -> int:
    """Where a start dragged in the cut editor is kept: snapped as slicing snaps it, so the cut kept is
    the one shown. As dragged if the recording can't be read; slicing then reports why."""
    paths = [Path(inst.recording.takes_dir).expanduser() / f for f in take.files]
    try:
        signals, sr = load_signals(paths)
    except (OSError, RuntimeError, ValueError):
        return start
    return snap_to_attack(mix(signals), sr, start, end, rise_db(inst))


def cut_points(ref: np.ndarray, sr: int, onsets: list[int], noise_rel_db: float,
               noises: list[int] | None = None, ring_frames: int = 0,
               begins: dict[int, int] | None = None) -> list[tuple[int, int, bool]]:
    """(start, end, cut_off) per onset; cut_off means the hit was still ringing at the next onset.

    noises: onsets that aren't hits (handling noise). They end the hit before them, so the noise
    isn't in the sample, but a hit that rang for ring_frames by then isn't flagged as cut off.
    begins: where the sound of an onset begins, if before it (see detect_sounds): a hit's tail ends
    there, not at the next attack, or it would take in the start of the next sound.
    """
    level = frame_rms_db(ref)
    ref_noise = noise_rel_db + float(db(np.max(np.abs(ref))))
    pre = int(PREROLL_S * sr)

    def sound(o: int, after: int) -> int:
        """Where the sound of onset `o` begins, with its pre-roll, but after `after`."""
        return max(after + 1, (begins or {}).get(o, o) - pre)

    cuts = []
    for i, onset in enumerate(onsets):
        start = max(0, onset - pre)
        next_hit = sound(onsets[i + 1], onset) if i + 1 < len(onsets) else len(ref)
        noise = next((sound(n, onset) for n in (noises or []) if onset < n and sound(n, onset) < next_hit), None)
        limit = noise if noise is not None else next_hit
        f0, f1 = start // HOP, max(start // HOP + 1, limit // HOP)
        loud = np.nonzero(level[f0:f1] > ref_noise + TAIL_ABOVE_NOISE_DB)[0]
        last_loud = (f0 + loud[-1] + 1) * HOP if loud.size else start
        cut_off = last_loud >= limit - HOP and (noise is None or limit - start < ring_frames)
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


def cleanup_setup(inst: Instrument) -> str:
    """What the written samples depend on beyond their cuts: the clean-up. "" while all of it is off,
    as before it was kept. (The low cut is applied when building, see bundle.)"""
    p = inst.processing
    return f"denoise {p.denoise_reduction_db:g}" if p.denoise else ""


def cut_in_samples(manifest: Manifest) -> bool:
    """Whether the sliced samples have a low cut in them: it was applied when slicing for a while."""
    return "low cut" in manifest.cleanup


def sliced_cleanup(inst: Instrument, manifest: Manifest) -> str:
    """The clean-up `manifest` was sliced with. Results from before it was kept only say whether they
    were denoised, so then the current amount is assumed."""
    if manifest.cleanup or not manifest.denoised:
        return manifest.cleanup
    return f"denoise {inst.processing.denoise_reduction_db:g}"


def fingerprint(paths: list[Path]) -> str:
    return ";".join(f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}" for p in paths)


GRID_EARLY_S = 0.4  # a hit may come this much before its beat
MORE_NAME = re.compile(r"-more(\d+)_(.+)\.wav$")


def on_grid(onsets: list[int], ref: np.ndarray, sr: int, hits_at: list[float]) -> list[int]:
    """Per planned hit that was played, the strongest onset in its bar.

    A bar runs from just before its beat to just before the next one, so a late hit still
    counts. All other onsets are handling noise: they end the hit before them (see cut_points),
    but never start one.
    """
    out = []
    for k, t in enumerate(hits_at):
        if k + 1 < len(hits_at):
            until = hits_at[k + 1]
        else:  # the last bar is as long as the others; a hit recorded on its own has no limit
            until = t + (t - hits_at[k - 1]) if k else float("inf")
        near = [o for o in onsets if (t - GRID_EARLY_S) * sr <= o < (until - GRID_EARLY_S) * sr]
        if near:
            out.append(max(near, key=lambda o: float(np.max(np.abs(ref[o: o + int(ATTACK_S * sr)])))))
    return out


def find_cuts(inst: Instrument, path: Path, ref: np.ndarray, sr: int, rel_floor: float) -> list[tuple[int, int, bool]]:
    """Hits in a recording: as cut by hand, if they were; for one recorded in the app, only those on
    its click grid."""
    by_hand = takes_meta.cuts(inst.meta.slug, path)
    if by_hand is not None:
        kept = [(max(0, a), min(len(ref), b)) for a, b in by_hand if min(len(ref), b) > max(0, a)]
        return [(snap_to_attack(ref, sr, a, b, rise_db(inst)), b, False) for a, b in kept]
    sounds = detect_sounds(ref, sr, rel_floor, rise_db(inst))
    onsets, begins = [a for a, _ in sounds], dict(sounds)
    grid = takes_meta.hits_at(inst.meta.slug, path)
    if grid is None:
        return cut_points(ref, sr, onsets, rel_floor, begins=begins)
    starts = on_grid(onsets, ref, sr, grid)
    noises = [o for o in onsets if o not in starts]
    return cut_points(ref, sr, starts, rel_floor, noises, int(inst.recording.ring_seconds * sr), begins)


def more_numbers(take: Take, takes_dir: Path) -> list[int]:
    """Numbers of the recordings with more variations of a lane take that exist on disk."""
    if not takes_dir.is_dir() or not take.signals:
        return []
    found = set()
    for path in takes_dir.glob(glob.escape(take.base_name) + f"-more*_{take.signals[0]}.wav"):
        m = MORE_NAME.search(path.name[len(take.base_name):])
        if m and m.group(2) == take.signals[0]:
            found.add(int(m.group(1)))
    return sorted(found)


def recordings(inst: Instrument, takes_dir: Path) -> list[Take]:
    """Everything to slice: the planned takes, variations of a lane recorded later, and takes that
    hold all lanes of a note in one file (recorded before notes had lanes; their hits start in the
    note's first lane and can be moved)."""
    out: list[Take] = []
    for t in plan_takes(inst):
        out.append(t)
        if t.kind != "note":
            continue
        for n in more_numbers(t, takes_dir):
            out.append(replace(t, base_name=more_base(t, n), files=[more_file(t, n, s) for s in t.signals],
                               origin="more", title=f"{t.title} · more ({n})", hits=0))
        lanes = note_lanes(inst, t.note_base)
        if t.lane != lanes[0]:
            continue
        files = [f"{combined_base(t)}_{s}.wav" for s in t.signals]
        if all((takes_dir / f).exists() for f in files):
            out.append(replace(t, base_name=combined_base(t), files=files, origin="combined",
                               title=t.title.replace(f" · {t.lane}", " · all lanes in one take"), hits=0))
        # Recordings of lanes this note no longer has: their hits stay, without a lane, so they
        # can be dragged into one, and hits already moved out of them keep their place.
        note_title = t.title.replace(f" · {t.lane}", "")
        for lane, number in sorted(lane_files(t, takes_dir), key=lambda f: (f[0], f[1] or 0)):
            if lane in lanes:
                continue
            base = f"{t.note_base}_{lane}" + (f"-more{number}" if number else "")
            files = [f"{base}_{s}.wav" for s in t.signals]
            if all((takes_dir / f).exists() for f in files):
                out.append(replace(t, base_name=base, files=files, lane=lane, hits=0,
                                   origin="more" if number else "unplanned",
                                   title=f"{note_title} · {lane}" + (f" · more ({number})" if number else "")
                                   + " (velocity removed)"))
    return out


LANE_FILE = re.compile(r"^_(?P<lane>[A-Za-z0-9-]+?)(?:-more(?P<n>\d+))?_(?P<sig>.+)\.wav$")
NOT_A_LANE = re.compile(r"^(hit\d+(-\d+)?|add-.*)$")  # single-hit files of an earlier version


def lane_files(take: Take, takes_dir: Path) -> set[tuple[str, int | None]]:
    """(lane, "more" number) of the lane recordings of a take's note that exist on disk."""
    if not takes_dir.is_dir() or not take.signals:
        return set()
    sig = take.signals[0]
    found = set()
    for path in takes_dir.glob(glob.escape(take.note_base) + f"_*_{sig}.wav"):
        m = LANE_FILE.match(path.name[len(take.note_base):])
        if m and m["sig"] == sig and not NOT_A_LANE.match(m["lane"]):
            found.add((m["lane"], int(m["n"]) if m["n"] else None))
    return found


def process_take(inst: Instrument, take: Take, takes_dir: Path, out_dir: Path,
                 noise_db: float | None, profiles: dict[str, np.ndarray] | None = None,
                 fixed: list[tuple[int, int, bool]] | None = None) -> TakeResult:
    """Slice and analyse one recording. The first signal is the reference for pitch and loudness.
    `fixed`: cut there instead of finding the hits (see edited_cuts)."""
    result = TakeResult(number=take.number, base=take.base_name, pass_id=take.pass_id, title=take.title,
                        kind=take.kind, status="ok", expected_hits=take.hits,
                        midi=take.note.midi if take.note else None, signals=take.signals,
                        lane=take.lane, note=take.note_base, origin=take.origin)
    by_id = {s.id: s for s in inst.signals}
    take_signals = [by_id[i] for i in take.signals]
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
    # Hits are found in the takes as recorded, so changing the EQ doesn't move a cut.
    ref = mix(signals)
    result.sample_rate, result.length = sr, len(ref)
    if take.kind == "noise_floor":
        # No waveform: normalised noise would look like a solid block.
        result.status = "noise_floor"
        result.message = f"{len(ref) / sr:.0f} s"
        result.level_db = noise_db  # measured from this take: its pass's noise floor
        return result
    result.envelope = envelope(ref)

    primary_level = float(db(np.max(np.abs(signals[0]))))
    floor = noise_db if noise_db is not None else float(np.percentile(frame_rms_db(signals[0]), 10))
    # Already denoised takes have (near) digital silence; without a lower bound tails would be
    # kept down to -200 dB.
    floor = max(floor, MIN_NOISE_FLOOR_DB)
    # detect_onsets works on the normalised mix; express the floor relative to the primary's peak.
    rel_floor = floor - primary_level
    cuts = fixed if fixed is not None else find_cuts(inst, paths[0], ref, sr, rel_floor)
    result.cut_by_hand = takes_meta.cuts(inst.meta.slug, paths[0]) is not None  # with `fixed`, see keep_cuts
    markers = next((m for m in (loops.read_wav_loops(p) for p in paths) if m), []) \
        if inst.processing.loops and take.kind == "note" else []
    # Measured on the EQ'd audio: unlike denoising, the EQ is meant to change how a hit sounds, and a
    # hum or click it takes out shouldn't count towards its loudness or tuning.
    result.cleanup_eq = cleanup_eq.take_setup(inst, take.key, take.signals)
    bands = {sig: cleanup_eq.filters_for(inst, take.key, sig) for sig in take.signals}
    cleaned = [cleanup_eq.apply(audio, sr, bands[sig]) for sig, audio in zip(take.signals, signals)]
    if profiles:
        profiles = {sig: cleanup_eq.shape_profile(profile, sr, bands.get(sig, []), denoise.N_FFT)
                    for sig, profile in profiles.items()}
    analyse_cuts(inst, take, result, take_signals, cleaned, sr, cuts, out_dir, profiles, markers, raw=signals)
    return result


def analyse_cuts(inst: Instrument, take: Take, result: TakeResult, take_signals: list, signals: list[np.ndarray],
                 sr: int, cuts: list[tuple[int, int, bool]], out_dir: Path,
                 profiles: dict[str, np.ndarray] | None, markers: list[tuple[int, int]],
                 raw: list[np.ndarray] | None = None) -> None:
    """Write and analyse the hits of one recording; cut positions are frames in `signals`. `raw`: the
    signals as recorded, if `signals` were EQ'd: clipping in the take counts too.

    Every hit of a lane take is a variation of that lane, however many there are; the planned
    count is only a guide.
    """
    id_base = take.key.replace("~", "_")  # hit ids must differ between passes
    if take.hits and len(cuts) != take.hits:
        result.info = f"{len(cuts)} hit{'' if len(cuts) == 1 else 's'}, {take.hits} planned"
    out_dir.mkdir(parents=True, exist_ok=True)
    subtype = SUBTYPES.get(inst.recording.bit_depth, "PCM_24")
    p = inst.processing
    for i, (start, end, cut_off) in enumerate(cuts):
        hit_id = f"{id_base}_{i + 1:02d}"
        files, peaks, played = {}, {}, []
        for k, (sig, audio) in enumerate(zip(take_signals, signals)):
            seg = audio[start:end]
            played.append(seg)
            peak = np.max(np.abs(seg))
            if raw is not None:
                peak = max(peak, np.max(np.abs(raw[k][start:end])))
            peaks[sig.id] = round(float(db(peak)), 2)
            name = f"{hit_id}_{sig.id}.wav"
            # Pitch and loudness are measured without denoising; only the written sample is cleaned.
            out = (denoise.reduce(seg, profiles[sig.id], p.denoise_reduction_db) if profiles else seg.copy())
            sf.write(out_dir / name, finish(out, sr), sr, subtype=subtype)
            files[sig.id] = name
        win = played[0][: int(LOUDNESS_WINDOW_S * sr)]
        hit = Hit(id=hit_id, index=i, layer=take.lane if take.kind == "note" else None, start=start, end=end,
                  files=files, rms_db=round(float(db(np.sqrt(np.mean(win ** 2)))), 2),
                  body_db=body_level(played[0], sr), peak_db=peaks)
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
    unused = [m for m in markers if not any(h.start <= m[0] and m[1] < h.end for h in result.hits)]
    if unused:
        where = ", ".join(f"{a / sr:.2f}–{b / sr:.2f} s" for a, b in unused)
        note = f"loop marker {where} doesn't lie inside one hit and was ignored"
        result.message = f"{result.message}; {note}" if result.message else note


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


def by_take(manifest: Manifest | None) -> dict[str, TakeResult]:
    """Earlier results by take."""
    return {t.key: t for t in manifest.takes} if manifest else {}


def carry_over(inst: Instrument, result: TakeResult, earlier: TakeResult | None, same_cuts: bool = False) -> None:
    """Keep the review's decisions (rejections, loops, moved lanes) while the recording is unchanged,
    for hits that are still cut where they were: slicing differently may number other hits alike.
    `same_cuts`: cut where it was after an edit, so its hits are the same ones."""
    if earlier and (same_cuts or earlier.source == result.source):
        old = {h.id: h for h in earlier.hits}
        sr = result.sample_rate or 48000
        # A start set by hand may have snapped forward to its attack since (see snap_to_attack).
        snapped = ATTACK_S if result.cut_by_hand and earlier.cut_by_hand else 0
        for h in result.hits:
            was = old.get(h.id)
            if was and -SAME_CUT_S * sr <= h.start - was.start <= (SAME_CUT_S + snapped) * sr:
                h.rejected, h.loop_off, h.layer_set = was.rejected, was.loop_off, was.layer_set
    settle(result, note_lanes(inst, result.note))


def check_all_lanes(inst: Instrument, manifest: Manifest) -> None:
    """Loudness checks across each note's lanes (the hits of one note come from several takes), and
    against their aims, which depend on all notes."""
    targets = aims.aims(inst, manifest)
    sustained = inst.meta.type in SUSTAINED_TYPES
    groups: dict[tuple[str, str], list[TakeResult]] = {}
    for t in manifest.takes:
        if t.kind == "note" and t.note:
            groups.setdefault((t.pass_id, t.note), []).append(t)
    for (pass_id, note), results in groups.items():
        check_lanes(results, note_lanes(inst, note), *targets.of(pass_id, note), sustained=sustained)


def process_one(inst: Instrument, previous: Manifest, key: str) -> Manifest:
    """Slice one recording (again), e.g. right after recording it in the app, keeping all others.

    Falls back to slicing everything for imported samples, or results that no longer match.
    """
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    take = next((t for t in recordings(inst, takes_dir) if t.key == key), None)
    # Samples sliced with the low cut in them (as it was for a while) and a new one without it would
    # sound different, and the build can't tell which is which: slice them all, so none has it.
    if take is None or inst.recording.source == "samples" or previous.setup != slicing_setup(inst) \
            or cut_in_samples(previous):
        return process(inst, previous)
    old = next((t for t in previous.takes if t.key == key), None)
    out_dir = Path(previous.samples_dir)
    p = next(p for p in recording_passes(inst) if p.id == take.pass_id)
    profiles = noise_profiles(inst, takes_dir) if inst.processing.denoise else None
    result = process_take(inst, take, takes_dir, out_dir, noise_floor(p.signals[0].id, takes_dir), profiles)
    carry_over(inst, result, old)
    manifest = previous.model_copy(deep=True)
    if old is None:
        manifest.takes.append(result)
    else:
        manifest.takes = [result if t.key == key else t for t in manifest.takes]
        remove_stale_files(Manifest(samples_dir=previous.samples_dir, takes=[old]), manifest)
    check_all_lanes(inst, manifest)
    return manifest


SAME_PERFORMANCE = 0.3  # least correlation of an edited take's hits with what was sliced from them


def edited_cuts(inst: Instrument, take: Take, takes_dir: Path, earlier: TakeResult | None,
                samples_dir: Path | None) -> list[tuple[int, int, bool]] | None:
    """Where `take` was cut before, if it was edited since in a way that moves no hit: changed, but
    as long as it was, at the same rate, and still the same performance where its hits are, e.g.
    after an EQ (measured: 0.6; another recording: 0.1 at most). Not for a recording made again,
    in the app or elsewhere, nor one without hits to keep: their hits are found anew."""
    if earlier is None or not earlier.hits or not earlier.length or samples_dir is None:
        return None
    paths = [takes_dir / f for f in take.files]
    if not all(p.exists() for p in paths) or fingerprint(paths) == earlier.source:
        return None
    if takes_meta.recorded_now(inst.meta.slug, paths[0]):
        return None
    try:
        info = sf.info(str(paths[0]))
    except RuntimeError:
        return None
    if info.frames != earlier.length or info.samplerate != earlier.sample_rate:
        return None
    if same_performance(paths[0], earlier, samples_dir) < SAME_PERFORMANCE:
        return None
    return [(h.start, h.end, "cut_off" in h.flags) for h in earlier.hits]


def same_performance(path: Path, earlier: TakeResult, samples_dir: Path, hits: int = 3) -> float:
    """How alike the recording at `path` is, where its first hits were cut, to the samples sliced
    there: about 1 for the same audio, still high after an EQ, about 0 for another performance."""
    seconds = 0.5
    scores = []
    for h in earlier.hits[:hits]:
        name = h.files.get(earlier.signals[0]) if earlier.signals else None
        try:
            then, sr = sf.read(str(samples_dir / name), frames=int(seconds * 96000), always_2d=True) if name \
                else (None, 0)
            now, _ = sf.read(str(path), start=h.start, frames=min(h.end - h.start, int(seconds * sr)),
                             always_2d=True) if then is not None else (None, 0)
        except (RuntimeError, OSError, TypeError):
            continue
        if then is None or now is None:
            continue
        n = min(len(then), len(now))
        a, b = now[:n, 0], then[:n, 0]
        scores.append(float(np.dot(a, b) / (np.sqrt(np.dot(a, a) * np.dot(b, b)) + 1e-12)))
    return float(np.median(scores)) if scores else 0.0


def keep_cuts(inst: Instrument, take: Take, takes_dir: Path, result: TakeResult, earlier: TakeResult) -> None:
    """After cutting an edited recording where it was cut: cuts set by hand stay set by hand."""
    if earlier.cut_by_hand and not result.cut_by_hand:
        takes_meta.save_cuts(inst.meta.slug, takes_dir / take.files[0], [(h.start, h.end) for h in result.hits])
        result.cut_by_hand = True


def edited_takes(inst: Instrument, manifest: Manifest) -> list[str]:
    """Keys of the recordings edited since slicing without moving a hit (see edited_cuts)."""
    if inst.recording.source == "samples" or not inst.recording.takes_dir:
        return []
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    earlier = by_take(manifest)
    folder = Path(manifest.samples_dir) if manifest.samples_dir else None
    return [t.key for t in recordings(inst, takes_dir)
            if edited_cuts(inst, t, takes_dir, earlier.get(t.key), folder)]


def recut(inst: Instrument, previous: Manifest, keys: list[str], progress: Progress | None = None) -> Manifest:
    """The edited recordings `keys` cut again where they were cut, their decisions kept; others as
    they were. Fast: no hit is looked for."""
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    out_dir = Path(previous.samples_dir)
    earlier = by_take(previous)
    profiles = noise_profiles(inst, takes_dir) if inst.processing.denoise else None
    passes = {p.id: p for p in recording_passes(inst)}
    takes = [t for t in recordings(inst, takes_dir) if t.key in keys]
    manifest = previous.model_copy(deep=True)
    for i, take in enumerate(takes):
        if progress:
            progress(i, len(takes), take.title)
        old = earlier.get(take.key)
        fixed = edited_cuts(inst, take, takes_dir, old, out_dir)
        if fixed is None:
            continue
        noise = noise_floor(passes[take.pass_id].signals[0].id, takes_dir)
        result = process_take(inst, take, takes_dir, out_dir, noise, profiles, fixed)
        keep_cuts(inst, take, takes_dir, result, old)
        carry_over(inst, result, old, same_cuts=True)
        manifest.takes = [result if t.key == take.key else t for t in manifest.takes]
    check_all_lanes(inst, manifest)
    remove_stale_files(previous, manifest)
    return manifest


def changed_takes(inst: Instrument, manifest: Manifest) -> list[str]:
    """Recordings made, changed or deleted since slicing; the slices are out of date for them."""
    if inst.recording.source == "samples" or not inst.recording.takes_dir:
        return []
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    results = by_take(manifest)
    current = recordings(inst, takes_dir)
    changed = []
    for take in current:
        paths = [takes_dir / f for f in take.files]
        now = fingerprint(paths) if all(path.exists() for path in paths) else ""
        r = results.get(take.key)
        if now != (r.source if r else "") and take.title not in changed:
            changed.append(take.title)
    keys = {t.key for t in current}
    changed += [r.title for r in manifest.takes if r.origin != "plan" and r.key not in keys]
    return changed


def eq_changed_takes(inst: Instrument, manifest: Manifest) -> list[str]:
    """Sliced recordings whose clean-up EQ changed since: their samples don't have the one set now."""
    if inst.recording.source == "samples":
        return []
    return [r.title for r in manifest.takes if r.hits and r.kind != "noise_floor"
            and r.cleanup_eq != cleanup_eq.take_setup(inst, r.key, r.signals)]


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
    if inst.recording.source == "samples":
        from vinst import importer

        return importer.process(inst, previous, progress)
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
    earlier = by_take(previous)
    takes = recordings(inst, takes_dir)
    manifest = Manifest(samples_dir=str(out_dir), setup=slicing_setup(inst), finder=HIT_FINDER,
                        cleanup=cleanup_setup(inst),
                        noise_floor_db=next(iter(noise.values()), None), denoised=profiles is not None)
    for i, take in enumerate(takes):
        if progress:
            progress(i, len(takes), take.title)
        old = earlier.get(take.key)
        # Before process_take overwrites the samples the comparison needs.
        fixed = edited_cuts(inst, take, takes_dir, old, Path(previous.samples_dir) if previous else None)
        result = process_take(inst, take, takes_dir, out_dir, noise[take.pass_id], profiles, fixed)
        if fixed is not None and old is not None:
            keep_cuts(inst, take, takes_dir, result, old)
        carry_over(inst, result, old, same_cuts=fixed is not None)
        manifest.takes.append(result)
    check_all_lanes(inst, manifest)
    if previous:
        remove_stale_files(previous, manifest)
    if progress:
        progress(len(takes), len(takes), "done")
    return manifest
