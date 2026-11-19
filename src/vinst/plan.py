"""Derive what to record from a spec: sampled notes, takes, expected file names, time estimate."""

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from vinst import notes
from vinst.spec import Instrument, InstrumentString, Signal, file_token

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


MAIN_PASS = "main"


@dataclass(frozen=True)
class Pass:
    """One recording run through all takes; its signals are recorded simultaneously."""

    id: str
    name: str
    signals: tuple[Signal, ...]


def recording_passes(inst: Instrument) -> list[Pass]:
    together = [s for s in inst.signals if s.recorded == "together"]
    out = [Pass(MAIN_PASS, " + ".join(s.name for s in together), tuple(together))] if together else []
    out += [Pass(s.id, s.name, (s,)) for s in inst.signals if s.recorded == "separate"]
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


def plan_takes(inst: Instrument) -> list[Take]:
    rec, dyn = inst.recording, inst.dynamics
    hit_s = rec.ring_seconds + HIT_GAP_S
    verb = "hold" if inst.meta.type == "sustained" else "let ring"
    passes = recording_passes(inst)
    takes: list[Take] = []

    for p in passes:
        suffix = f" · {p.name}" if len(passes) > 1 else ""

        def add(kind: str, base: str, title: str, instructions: list[str], hits: int, seconds: float,
                note: SampledNote | None = None, p: Pass = p, suffix: str = suffix) -> None:
            takes.append(Take(
                number=len(takes) + 1, kind=kind, base_name=base, title=title + suffix,
                instructions=instructions, hits=hits, seconds=seconds + TAKE_OVERHEAD_S, note=note,
                files=[f"{base}_{s.id}.wav" for s in p.signals],
                pass_id=p.id, pass_name=p.name, signals=[s.id for s in p.signals],
            ))

        if rec.noise_floor_seconds:
            add("noise_floor", "noisefloor", "Noise floor",
                [f"Record {rec.noise_floor_seconds} s of silence with everything set up as for the takes."],
                0, rec.noise_floor_seconds)
        for n in sampled_notes(inst):
            hits = len(dyn.layers) * dyn.round_robins
            instr = [f"{dyn.round_robins}× {layer}" for layer in dyn.layers]
            instr.append(f"{verb} ~{rec.ring_seconds:g} s each")
            add("note", note_base_name(n), note_title(n), instr, hits, hits * hit_s, n)
        for x in inst.extras:
            add("extra", f"x_{file_token(x.id)}", x.name, [f"{x.hits}×", x.instructions] if x.instructions
                else [f"{x.hits}×"], x.hits, x.hits * (EXTRA_HIT_S + HIT_GAP_S))
    return takes


def slicing_setup(inst: Instrument) -> str:
    """Fingerprint of everything slicing depends on: takes, their files and hit counts, layer names.

    Stored in hits.json; when it no longer matches, the hits don't fit the spec any more (e.g. a
    signal moved to its own pass, a layer was renamed) and the takes must be sliced again.
    """
    takes = [(t.key, t.kind, t.files, t.hits) for t in plan_takes(inst)]
    data = json.dumps({"takes": takes, "layers": inst.dynamics.layers, "rr": inst.dynamics.round_robins,
                       "loops": inst.processing.loops})
    return hashlib.sha1(data.encode()).hexdigest()[:16]


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


def recorded_files(inst: Instrument, takes: list[Take] | None = None) -> int:
    folder = Path(inst.recording.takes_dir).expanduser() if inst.recording.takes_dir else None
    if folder is None or not folder.is_dir():
        return 0
    present = _listing(str(folder), folder.stat().st_mtime_ns)
    return sum(f in present for t in (takes if takes is not None else plan_takes(inst)) for f in t.files)


def summarize(inst: Instrument) -> Summary:
    takes = plan_takes(inst)
    span = playable_range(inst)
    return Summary(
        range_text=f"{notes.label(span[0])} – {notes.label(span[1])}" if span else "–",
        sampled=len({t.base_name for t in takes if t.kind == "note"}),
        takes=len(takes),
        hits=sum(t.hits for t in takes),
        files=sum(len(t.files) for t in takes),
        minutes=sum(t.seconds for t in takes) / 60,
        sample_mb=sample_megabytes(inst, takes),
        recorded_files=recorded_files(inst, takes),
    )
