"""Derive what to record from a spec: sampled notes, their velocity lanes, takes and file names.

Every note is recorded once per velocity lane (and pass): a take holds only variations of one
dynamic, so a hit's lane is known from its take and changing lanes never re-assigns hits.
"""

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from vinst import notes
from vinst.spec import MAIN_PASS, Instrument, InstrumentString, Signal, file_token

# Seconds per take for tuning, arming and naming, on top of the hits themselves.
TAKE_OVERHEAD_S = 30.0
HIT_GAP_S = 1.0
EXTRA_HIT_S = 2.0  # typical length of a noise / knock sample


@dataclass(frozen=True)
class SampledNote:
    midi: int
    string: str | None = None
    fret: int | None = None
    sound: str | None = None


@dataclass(frozen=True)
class Pass:
    """One recording run through all takes; its signals are recorded simultaneously."""

    id: str
    name: str
    signals: tuple[Signal, ...]


def recording_passes(inst: Instrument) -> list[Pass]:
    """Signals grouped by pass: the main pass first, as the instrument's default one, then the others
    in the order of their first signal."""
    ids = sorted(dict.fromkeys(s.pass_id for s in inst.signals), key=lambda i: i != MAIN_PASS)
    out = []
    for pass_id in ids:
        signals = tuple(s for s in inst.signals if s.pass_id == pass_id)
        out.append(Pass(pass_id, " + ".join(s.name for s in signals), signals))
    return out


def take_key(base_name: str, pass_id: str) -> str:
    """Unique id of a take; the same note is recorded once per pass."""
    return base_name if pass_id == MAIN_PASS else f"{base_name}~{pass_id}"


@dataclass
class Take:
    number: int
    kind: str  # "note" | "extra" | "noise_floor"
    base_name: str
    title: str
    instructions: list[str]
    hits: int
    seconds: float
    note: SampledNote | None = None
    files: list[str] = field(default_factory=list)
    pass_id: str = MAIN_PASS
    pass_name: str = ""
    signals: list[str] = field(default_factory=list)  # signal ids, in the order of `files`
    lane: str | None = None  # a note take's velocity lane
    note_base: str = ""  # a note take's note, e.g. "060_C-0"; its lanes' takes share it
    origin: str = "plan"  # "plan" | "more" (variations recorded later) | "combined" (lanes in one take)

    @property
    def key(self) -> str:
        return take_key(self.base_name, self.pass_id)


def playable_range(inst: Instrument) -> tuple[int, int] | None:
    return inst.range.span()


def natural_string(strings: list[InstrumentString], midi: int) -> tuple[InstrumentString, int] | None:
    """The string that plays `midi` at the lowest fret; ties go to the string listed first."""
    best: tuple[InstrumentString, int] | None = None
    for s in strings:
        fret = midi - s.open
        if 0 <= fret <= s.frets and (best is None or fret < best[1]):
            best = (s, fret)
    return best


def _density_steps(low: int, high: int, density: int) -> list[int]:
    steps = list(range(low, high + 1, density))
    if steps and steps[-1] != high:
        steps.append(high)
    return steps


def sampled_notes(inst: Instrument) -> list[SampledNote]:
    r = inst.range
    if r.mode == "sounds":
        return [SampledNote(midi=s.key, sound=s.name) for s in sorted(r.sounds, key=lambda s: s.key)]
    span = playable_range(inst)
    if span is None:
        return []
    out: list[SampledNote] = []
    for midi in _density_steps(*span, r.density):
        if r.mode == "strings":
            hit = natural_string(r.strings, midi)
            if hit is None:  # gap between strings
                continue
            out.append(SampledNote(midi=midi, string=hit[0].name, fret=hit[1]))
        else:
            out.append(SampledNote(midi=midi))
    return out


def note_base_name(n: SampledNote) -> str:
    if n.sound is not None:
        return f"{n.midi:03d}_{file_token(n.sound)}"
    if n.string is not None:
        return f"{n.midi:03d}_{file_token(n.string)}-{n.fret}"
    return f"{n.midi:03d}"


def note_title(n: SampledNote) -> str:
    if n.sound is not None:
        return f"{n.sound} → key {notes.label(n.midi)}"
    if n.string is not None:
        where = "open" if n.fret == 0 else f"fret {n.fret}"
        return f"{notes.label(n.midi)} · {n.string} string, {where}"
    return notes.label(n.midi)


def valid_splits(n: int, splits: list[int]) -> bool:
    """Whether `splits` can start lanes 2..n: one start each, rising, so every lane keeps a velocity."""
    return len(splits) == n - 1 and splits == sorted(set(splits)) and all(2 <= v <= 127 for v in splits)


def layer_bounds(n: int, splits: list[int] | None = None) -> list[tuple[int, int]]:
    """Velocities 1..127 per lane: from the chosen splits (each lane's lowest velocity after the
    first), or evenly."""
    if splits and valid_splits(n, splits):
        edges = [1, *splits, 128]
    else:
        edges = [1 + round(i * 127 / n) for i in range(n)] + [128]
    return [(edges[i], edges[i + 1] - 1) for i in range(n)]


def note_lanes(inst: Instrument, note_base: str) -> list[str]:
    """A note's velocity lanes: its own, or the defaults."""
    own = inst.note_lanes.get(note_base)
    return list(own.layers) if own and own.layers else list(inst.dynamics.layers)


def lane_bounds(inst: Instrument, note_base: str) -> list[tuple[str, int, int]]:
    """(lane, lowest velocity, highest velocity) for a note."""
    own = inst.note_lanes.get(note_base)
    lanes = note_lanes(inst, note_base)
    splits = own.splits if own and own.layers else inst.preset.velocity_splits
    return [(lane, lo, hi) for lane, (lo, hi) in zip(lanes, layer_bounds(len(lanes), splits))]


def plan_takes(inst: Instrument) -> list[Take]:
    rec, dyn = inst.recording, inst.dynamics
    hit_s = rec.ring_seconds + HIT_GAP_S
    verb = "hold" if inst.meta.type == "sustained" else "let ring"
    passes = recording_passes(inst)
    takes: list[Take] = []

    for p in passes:
        suffix = f" · {p.name}" if len(passes) > 1 else ""

        def add(kind: str, base: str, title: str, instructions: list[str], hits: int, seconds: float,
                note: SampledNote | None = None, lane: str | None = None, note_base: str = "",
                p: Pass = p, suffix: str = suffix) -> None:
            takes.append(Take(
                number=len(takes) + 1, kind=kind, base_name=base, title=title + suffix,
                instructions=instructions, hits=hits, seconds=seconds + TAKE_OVERHEAD_S, note=note,
                files=[f"{base}_{s.id}.wav" for s in p.signals],
                pass_id=p.id, pass_name=p.name, signals=[s.id for s in p.signals], lane=lane, note_base=note_base,
            ))

        if rec.noise_floor_seconds:
            add("noise_floor", "noisefloor", "Noise floor",
                [f"Record {rec.noise_floor_seconds} s of silence with everything set up as for the takes."],
                0, rec.noise_floor_seconds)
        for n in sampled_notes(inst):
            base = note_base_name(n)
            for lane in note_lanes(inst, base):
                add("note", f"{base}_{lane}", f"{note_title(n)} · {lane}",
                    [f"{dyn.round_robins}× {lane}", f"{verb} ~{rec.ring_seconds:g} s each"],
                    dyn.round_robins, dyn.round_robins * hit_s, n, lane, base)
        for x in inst.extras:
            add("extra", f"x_{file_token(x.id)}", x.name, [f"{x.hits}×", x.instructions] if x.instructions
                else [f"{x.hits}×"], x.hits, x.hits * (EXTRA_HIT_S + HIT_GAP_S))
    return takes


def planned_notes(inst: Instrument, pass_id: str) -> set[int]:
    return {t.note.midi for t in plan_takes(inst) if t.kind == "note" and t.pass_id == pass_id and t.note}


def more_base(take: Take, number: int) -> str:
    """Variations of a lane recorded later, to compare with the lane's own and keep the best."""
    return f"{take.base_name}-more{number}"


def more_file(take: Take, number: int, signal_id: str) -> str:
    return f"{more_base(take, number)}_{signal_id}.wav"


def combined_base(take: Take) -> str:
    """Takes recorded before notes had lanes held all dynamics in one file, named after the note."""
    return take.note_base


def slicing_setup(inst: Instrument) -> str:
    """Fingerprint of what the meaning of sliced hits depends on: signals, passes, loop finding.

    Stored in hits.json; when it no longer matches, the hits must be sliced again. Lanes and
    variations aren't part of it: a hit's lane comes from its take, so adding a lane or a
    variation only adds takes to record.
    """
    if inst.recording.source == "samples":
        from vinst.importer import import_setup

        return import_setup(inst)
    setup = {"passes": [(p.id, [s.id for s in p.signals]) for p in recording_passes(inst)],
             "loops": inst.processing.loops}
    return hashlib.sha1(json.dumps(setup).encode()).hexdigest()[:16]


@dataclass
class Summary:
    range_text: str
    sampled: int
    takes: int
    hits: int
    files: int
    minutes: float
    sample_mb: float
    recorded_files: int  # expected take files already present in recording.takes_dir


def sample_megabytes(inst: Instrument, takes: list[Take] | None = None) -> float:
    """Estimated size of the sliced samples in the finished library (uncompressed WAV, mono per signal)."""
    rec = inst.recording
    seconds = sum(
        t.hits * (rec.ring_seconds if t.kind == "note" else EXTRA_HIT_S) * len(t.files)
        for t in (takes if takes is not None else plan_takes(inst)) if t.kind != "noise_floor"
    )
    return seconds * rec.sample_rate * rec.bit_depth / 8 / 1e6


@lru_cache(maxsize=8)
def _listing(folder: str, mtime_ns: int) -> frozenset[str]:
    # Keyed on the folder's mtime, which changes when files are added or removed, so the
    # (possibly external, spun-down) disk is only listed again when something changed.
    return frozenset(p.name for p in Path(folder).iterdir())


def expected_files(inst: Instrument, takes: list[Take] | None = None) -> list[str]:
    return [f for t in (takes if takes is not None else plan_takes(inst)) for f in t.files]


def recorded_files(inst: Instrument, takes: list[Take] | None = None) -> int:
    folder = Path(inst.recording.takes_dir).expanduser() if inst.recording.takes_dir else None
    if folder is None or not folder.is_dir():
        return 0
    present = _listing(str(folder), folder.stat().st_mtime_ns)
    count = 0
    for t in takes if takes is not None else plan_takes(inst):
        if all(f in present for f in t.files):
            count += len(t.files)
        elif t.kind == "note" and t.lane == note_lanes(inst, t.note_base)[0] \
                and all(f"{combined_base(t)}_{s}.wav" in present for s in t.signals):
            count += len(t.files)  # a take from before lanes counts for the note's first lane
        else:
            count += sum(f in present for f in t.files)
    return count


def summarize(inst: Instrument) -> Summary:
    takes = plan_takes(inst)
    span = playable_range(inst)
    return Summary(
        range_text=f"{notes.label(span[0])} – {notes.label(span[1])}" if span else "–",
        sampled=len({t.note_base for t in takes if t.kind == "note"}),
        takes=len(takes),
        hits=sum(t.hits for t in takes),
        files=len(expected_files(inst, takes)),
        minutes=sum(t.seconds for t in takes) / 60,
        sample_mb=sample_megabytes(inst, takes),
        recorded_files=recorded_files(inst, takes),
    )
