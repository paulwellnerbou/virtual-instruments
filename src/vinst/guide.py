"""What to play while recording in the app.

Hits are played at the player's own pace, as many as they like, each left to ring out, and the
player stops when done: slicing finds them however they're spaced. The planned number is only a
suggestion. Only the noise floor is timed: a count-in, then silence.
"""

from dataclasses import asdict, dataclass, field

from vinst import notes as note_names
from vinst.plan import Take, plan_takes, planned_notes
from vinst.spec import Instrument, InstrumentType, file_token

BEAT_S = 1.0  # of the noise floor's count-in
COUNT_IN_BEATS = 4
PRE_ROLL_S = 0.5  # of the count-in, recorded before the silence
DYNAMICS = {"ppp": "very soft", "pp": "very soft", "p": "soft", "mp": "medium soft", "mf": "medium",
            "f": "loud", "ff": "very loud", "fff": "very loud"}
DONE = "stop when you're done."


@dataclass
class Step:
    at: float  # seconds from the start of the recording
    length: float  # 0: until the player stops
    kind: str  # "count" | "silence" | "play"
    title: str  # what to do, large
    detail: str = ""  # e.g. the dynamic
    beats: int = 0  # beats in this step, for the count-in; 0 otherwise


@dataclass
class Guide:
    beat_s: float
    steps: list[Step] = field(default_factory=list)

    @property
    def total(self) -> float:
        """How long the take runs; 0 for one the player stops."""
        return self.steps[-1].at + self.steps[-1].length if self.steps else 0.0

    @property
    def free(self) -> bool:
        return bool(self.steps) and self.steps[-1].kind == "play"

    def add(self, length: float, kind: str, title: str, detail: str = "", beats: int = 0) -> None:
        self.steps.append(Step(self.total, length, kind, title, detail, beats))

    @property
    def skip(self) -> float:
        """What isn't recorded at the start: the count-in, but for a short pre-roll."""
        first = self.steps[0] if self.steps else None
        return max(0.0, first.length - PRE_ROLL_S) if first and first.kind == "count" else 0.0

    def to_dict(self) -> dict:
        return {"beat": self.beat_s, "total": self.total, "free": self.free,
                "steps": [asdict(s) for s in self.steps]}


def lane_name(lane: str) -> str:
    return f"{lane} ({DYNAMICS[lane]})" if lane in DYNAMICS else lane


def how_to_play(inst: Instrument, take: Take) -> str:
    """For every hit of the take: its dynamic, or an extra's instructions."""
    if take.kind != "note":
        extra = next((x for x in inst.extras if f"x_{file_token(x.id)}" == take.base_name), None)
        return extra.instructions if extra else ""
    return lane_name(take.lane or "")


NOISE_FLOOR_HOW = "Everything connected, nothing touched"


def times(count: int) -> str:
    return "once" if count == 1 else f"{count} times"


def between(inst: Instrument) -> str:
    """What to do before playing the next note, and then: stop."""
    held = inst.meta.type == InstrumentType.SUSTAINED
    return f"{'Hold each as long as a note may last' if held else 'Let each ring out before the next'}, and {DONE}"


def hits(inst: Instrument, take: Take, count: int) -> Guide:
    guide = Guide(BEAT_S)
    how = how_to_play(inst, take)
    about = f"As often as you like, about {times(count)}"
    if take.kind == "note":  # an extra's sound, a knock or a chuck, doesn't ring
        guide.add(0, "play", f"Play it {how}", f"{about}. {between(inst)}")
    else:
        guide.add(0, "play", how or "Play it", f"{about}, and {DONE}")
    return guide


def take_guide(inst: Instrument, take: Take) -> Guide:
    """One take on its own."""
    if take.kind != "noise_floor":
        return hits(inst, take, take.hits)
    guide = Guide(BEAT_S)
    guide.add(COUNT_IN_BEATS * BEAT_S, "count", "Get ready", "Then stay silent", beats=COUNT_IN_BEATS)
    guide.add(inst.recording.noise_floor_seconds, "silence", "Stay silent", NOISE_FLOOR_HOW)
    return guide


def more_guide(inst: Instrument, take: Take, count: int) -> Guide:
    """More variations of a lane take, to compare with its own and keep the best."""
    return hits(inst, take, count)


def scale_guide(inst: Instrument, pass_id: str, per_note: int) -> Guide:
    """The whole scale in one recording, `per_note` hits on each note, at one velocity throughout:
    split into the notes' takes by pitch afterwards."""
    guide = Guide(BEAT_S)
    midis = sorted(planned_notes(inst, pass_id))
    if not midis:
        return guide
    every = midis == list(range(midis[0], midis[-1] + 1))
    which = f"{note_names.name(midis[0])} to {note_names.name(midis[-1])}" if every \
        else ", ".join(note_names.name(m) for m in midis)
    guide.add(0, "play", f"{which}, low to high",
              f"Each note about {times(per_note)}, at one velocity throughout. {between(inst)}")
    return guide


def take_by_number(inst: Instrument, number: int) -> Take | None:
    return next((t for t in plan_takes(inst) if t.number == number), None)
