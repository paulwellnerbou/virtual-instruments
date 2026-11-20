"""How loud to record each velocity of a note, next to its loudest one.

With loudness following velocity (see vinst.velocity) the instrument sets the loudness itself, so the
recordings of a note's velocities only need to sound like their dynamic. Played alike on every note,
they are about the same distance apart; a note whose soft velocity was played harder or softer than
on the others sounds like a different dynamic there (and without the curve, jumps further). The aim is the instrument's
own step: its typical dB per velocity between the middles of two neighbouring velocities, as
recorded, so a note with its own split, or a third velocity, gets an aim to match.
"""

from dataclasses import dataclass, field
from statistics import median

from vinst.plan import lane_bounds, note_lanes
from vinst.review import Manifest, lane_of, loudness
from vinst.spec import SUSTAINED_TYPES, Instrument

# Until this many notes have two velocities recorded, the step is a typical one: 13 dB between
# velocities split at 81, as measured on a ukulele played p and f.
MIN_NOTES = 3
DEFAULT_DB_PER_VELOCITY = 0.2

Levels = dict[str, list[float]]  # velocity -> loudness of its kept hits, on one note
Bounds = list[tuple[str, int, int]]  # (velocity, lowest, highest), from the softest


def middle(lo: int, hi: int) -> float:
    return (lo + hi) / 2


def note_steps(levels: Levels, bounds: Bounds) -> list[float]:
    """dB per velocity between each two neighbouring recorded velocities of one note."""
    recorded = [b for b in bounds if levels.get(b[0])]
    out = []
    for (a, alo, ahi), (b, blo, bhi) in zip(recorded, recorded[1:]):
        gap = median(levels[b]) - median(levels[a])
        distance = middle(blo, bhi) - middle(alo, ahi)
        if gap > 0 and distance > 0:  # one out of order is a problem of its own (see review.check_lanes)
            out.append(gap / distance)
    return out


def db_per_velocity(notes: list[tuple[Levels, Bounds]]) -> tuple[float, bool]:
    """The instrument's step, and whether it was measured (else the default)."""
    # One per note, so notes with three velocities don't count twice.
    measured = [median(s) for s in (note_steps(levels, bounds) for levels, bounds in notes) if s]
    if len(measured) < MIN_NOTES:
        return DEFAULT_DB_PER_VELOCITY, False
    return median(measured), True


def reference(levels: Levels, bounds: Bounds) -> str | None:
    """The note's loudest recorded velocity: the others are aimed at from it."""
    return next((lane for lane, _, _ in reversed(bounds) if levels.get(lane)), None)


def note_aims(levels: Levels, bounds: Bounds, step: float) -> dict[str, float]:
    """The loudness each velocity of the note should have; empty while none is recorded."""
    ref = reference(levels, bounds)
    if ref is None:
        return {}
    _, lo, hi = next(b for b in bounds if b[0] == ref)
    level = median(levels[ref])
    return {lane: level + step * (middle(a, b) - middle(lo, hi)) for lane, a, b in bounds}


@dataclass
class Aims:
    step: dict[str, float] = field(default_factory=dict)  # per pass: dB per velocity
    measured: dict[str, bool] = field(default_factory=dict)
    notes: dict[tuple[str, str], dict[str, float]] = field(default_factory=dict)  # (pass, note) -> aims
    references: dict[tuple[str, str], str] = field(default_factory=dict)  # (pass, note) -> loudest recorded

    def of(self, pass_id: str, note: str) -> tuple[dict[str, float], str | None]:
        return self.notes.get((pass_id, note), {}), self.references.get((pass_id, note))


def note_levels(inst: Instrument, manifest: Manifest) -> dict[tuple[str, str], Levels]:
    """Per pass and note: the loudness of each velocity's kept hits."""
    sustained = inst.meta.type in SUSTAINED_TYPES
    imported = inst.recording.source == "samples"
    out: dict[tuple[str, str], Levels] = {}
    for t in manifest.takes:
        if t.kind != "note" or not t.note:
            continue
        lanes = note_lanes(inst, t.note)
        levels = out.setdefault((t.pass_id, t.note), {})
        for h in t.hits:
            lane = h.layer if imported else lane_of(h, t, lanes)
            if not h.rejected and lane in lanes:
                levels.setdefault(lane, []).append(loudness(h, sustained))
    return out


def aims(inst: Instrument, manifest: Manifest | None) -> Aims:
    out = Aims()
    if manifest is None:
        return out
    by_note = note_levels(inst, manifest)
    for pass_id in dict.fromkeys(p for p, _ in by_note):
        notes = {note: (levels, lane_bounds(inst, note)) for (p, note), levels in by_note.items() if p == pass_id}
        step, measured = db_per_velocity(list(notes.values()))
        out.step[pass_id], out.measured[pass_id] = step, measured
        for note, (levels, bounds) in notes.items():
            out.notes[(pass_id, note)] = note_aims(levels, bounds, step)
            if ref := reference(levels, bounds):
                out.references[(pass_id, note)] = ref
    return out


def advice(found: Aims, pass_id: str, note: str, lane: str, follows: bool = True) -> str | None:
    """How loud to play `lane` of `note`, in words; None for its loudest recorded velocity, or before
    any is recorded. `follows`: loudness follows velocity (see vinst.velocity)."""
    targets, ref = found.of(pass_id, note)
    if lane == ref or lane not in targets or ref is None:
        return None
    aim, apart = targets[lane], targets[lane] - targets[ref]
    step = found.step.get(pass_id, DEFAULT_DB_PER_VELOCITY)
    how = (f"as on this instrument's other notes ({step:.2f} dB per velocity step)" if found.measured.get(pass_id)
           else f"a typical distance ({step:.2f} dB per velocity step) until {MIN_NOTES} notes have two "
                "velocities recorded, then the instrument's own")
    why = ("The instrument sets the loudness by velocity itself; this keeps each velocity sounding alike "
           "across the notes." if follows else
           "That keeps the step from one velocity to the next alike across the notes.")
    return (f"Play {lane} so its attacks come to about {aim:.0f} dB, {abs(apart):.0f} dB "
            f"{'under' if apart < 0 else 'over'} {ref}: {how}. {why}")
