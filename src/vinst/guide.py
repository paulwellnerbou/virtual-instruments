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
    detail: str = ""  # e.g. the dynamic, or what comes next
    what: str = ""  # the take, e.g. "C3 (60) · C string, open"
    beats: int = 0  # beats in this step, for the metronome; 0 for silence


@dataclass
class Guide:
    beat_s: float
    steps: list[Step] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.steps[-1].at + self.steps[-1].length if self.steps else 0.0

    def add(self, length: float, kind: str, title: str, detail: str = "", what: str = "", beats: int = 0) -> None:
        self.steps.append(Step(self.total, length, kind, title, detail, what, beats))

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


def hit_details(inst: Instrument, take: Take) -> list[str]:
    """Per hit, how to play it."""
    if take.kind != "note":
        extra = next((x for x in inst.extras if f"x_{file_token(x.id)}" == take.base_name), None)
        return [extra.instructions if extra else ""] * take.hits
    name = lane_name(take.lane or "")
    return [name + (f" · variation {r + 1} of {take.hits}" if take.hits > 1 else "") for r in range(take.hits)]


def bar_beats(inst: Instrument, take: Take) -> int:
    seconds = inst.recording.ring_seconds if take.kind == "note" else EXTRA_HIT_S
    return math.ceil((seconds + HIT_GAP_S) / BEAT_S)


def count_in(guide: Guide, beats: int, what: str) -> None:
    beats = min(beats, COUNT_IN_BEATS)
    guide.add(beats * BEAT_S, "count", "Get ready", what=what, beats=beats)


def add_take(guide: Guide, inst: Instrument, take: Take, beats: int, silence_s: float) -> None:
    if take.kind == "noise_floor":
        guide.add(silence_s, "silence", "Stay silent", "Noise floor: everything connected, nothing touched",
                  take.title)
        return
    details = hit_details(inst, take)
    for i in range(take.hits):
        guide.add(beats * BEAT_S, "hit", f"Hit {i + 1} of {take.hits}", details[i], take.title, beats)


def take_guide(inst: Instrument, take: Take) -> Guide:
    """One take on its own."""
    guide = Guide(BEAT_S)
    beats = bar_beats(inst, take)
    count_in(guide, beats, take.title)
    add_take(guide, inst, take, beats, inst.recording.noise_floor_seconds)
    return guide


def more_guide(inst: Instrument, take: Take, count: int) -> Guide:
    """More variations of a lane take, to compare with its own and keep the best."""
    guide = Guide(BEAT_S)
    beats = bar_beats(inst, take)
    count_in(guide, beats, take.title)
    name = lane_name(take.lane or "")
    for i in range(count):
        guide.add(beats * BEAT_S, "hit", f"New hit {i + 1} of {count}", name, take.title, beats)
    return guide


def take_by_number(inst: Instrument, number: int) -> Take | None:
    return next((t for t in plan_takes(inst) if t.number == number), None)
