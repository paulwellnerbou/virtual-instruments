"""What to play when while recording in the app: a count-in, then one hit on beat 1 of each bar.

A bar is as long as a hit rings, plus a short gap, at one beat per second.
"""

import math
from dataclasses import asdict, dataclass, field

from vinst.plan import EXTRA_HIT_S, HIT_GAP_S, Take, plan_takes
from vinst.spec import Instrument, file_token

BEAT_S = 1.0  # 60 BPM keeps the counting readable
COUNT_IN_BEATS = 4
PRE_ROLL_S = 0.5  # of the count-in, recorded before the first beat
DYNAMICS = {"ppp": "very soft", "pp": "very soft", "p": "soft", "mp": "medium soft", "mf": "medium",
            "f": "loud", "ff": "very loud", "fff": "very loud"}


@dataclass
class Step:
    at: float  # seconds from the start of the recording
    length: float
    kind: str  # "count" | "hit" | "rest" | "silence"
    title: str  # what to do, large
    detail: str = ""  # e.g. the dynamic
    beats: int = 0  # beats in this step, for the metronome; 0 for silence


@dataclass
class Guide:
    beat_s: float
    steps: list[Step] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.steps[-1].at + self.steps[-1].length if self.steps else 0.0

    def add(self, length: float, kind: str, title: str, detail: str = "", beats: int = 0) -> None:
        self.steps.append(Step(self.total, length, kind, title, detail, beats))

    @property
    def skip(self) -> float:
        """What isn't recorded at the start: the count-in, but for a short pre-roll."""
        first = self.steps[0] if self.steps else None
        return max(0.0, first.length - PRE_ROLL_S) if first and first.kind == "count" else 0.0

    def hits_at(self) -> list[float]:
        """Where each hit is asked for, in seconds from the start of the recorded file."""
        return [s.at - self.skip for s in self.steps if s.kind == "hit"]

    def to_dict(self) -> dict:
        return {"beat": self.beat_s, "total": self.total, "steps": [asdict(s) for s in self.steps]}


def lane_name(lane: str) -> str:
    return f"{lane} ({DYNAMICS[lane]})" if lane in DYNAMICS else lane


def how_to_play(inst: Instrument, take: Take) -> str:
    """For every hit of the take: its dynamic, or an extra's instructions."""
    if take.kind != "note":
        extra = next((x for x in inst.extras if f"x_{file_token(x.id)}" == take.base_name), None)
        return extra.instructions if extra else ""
    return lane_name(take.lane or "")


def bar_beats(inst: Instrument, take: Take) -> int:
    seconds = inst.recording.ring_seconds if take.kind == "note" else EXTRA_HIT_S
    return math.ceil((seconds + HIT_GAP_S) / BEAT_S)


def count_in(guide: Guide, beats: int, first: str) -> None:
    """`first` is what the first hit asks for: the count-in is about that hit, nothing later."""
    beats = min(beats, COUNT_IN_BEATS)
    guide.add(beats * BEAT_S, "count", "Get ready", first, beats=beats)


NOISE_FLOOR_HOW = "Everything connected, nothing touched"


def hits(guide: Guide, inst: Instrument, take: Take, count: int, title: str) -> None:
    beats = bar_beats(inst, take)
    how = how_to_play(inst, take)
    count_in(guide, beats, f"{title.format(1, count)} · {how}" if how else title.format(1, count))
    for i in range(count):
        guide.add(beats * BEAT_S, "hit", title.format(i + 1, count), how, beats)


def take_guide(inst: Instrument, take: Take, count: int | None = None) -> Guide:
    """One take on its own; `count` hits instead of the planned ones."""
    guide = Guide(BEAT_S)
    if take.kind == "noise_floor":
        count_in(guide, bar_beats(inst, take), "Then stay silent")
        guide.add(inst.recording.noise_floor_seconds, "silence", "Stay silent", NOISE_FLOOR_HOW)
    else:
        hits(guide, inst, take, count or take.hits, "Hit {} of {}")
    return guide


def more_guide(inst: Instrument, take: Take, count: int) -> Guide:
    """More variations of a lane take, to compare with its own and keep the best."""
    guide = Guide(BEAT_S)
    hits(guide, inst, take, count, "New hit {} of {}")
    return guide


def take_by_number(inst: Instrument, number: int) -> Take | None:
    return next((t for t in plan_takes(inst) if t.number == number), None)
