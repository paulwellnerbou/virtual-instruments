"""Import a folder of already-cut samples (one note per file) instead of slicing recorded takes.

The result is the same hits.json the slicer writes, so review, sound settings and building work
unchanged. Files are copied under uniform names into the samples folder; the imported folder is
only read.
"""

import hashlib
import json
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from vinst import loops, notes
from vinst.review import Hit, Manifest, TakeResult, samples_dir
from vinst.slicing import (
    FADE_OUT_S,
    LOUDNESS_WINDOW_S,
    Progress,
    body_level,
    db,
    detect_pitch,
    envelope,
    refine_pitch,
    remove_stale_files,
)
from vinst.spec import AUDIO_SUFFIXES, Instrument

NOTE_NAME = re.compile(r"(?<![A-Za-z])([A-Ga-g])(#|b|s)?(-?\d)(?![0-9])")
NUMBER = re.compile(r"(?<![0-9])(\d{1,3})(?![0-9])")
VELOCITY_TOKEN = re.compile(r"^(?:v|vel|velocity|layer|l)(\d+)$")
RR_TOKEN = re.compile(r"^(?:rr|r|take|t)(\d+)$")
CONFIRM_SEMITONES = 0.6
MIN_PERIODICITY = 0.5
GAP_S = 0.25  # between hits in the review waveform


@dataclass
class NameInfo:
    """What a file name says about the sample."""

    names: list[tuple[int, int]] = field(default_factory=list)  # MIDI if middle C is C3, if it is C4
    numbers: list[int] = field(default_factory=list)  # MIDI note numbers 0..127
    layer: str | None = None
    velocity_rank: int | None = None
    rr: int | None = None
    signal: str | None = None


def tokens(stem: str) -> list[str]:
    return [t for t in re.split(r"[^A-Za-z0-9#]+", stem) if t]


def parse_name(stem: str, layers: list[str], signal_ids: list[str]) -> NameInfo:
    info = NameInfo()
    masked = stem
    for m in NOTE_NAME.finditer(stem):
        letter, accidental, octave = m.group(1).upper(), m.group(2) or "", int(m.group(3))
        if (accidental in ("#", "s") and letter in "EB") or (accidental == "b" and letter in "CF"):
            continue
        pc = notes.parse(f"{letter}0") - 24 + {"#": 1, "s": 1, "b": -1, "": 0}[accidental]
        c3 = pc + 12 * (octave + 2)
        c4 = c3 - 12  # the same name is an octave lower when middle C is called C4
        if 0 <= c3 <= 127 or 0 <= c4 <= 127:
            info.names.append((c3, c4))
        masked = masked[:m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
    info.numbers = [int(n) for n in NUMBER.findall(masked) if 0 <= int(n) <= 127]
    lower_layers = {name.lower(): name for name in layers}
    lower_signals = {s.lower(): s for s in signal_ids}
    for t in tokens(stem):
        low = t.lower()
        if low in lower_signals:
            info.signal = lower_signals[low]
        elif low in lower_layers:
            info.layer = lower_layers[low]
        elif m := VELOCITY_TOKEN.match(low):
            info.velocity_rank = int(m.group(1))
        elif m := RR_TOKEN.match(low):
            info.rr = int(m.group(1))
    return info


def candidates(info: NameInfo, convention: str) -> list[int]:
    """Possible notes from the name, most likely first."""
    out = [c3 if convention == "c3" else c4 for c3, c4 in info.names]
    out += [c4 if convention == "c3" else c3 for c3, c4 in info.names]
    out += [n for n in info.numbers if n >= 12] + [n for n in info.numbers if n < 12]
    return [n for i, n in enumerate(out) if 0 <= n <= 127 and n not in out[:i]]


def free_pitch(audio: np.ndarray, sr: int) -> float | None:
    """Pitch without a hint from the name (wider search, so octave errors are possible)."""
    window = audio[int(0.04 * sr): int(0.64 * sr)]
    frame = 4096 if sr <= 48000 else 8192
    if len(window) < frame:
        return None
    f0 = librosa.yin(window, fmin=27.5, fmax=4200, sr=sr, frame_length=frame, hop_length=frame // 4)
    midi = librosa.hz_to_midi(f0[np.isfinite(f0)])
    if midi.size < 3:
        return None
    centre = float(np.median(midi))
    if np.mean(np.abs(midi - centre) < 0.3) < 0.6:
        return None
    # On noise YIN settles on its search limits, which looks stable: also require that the
    # sound actually repeats after one period.
    if not 21.5 < centre < 107.5 or periodicity(window, sr, float(librosa.midi_to_hz(centre))) < MIN_PERIODICITY:
        return None
    return refine_pitch(window, sr, centre)


def periodicity(window: np.ndarray, sr: int, hz: float) -> float:
    """Normalised autocorrelation at one period: near 1 for a pitched tone, near 0 for noise."""
    lag = int(round(sr / hz))
    a, b = window[:-lag], window[lag:]
    denom = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    return float(np.dot(a, b) / denom) if lag < len(window) and denom > 0 else 0.0


@dataclass
class Sample:
    """One imported hit: one file per signal."""

    key: str
    files: dict[str, Path]
    info: NameInfo
    audio: np.ndarray  # primary signal, mono
    sr: int
    peaks: dict[str, float] = field(default_factory=dict)  # dBFS per signal file
    midi: int | None = None
    detected: float | None = None
    flags: list[str] = field(default_factory=list)


def attack_start(audio: np.ndarray, sr: int) -> int:
    """Where the sound starts: imported files may begin with silence, unlike sliced hits."""
    loud = np.nonzero(np.abs(audio) >= 0.1 * max(float(np.max(np.abs(audio))), 1e-9))[0]
    return max(0, int(loud[0]) - int(0.005 * sr)) if loud.size else 0


def choose_note(s: Sample, convention: str) -> None:
    cands = candidates(s.info, convention)
    sound = s.audio[attack_start(s.audio, s.sr):]
    for c in cands:
        detected = detect_pitch(sound, s.sr, c)
        if detected is not None and abs(detected - c) <= CONFIRM_SEMITONES:
            s.midi, s.detected = c, detected
            return
    detected = free_pitch(sound, s.sr)
    if detected is not None:
        s.midi, s.detected = int(round(detected)), detected
        if cands and s.midi not in cands:
            s.flags.append("name_mismatch")
        return
    if cands:
        s.midi = cands[0]
        s.flags.append("no_pitch")


def assign_layers(group: list[Sample], layers: list[str]) -> list[tuple[str, Sample]]:
    """Layer per sample: from the name, else from a velocity number, else by loudness."""
    if all(s.info.layer for s in group):
        return [(s.info.layer, s) for s in group]  # type: ignore[misc]
    if all(s.info.velocity_rank is not None for s in group):
        ranks = sorted({s.info.velocity_rank for s in group})
        return [(layers[min(len(layers) - 1, round(ranks.index(s.info.velocity_rank) * (len(layers) - 1)
                                                         / max(1, len(ranks) - 1)))], s) for s in group]
    def level(s: Sample) -> float:
        sound = s.audio[attack_start(s.audio, s.sr):]
        return float(np.sqrt(np.mean(sound[: int(LOUDNESS_WINDOW_S * s.sr)] ** 2)))

    by_level = sorted(group, key=level)
    n, k = len(layers), len(by_level)
    # Spread the quietest..loudest samples evenly over the layers.
    return [(layers[min(n - 1, i * n // k)], s) for i, s in enumerate(by_level)]


def natural_key(text: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", text)]


def audio_files(folder: Path) -> list[Path]:
    return sorted((p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_SUFFIXES
                   and not any(part.startswith(".") for part in p.relative_to(folder).parts)),
                  key=lambda p: natural_key(str(p.relative_to(folder))))


def import_setup(inst: Instrument) -> str:
    """Fingerprint of the import: the files (name, size, date) and the settings that shape it."""
    folder = Path(inst.recording.import_dir).expanduser()
    files = [(str(p.relative_to(folder)), p.stat().st_size, p.stat().st_mtime_ns)
             for p in audio_files(folder)] if folder.is_dir() else []
    data = json.dumps({"source": "samples", "files": files, "signals": [s.id for s in inst.signals],
                       "layers": inst.dynamics.layers, "names": inst.recording.import_note_names,
                       "loops": inst.processing.loops})
    return hashlib.sha1(data.encode()).hexdigest()[:16]


def load(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(path, always_2d=True, dtype="float64")
    return data.mean(axis=1), sr


def group_files(folder: Path, inst: Instrument) -> tuple[list[Sample], list[str]]:
    """Files belonging to one hit (one per signal) share their name apart from the signal id."""
    signal_ids = [s.id for s in inst.signals]
    groups: dict[str, dict[str, Path]] = defaultdict(dict)
    infos: dict[str, NameInfo] = {}
    problems: list[str] = []
    for path in audio_files(folder):
        info = parse_name(path.stem, inst.dynamics.layers, signal_ids)
        signal = info.signal or signal_ids[0]
        key = str(path.relative_to(folder).with_suffix(""))
        if info.signal:
            key = re.sub(rf"(?i)(?<![A-Za-z0-9]){re.escape(info.signal)}(?![A-Za-z0-9])", "", key)
        if signal in groups[key]:
            problems.append(f"{path.name}: another file already provides {signal} for “{key}”")
            continue
        groups[key][signal] = path
        infos.setdefault(key, info)
    samples = []
    for key, files in groups.items():
        primary_id = next(s for s in signal_ids if s in files)
        peaks, audio, sr = {}, np.zeros(0), 0
        for sig, path in files.items():  # each file is read once; only the primary's audio is kept
            data, rate = load(path)
            peaks[sig] = round(float(db(np.max(np.abs(data)))), 2) if data.size else -200.0
            if sig == primary_id:
                audio, sr = data, rate
        sample = Sample(key, files, infos[key], audio, sr, peaks)
        if set(files) != set(signal_ids):
            sample.flags.append("incomplete")
        samples.append(sample)
    return samples, problems


def process(inst: Instrument, previous: Manifest | None = None, progress: Progress | None = None) -> Manifest:
    problems = inst.problems()
    if problems:
        raise ValueError("Fix these first: " + " ".join(problems))
    folder = Path(inst.recording.import_dir).expanduser()
    if not folder.is_dir():
        raise ValueError(f"Import folder not found: {folder}")
    out_dir = samples_dir(inst)
    assert out_dir is not None
    if out_dir.resolve() == folder.resolve() or out_dir.resolve().is_relative_to(folder.resolve()):
        # The copies would be imported again next time, as extra round robins.
        raise ValueError(f"The copies would go into the folder you import from ({out_dir}). "
                         "Choose another folder for them (step 6, Change…) or use the default.")
    out_dir.mkdir(parents=True, exist_ok=True)

    samples, problems = group_files(folder, inst)
    if not samples:
        raise ValueError(f"No audio files (WAV, AIFF, FLAC) in {folder}")
    for i, s in enumerate(samples):
        if progress:
            progress(i, len(samples), s.key)
        choose_note(s, inst.recording.import_note_names)
    unassigned = [s for s in samples if s.midi is None]
    problems += [f"{s.key}: no note in the name and no clear pitch; left out" for s in unassigned]

    by_note: dict[int, list[Sample]] = defaultdict(list)
    for s in samples:
        if s.midi is not None:
            by_note[s.midi].append(s)

    previous_takes = {t.key: t for t in previous.takes} if previous else {}
    signal_ids = [s.id for s in inst.signals]
    manifest = Manifest(samples_dir=str(out_dir), setup=import_setup(inst), source="samples")
    for number, midi in enumerate(sorted(by_note), 1):
        result = note_result(inst, number, midi, by_note[midi], out_dir, signal_ids)
        old = previous_takes.get(result.key)
        if old is not None and old.source == result.source:
            keep = {h.id for h in old.hits if h.rejected}
            no_loop = {h.id for h in old.hits if h.loop_off}
            for h in result.hits:
                h.rejected = h.rejected or h.id in keep
                h.loop_off = h.id in no_loop
        manifest.takes.append(result)
    if problems:
        manifest.takes.append(TakeResult(number=len(manifest.takes) + 1, base="unassigned", title="Not imported",
                                         kind="extra", status="error", message="; ".join(problems),
                                         expected_hits=0))
    if previous:
        remove_stale_files(previous, manifest)
    if progress:
        progress(len(samples), len(samples), "done")
    return manifest


def note_result(inst: Instrument, number: int, midi: int, group: list[Sample], out_dir: Path,
                signal_ids: list[str]) -> TakeResult:
    layered = assign_layers(group, inst.dynamics.layers)
    rr: dict[str, int] = defaultdict(int)
    ordered = sorted(layered, key=lambda ls: (inst.dynamics.layers.index(ls[0]),
                                              ls[1].info.rr if ls[1].info.rr is not None else 0,
                                              natural_key(ls[1].key)))
    result = TakeResult(number=number, base=f"{midi:03d}", title=notes.label(midi), kind="note", status="ok",
                        expected_hits=len(group), midi=midi, signals=signal_ids,
                        source=";".join(f"{p.name}:{p.stat().st_size}:{p.stat().st_mtime_ns}"
                                        for s in group for p in s.files.values()))
    gap = int(GAP_S * group[0].sr)
    parts: list[np.ndarray] = []
    pos = 0
    for index, (layer, s) in enumerate(ordered):
        rr[layer] += 1
        hit_id = f"{midi:03d}_{layer}_rr{rr[layer]}"
        files = {}
        for sig, path in s.files.items():
            name = f"{hit_id}_{sig}{path.suffix.lower()}"
            target = out_dir / name
            st = path.stat()
            if not (target.exists() and target.stat().st_size == st.st_size
                    and int(target.stat().st_mtime) == int(st.st_mtime)):
                shutil.copy2(path, target)
            files[sig] = name
        sound = s.audio[attack_start(s.audio, s.sr):]
        hit = Hit(id=hit_id, index=index, layer=layer, rr=rr[layer], start=pos, end=pos + len(s.audio),
                  files=files, rms_db=round(float(db(np.sqrt(np.mean(sound[: int(LOUDNESS_WINDOW_S * s.sr)] ** 2)))), 2),
                  body_db=body_level(sound, s.sr),
                  peak_db=dict(s.peaks), flags=list(s.flags), sample_rate=s.sr)
        if s.detected is not None:
            hit.midi_detected = round(s.detected, 3)
            hit.cents = round((s.detected - midi) * 100, 1)
            if abs(hit.cents) > 20:
                hit.flags.append("out_of_tune")
        if any(p >= -0.02 for p in hit.peak_db.values()):
            hit.flags.append("clipped")
        if "incomplete" in hit.flags:
            hit.rejected = True  # its missing signal file can't be mapped
        if inst.processing.loops:
            add_import_loop(inst, hit, s)
        result.hits.append(hit)
        parts += [s.audio, np.zeros(gap)]
        pos += len(s.audio) + gap
    result.sample_rate, result.length = group[0].sr, pos
    result.envelope = envelope(np.concatenate(parts))
    return result


def add_import_loop(inst: Instrument, hit: Hit, s: Sample) -> None:
    """Loop markers saved in the file itself win; otherwise search for a loop."""
    primary = next(iter(s.files.values()))
    marked = loops.read_wav_loops(primary)
    loop = loops.Loop(marked[0][0], marked[0][1], 1.0, "marker") if marked else None
    if loop is None:
        freq = float(librosa.midi_to_hz(hit.midi_detected)) if hit.midi_detected is not None else None
        crossfade = int(inst.processing.loop_crossfade_ms / 1000 * s.sr)
        loop = loops.find_loop(s.audio, s.sr, freq, tail_guard=int((FADE_OUT_S + 0.05) * s.sr), crossfade=crossfade)
    if loop is None:
        hit.flags.append("no_loop")
        return
    hit.loop_start, hit.loop_end, hit.loop_score, hit.loop_source = loop.start, loop.end, loop.score, loop.source
    if loop.source == "auto" and loop.score < loops.POOR_MATCH:
        hit.flags.append("loop_poor")
