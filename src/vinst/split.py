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
    PREROLL_S,
    SUBTYPES,
    detect_pitch,
    detect_sounds,
    has_audio,
    heard,
    load_signals,
    mix,
    mix_floor,
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


def find(inst: Instrument, p: Pass, signals: list[np.ndarray], sr: int,
         floors: list[float | None] | None = None) -> Split:
    """Where each note's hits are in the long recording (signals of pass `p`, in its order).
    `floors`: each signal's noise floor, see mix_floor."""
    primary = signals[0]
    heard_signals, heard_floors = heard(signals, floors or [None] * len(signals))
    sounds = detect_sounds(mix(heard_signals), sr, mix_floor(heard_signals, heard_floors), rise_db(inst))
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
        recorded = any(has_audio(takes_dir / f) for f in take.files)
        out.append(Placed(r, take, more=recorded or take.note.midi in seen))
        seen.add(take.note.midi)
    return out


def write(inst: Instrument, signals: list[np.ndarray], sr: int, placed: list[Placed], takes_dir: Path) -> list[str]:
    """Write each placed run as its note's take, or as more hits beside it; nothing recorded is overwritten.
    Returns the keys of the takes written."""
    subtype = SUBTYPES.get(inst.recording.bit_depth, "PCM_24")
    ensure_folder(takes_dir)
    keys = []
    for pl in placed:
        take = pl.take
        if take is None:
            continue
        if any(has_audio(takes_dir / f) for f in take.files):
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


def names_signal(stem: str, signal_id: str) -> bool:
    """Whether a file's name (its stem, lower case) has a signal's id in it as a word of its own."""
    return re.search(rf"(^|[^a-z0-9]){re.escape(signal_id.lower())}([^a-z0-9]|$)", stem) is not None


def by_name(p: Pass, names: list[str]) -> list[int] | None:
    """Which file is each of the pass's signals, by the ids in their names; None if that can't be told."""
    stems = [Path(n).stem.lower() for n in names]
    if len(names) != len(p.signals):
        return None
    order = [next((i for i, st in enumerate(stems) if names_signal(st, s.id)), -1) for s in p.signals]
    return order if -1 not in order and len(set(order)) == len(order) else None


def pass_for(inst: Instrument, names: list[str]) -> tuple[Pass, list[int]] | str:
    """Which pass the uploaded files are, and which file is each of its signals (indices into
    `names`, in the pass's order); or why that can't be told."""
    stems = [Path(n).stem.lower() for n in names]
    passes = recording_passes(inst)
    for p in passes:
        ids = [s.id for s in p.signals]
        if len(ids) == 1 and len(names) == 1 and (len(passes) == 1 or names_signal(stems[0], ids[0])):
            return p, [0]
        if (order := by_name(p, names)) is not None:
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


@dataclass(frozen=True)
class Source:
    """Where a signal is among uploaded files: a file, and one of its channels, or all of them mixed."""

    file: int
    channel: int | None = None


Chosen = tuple[Pass, list[Source]]  # a pass, and where each of its signals is (in its order)


def channel_count(path: Path) -> int:
    return sf.info(str(path)).channels


def told(inst: Instrument, paths: list[Path], pass_id: str | None = None) -> Chosen | None:
    """Where each signal is, when the files tell without asking: by their names, or one file for a pass
    of one signal. With `pass_id`, only for that pass. None: ask (see guess)."""
    names = [p.name for p in paths]
    if pass_id is None:
        which = pass_for(inst, names)
        return None if isinstance(which, str) else (which[0], [Source(i) for i in which[1]])
    p = next((q for q in recording_passes(inst) if q.id == pass_id), None)
    if p is None:
        return None
    if len(p.signals) == 1 and len(paths) == 1:
        return p, [Source(0)]
    order = by_name(p, names)
    return (p, [Source(i) for i in order]) if order is not None else None


def sources_of(paths: list[Path], channels: list[int]) -> list[Source]:
    """Every file's channels one by one, and a file of several channels also mixed."""
    out = []
    for i, n in enumerate(channels):
        out += [Source(i)] if n == 1 else [*(Source(i, c) for c in range(n)), Source(i)]
    return out


def guess(p: Pass, paths: list[Path], channels: list[int]) -> list[Source]:
    """A first answer to where each of the pass's signals is: a file named after it, else the files'
    channels in order (left for the first signal, right for the second)."""
    if (order := by_name(p, [x.name for x in paths])) is not None:
        return [Source(i) for i in order]
    singles = [s for s in sources_of(paths, channels) if s.channel is not None or channels[s.file] == 1]
    if not singles:
        return [Source(0)] * len(p.signals)
    return [singles[min(i, len(singles) - 1)] for i in range(len(p.signals))]


def source_label(paths: list[Path], channels: list[int], src: Source) -> str:
    name = paths[src.file].name
    if channels[src.file] == 1:
        return name
    if src.channel is None:
        return f"{name} · all channels mixed"
    side = {0: "left", 1: "right"}.get(src.channel) if channels[src.file] == 2 else None
    return f"{name} · {side or f'channel {src.channel + 1}'}"


def load_sources(paths: list[Path], sources: list[Source]) -> tuple[list[np.ndarray], int]:
    """Each source's audio, mono, as long as the shortest; files read once however many channels are used."""
    read: dict[int, tuple[np.ndarray, int]] = {}
    signals, rates = [], set()
    for src in sources:
        if src.file not in read:
            data, sr = sf.read(paths[src.file], always_2d=True, dtype="float64")
            if not len(data):
                raise ValueError(f"{paths[src.file].name} is empty")
            read[src.file] = data, sr
        data, sr = read[src.file]
        if src.channel is not None and src.channel >= data.shape[1]:
            raise ValueError(f"{paths[src.file].name} has no channel {src.channel + 1}")
        signals.append(data.mean(axis=1) if src.channel is None else data[:, src.channel])
        rates.add(sr)
    if len(rates) != 1:
        raise ValueError(f"signals have different sample rates: {sorted(rates)}")
    n = min(len(x) for x in signals)
    return [x[:n] for x in signals], rates.pop()


def load(inst: Instrument, paths: list[Path], chosen: Chosen | None = None) -> tuple[Pass, list[np.ndarray], int] | str:
    """The pass and its signals (in its order) of the long recording's files; or why not. `chosen`:
    where each signal is, as answered; else by the files' names."""
    if chosen is not None:
        p, sources = chosen
        signals, sr = load_sources(paths, sources)
        return p, signals, sr
    which = pass_for(inst, [p.name for p in paths])
    if isinstance(which, str):
        return which
    p, order = which
    signals, sr = load_signals([paths[i] for i in order])
    return p, signals, sr


def analyse(inst: Instrument, paths: list[Path], chosen: Chosen | None = None) -> tuple[Pass, Split] | str:
    loaded = load(inst, paths, chosen)
    if isinstance(loaded, str):
        return loaded
    p, signals, sr = loaded
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    return p, find(inst, p, signals, sr, [noise_floor(s.id, takes_dir) for s in p.signals])


def add(inst: Instrument, paths: list[Path], lane: str, found: Split | None = None,
        chosen: Chosen | None = None) -> list[str]:
    """Cut the long recording at `paths` into takes at `lane`; the keys of the takes written.
    `found`: what analyse() found in these files, so it isn't looked for again."""
    loaded = load(inst, paths, chosen)
    if isinstance(loaded, str):
        raise ValueError(loaded)
    p, signals, sr = loaded
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    if found is None:
        found = find(inst, p, signals, sr, [noise_floor(s.id, takes_dir) for s in p.signals])
    return write(inst, signals, sr, place(inst, p, found, lane, takes_dir), takes_dir)
