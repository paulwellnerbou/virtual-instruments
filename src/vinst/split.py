"""Split one long recording of many notes, e.g. the whole scale played in one go, into note takes.

Every hit's pitch says which note it is. Hits in a row on the same note become one take of that
note at the chosen velocity, written as if recorded on its own (or as more hits beside one there
already), so slicing, review and the rest work as for any take. A take ends where the next note's
sound begins, as a hit's tail does when slicing.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

from vinst.importer import CONFIRM_SEMITONES, free_pitch
from vinst.plan import Pass, Take, more_base, more_file, plan_takes, planned_notes, recording_passes, take_key
from vinst.slicing import (
    MIN_NOISE_FLOOR_DB,
    PREROLL_S,
    SUBTYPES,
    db,
    detect_pitch,
    detect_sounds,
    frame_rms_db,
    load_signals,
    mix,
    more_numbers,
    noise_floor,
    rise_db,
)
from vinst.spec import Instrument
from vinst.ui.finder import ensure_folder

PITCH_S = 0.7  # what free_pitch looks at, from the attack
# A take starts this much before its first sound, at most halfway back to the hit before: onset
# detection misses a hit right at the start of a file. Its attack is told from the tail before it
# as in the long recording.
LEAD_S = 0.2


@dataclass
class Run:
    """Hits in a row on one note: one take."""

    midi: int  # may lie outside the instrument's notes
    start: int  # frames in the long recording
    end: int
    hits: int = 0
    unpitched: int = 0  # of them, without a clear pitch: kept with the note before them


@dataclass
class Split:
    sample_rate: int
    length: int
    runs: list[Run] = field(default_factory=list)
    before_first: int = 0  # sounds without a clear pitch before the first note: left out
    corrected: int = 0  # hits read as another note between two of the same: taken as that note

    def hits(self) -> int:
        return sum(r.hits for r in self.runs)


def note_takes(inst: Instrument, pass_id: str, lane: str) -> dict[int, Take]:
    """The take of each note at `lane`, by MIDI; notes without that velocity have none."""
    return {t.note.midi: t for t in plan_takes(inst)
            if t.kind == "note" and t.pass_id == pass_id and t.lane == lane and t.note is not None}


FUNDAMENTAL_SHARE = 0.1  # of the spectrum's highest peak: a fundamental weaker than this isn't one


def fundamental_present(window: np.ndarray, sr: int, midi: int) -> bool:
    """Whether the spectrum has a peak within half a semitone of `midi`'s frequency."""
    seg = window[int(0.04 * sr): int(0.64 * sr)]
    if seg.size < 16:
        return False
    n = max(1 << 16, 1 << int(np.ceil(np.log2(seg.size))))
    spec = np.abs(np.fft.rfft(seg * np.hanning(seg.size), n))
    hz = float(librosa.midi_to_hz(midi))
    lo, hi = (int(hz * f * n / sr) for f in (2 ** (-0.5 / 12), 2 ** (0.5 / 12)))
    return hi > lo and float(spec[lo:hi + 1].max()) >= FUNDAMENTAL_SHARE * float(spec.max())


def note_of(window: np.ndarray, sr: int, planned: set[int]) -> int | None:
    """The note a hit plays, or None without a clear pitch. Off the instrument's notes, YIN may have
    read an octave off. A search near an octave away can't tell: it finds a period twice or half the
    true one. The spectrum can: the true note is the lower of the two if its fundamental is there
    (above it, the higher one's fundamental is the lower one's second harmonic)."""
    found = free_pitch(window, sr)
    if found is None:
        return None
    near = int(round(found))
    if near in planned:
        return near
    for other in (near - 12, near + 12):
        if other not in planned:
            continue
        lower = min(near, other)
        true = lower if fundamental_present(window, sr, lower) else max(near, other)
        if true == other:
            detected = detect_pitch(window, sr, other)
            if detected is not None and abs(detected - other) <= CONFIRM_SEMITONES:
                return other
    return near


def find(inst: Instrument, p: Pass, signals: list[np.ndarray], sr: int, noise_db: float | None = None) -> Split:
    """Where each note's hits are in the long recording (signals of pass `p`, in its order)."""
    ref = mix(signals)
    primary = signals[0]
    floor = noise_db if noise_db is not None else float(np.percentile(frame_rms_db(primary), 10))
    rel_floor = max(floor, MIN_NOISE_FLOOR_DB) - float(db(np.max(np.abs(primary))))
    sounds = detect_sounds(ref, sr, rel_floor, rise_db(inst))
    planned = planned_notes(inst, p.id)
    pre = int(PREROLL_S * sr)
    begins = [max(0, min(b, a) - pre) for a, b in sounds]
    found: list[int | None] = []
    for i, (attack, _) in enumerate(sounds):
        until = min(begins[i + 1] if i + 1 < len(sounds) else len(primary), attack + int(PITCH_S * sr))
        found.append(note_of(primary[attack:until], sr, planned))

    split = Split(sample_rate=sr, length=len(primary))
    pitched = [i for i, m in enumerate(found) if m is not None]
    # One hit read as another note inside a run of the same is a misread or a slip: it stays in their
    # take, where slicing flags a wrong note, instead of splitting that take in two. Only inside a run
    # (two of them on one side): played once each, a scale turning round (A B C B A) keeps its top.
    def at(j: int) -> int | None:
        return found[pitched[j]] if 0 <= j < len(pitched) else None

    for k in range(1, len(pitched) - 1):
        before, here, after = at(k - 1), at(k), at(k + 1)
        if before == after != here and before in (at(k - 2), at(k + 2)):
            found[pitched[k]] = before
            split.corrected += 1

    lead = int(LEAD_S * sr)
    for i, midi in enumerate(found):
        current = split.runs[-1] if split.runs else None
        if midi is None:
            if current is None:
                split.before_first += 1
            else:
                current.hits += 1
                current.unpitched += 1
            continue
        if current is None or current.midi != midi:
            start = max(0, begins[i] - lead)
            if i:
                start = max(start, (sounds[i - 1][0] + begins[i]) // 2)
            if current is not None:
                current.end = begins[i]
            split.runs.append(Run(midi=midi, start=start, end=len(primary)))
            current = split.runs[-1]
        current.hits += 1
    return split


@dataclass
class Placed:
    """Where a run goes at one velocity."""

    run: Run
    take: Take | None  # None: left out, see `why`
    why: str = ""
    more: bool = False  # beside hits already recorded


def place(inst: Instrument, p: Pass, split: Split, lane: str, takes_dir: Path) -> list[Placed]:
    targets = note_takes(inst, p.id, lane)
    planned = planned_notes(inst, p.id)
    seen: set[int] = set()
    out = []
    for r in split.runs:
        take = targets.get(r.midi)
        if take is None:
            why = (f"this note has velocities of its own, without {lane}" if r.midi in planned
                   else "not one of the instrument's notes")
            out.append(Placed(r, None, why))
            continue
        recorded = any((takes_dir / f).exists() for f in take.files)
        out.append(Placed(r, take, more=recorded or take.note.midi in seen))
        seen.add(take.note.midi)
    return out


def write(inst: Instrument, signals: list[np.ndarray], sr: int, placed: list[Placed], takes_dir: Path) -> list[str]:
    """Write each placed run as its note's take, or as more hits beside it; nothing is overwritten.
    Returns the keys of the takes written."""
    subtype = SUBTYPES.get(inst.recording.bit_depth, "PCM_24")
    ensure_folder(takes_dir)
    keys = []
    for pl in placed:
        take = pl.take
        if take is None:
            continue
        if any((takes_dir / f).exists() for f in take.files):
            number = max(more_numbers(take, takes_dir), default=0) + 1
            files, key = [more_file(take, number, s) for s in take.signals], take_key(more_base(take, number),
                                                                                   take.pass_id)
        else:
            files, key = take.files, take.key
        # All signals of a take or none: a take missing a signal can't be sliced.
        parts = [(takes_dir / f"{name}.part", takes_dir / name) for name in files]
        try:
            for (part, _), audio in zip(parts, signals):
                sf.write(part, audio[pl.run.start:pl.run.end], sr, subtype=subtype, format="WAV")
        except BaseException:
            for part, _ in parts:
                part.unlink(missing_ok=True)
            raise
        for part, target in parts:
            part.replace(target)
        keys.append(key)
    return keys


def pass_for(inst: Instrument, names: list[str]) -> tuple[Pass, list[int]] | str:
    """Which pass the uploaded files are, and which file is each of its signals (indices into
    `names`, in the pass's order); or why that can't be told."""
    stems = [Path(n).stem.lower() for n in names]

    def has(stem: str, signal_id: str) -> bool:
        return re.search(rf"(^|[^a-z0-9]){re.escape(signal_id.lower())}([^a-z0-9]|$)", stem) is not None

    passes = recording_passes(inst)
    for p in passes:
        ids = [s.id for s in p.signals]
        if len(ids) == 1 and len(names) == 1 and (len(passes) == 1 or has(stems[0], ids[0])):
            return p, [0]
        if len(names) == len(ids):
            order = [next((i for i, st in enumerate(stems) if has(st, sid)), -1) for sid in ids]
            if -1 not in order and len(set(order)) == len(order):
                return p, order
    example = f"scale_{passes[0].signals[0].id}.wav"
    if len(passes) == 1 and len(passes[0].signals) == 1:
        return "This instrument records one signal: drop one file."
    if len(passes) == 1:
        return (f"Drop one file per signal together ({len(passes[0].signals)} files), each with its signal in "
                f"its name, e.g. {example}.")
    if len(names) > 1 and not any(len(p.signals) == len(names) for p in passes):
        return ("Drop one pass at a time: " + "; ".join(
            f"{p.name}: {len(p.signals)} file{'' if len(p.signals) == 1 else 's'}" for p in passes) + ".")
    wanted = " or ".join(" + ".join(s.id for s in p.signals) for p in passes)
    return f"Which signal is it? Put {wanted} in the file name, e.g. {example}."


def load(inst: Instrument, paths: list[Path]) -> tuple[Pass, list[np.ndarray], int] | str:
    """The pass and its signals (in its order) of the long recording's files; or why not."""
    which = pass_for(inst, [p.name for p in paths])
    if isinstance(which, str):
        return which
    p, order = which
    signals, sr = load_signals([paths[i] for i in order])
    return p, signals, sr


def analyse(inst: Instrument, paths: list[Path]) -> tuple[Pass, Split] | str:
    loaded = load(inst, paths)
    if isinstance(loaded, str):
        return loaded
    p, signals, sr = loaded
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    return p, find(inst, p, signals, sr, noise_floor(p.signals[0].id, takes_dir))


def add(inst: Instrument, paths: list[Path], lane: str, found: Split | None = None) -> list[str]:
    """Cut the long recording at `paths` into takes at `lane`; the keys of the takes written.
    `found`: what analyse() found in these files, so it isn't looked for again."""
    loaded = load(inst, paths)
    if isinstance(loaded, str):
        raise ValueError(loaded)
    p, signals, sr = loaded
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    if found is None:
        found = find(inst, p, signals, sr, noise_floor(p.signals[0].id, takes_dir))
    return write(inst, signals, sr, place(inst, p, found, lane, takes_dir), takes_dir)
