"""Results of slicing and analysing the takes: instruments/<slug>/hits.json."""

from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, field_validator

from vinst.plan import MAIN_PASS, take_key
from vinst.spec import Instrument

MANIFEST_FILE = "hits.json"

# Flag -> (severity, explanation). "error" hits shouldn't go into an instrument unless reviewed.
FLAGS: dict[str, tuple[str, str]] = {
    "wrong_note": ("error", "pitch is more than 50 cents away from the expected note"),
    "clipped": ("error", "the signal reached full scale"),
    "no_pitch": ("warning", "no stable pitch found"),
    "layer_mismatch": ("warning", "loudness fits a different velocity of this note better"),
    "off_aim": ("warning", "played harder or softer than this velocity on the other notes, next to this note's "
                           "loudest velocity (see the row's aim), so it may sound brighter or duller. With "
                           "loudness following velocity its loudness is evened out in the instrument, its "
                           "sound stays"),
    "uneven": ("info", "more than 3 dB louder or softer than this velocity's other hits on the note; even out "
                       "loudness brings it closer"),
    "cut_off": ("warning", "still ringing when the next hit (or the file) started"),
    "out_of_tune": ("info", "more than 20 cents off; the instrument will correct it"),
    "loop_poor": ("warning", "the automatic loop may click or pulse; listen to it"),
    "no_loop": ("warning", "no sustained part long enough to loop"),
    "name_mismatch": ("warning", "the file name says a different note than the measured pitch; the pitch was used"),
    "incomplete": ("error", "not every signal has a file for this sample, so it is left out"),
    "loop_in_fade": ("warning", "the loop marker ends in the sample's final fade-out, so the loop pulses; "
                                "move the marker earlier"),
}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


class Hit(BaseModel):
    id: str
    index: int  # among the hits found in its recording
    layer_set: str | None = None  # velocity lane it was moved to in the worksheet; wins over its take's lane
    layer: str | None = None  # velocity lane it plays in
    rr: int | None = None
    start: int
    end: int
    files: dict[str, str]  # signal id -> file name in the samples folder
    midi_detected: float | None = None
    cents: float | None = None
    rms_db: float  # first 0.3 s: the attack, which is what counts for plucked and struck notes
    body_db: float | None = None  # held part, without attack and fade-out: what counts for sustained notes
    peak_db: dict[str, float]
    flags: list[str] = []
    rejected: bool = False
    loop_start: int | None = None  # frames, relative to the sample file
    loop_end: int | None = None  # inclusive
    loop_score: float | None = None
    loop_source: str | None = None  # "marker" | "auto"
    loop_off: bool = False  # the user turned this hit's loop off in the review
    sample_rate: int | None = None  # imported files can differ; sliced hits use their take's rate

    def severity(self) -> str | None:
        levels = [FLAGS[f][0] for f in self.flags if f in FLAGS]
        return min(levels, key=SEVERITY_ORDER.__getitem__) if levels else None


class TakeResult(BaseModel):
    number: int
    base: str
    pass_id: str = MAIN_PASS
    midi: int | None = None  # the note's key; None for extras and the noise floor
    signals: list[str] = []  # signal ids, as in each hit's files
    note: str = ""  # a note recording's note, e.g. "060_C-0"; its lanes' recordings share it
    lane: str | None = None  # the velocity lane its hits start in
    origin: str = "plan"  # "plan" | "more" (variations recorded later) | "combined" (lanes in one take)
    title: str
    kind: str
    status: Literal["ok", "missing", "count_mismatch", "error", "noise_floor"]

    @field_validator("status", "kind", mode="before")
    @classmethod
    def _renamed(cls, v: str) -> str:
        return "noise_floor" if v == "room_tone" else v
    message: str = ""  # what needs attention
    info: str = ""  # good to know, e.g. "3 hits added"; not a problem
    expected_hits: int
    source: str = ""  # name, size and mtime of the take files; rejections only carry over if unchanged
    sample_rate: int | None = None
    length: int | None = None
    envelope: list[float] = []  # normalised peak per bucket, for the waveform thumbnail
    level_db: float | None = None  # a noise floor's RMS level; each pass has its own
    cut_by_hand: bool = False  # its hits are where they were set by hand, not where they were found
    cleanup_eq: str = ""  # cleanup_eq.take_setup() its samples were written with

    @property
    def files(self) -> list[str]:
        return [f"{self.base}_{s}.wav" for s in self.signals]
    hits: list[Hit] = []

    @property
    def key(self) -> str:
        return take_key(self.base, self.pass_id)


def settle(take: TakeResult, lanes: list[str]) -> None:
    """A note recording's hits play in its lane, or in the lane they were moved to.

    Hits whose lane the note no longer has get none and are left out of the build. Not for
    imported samples: the importer sets their lanes.
    """
    if take.kind != "note" or take.status not in ("ok", "count_mismatch"):
        return
    take.status = "ok"  # a lane take holds variations of one dynamic: any number of hits is fine
    for h in take.hits:
        h.layer = lane_of(h, take, lanes)


def lane_of(hit: Hit, take: "TakeResult", lanes: list[str]) -> str | None:
    """The lane a recorded hit plays in, given its note's current lanes; None if that lane is gone.

    Worked out where it's used, so a change to a note's lanes shows at once, before slicing again.
    """
    return hit.layer_set if hit.layer_set in lanes else take.lane if take.lane in lanes else None


def loudness(hit: Hit, sustained: bool) -> float:
    """What makes notes sound equally loud: the held part for sustained sounds, the attack otherwise."""
    return hit.body_db if sustained and hit.body_db is not None else hit.rms_db


LANE_FLAGS = ("layer_mismatch", "off_aim", "uneven")
AIM_TOLERANCE_DB = 3.0  # how far a hit may be from its aim, or from its lane's others on the note


def check_lanes(results: list["TakeResult"], lanes: list[str], aims: dict[str, float] | None = None,
                reference: str | None = None, sustained: bool = False, evened_out: bool = False) -> None:
    """Loudness across one note's lanes: hits that fit another lane better, lanes out of order; with
    `aims` (see vinst.aims), lanes and hits off their aim next to `reference`, and hits off their
    lane's others. `evened_out`: the instrument sets the loudness by velocity (see vinst.velocity)."""
    hits = [h for t in results for h in t.hits if not h.rejected and h.layer in lanes]
    for t in results:
        for h in t.hits:
            h.flags = [f for f in h.flags if f not in LANE_FLAGS]
        t.message = "; ".join(m for m in t.message.split("; ") if m and not m.endswith(LANES_OUT_OF_ORDER)
                              and ON_OTHER_NOTES not in m
                              and m != "this note's velocities don't get louder in order")  # in older manifests
    level = {h.id: loudness(h, sustained) for h in hits}
    medians = {lane: float(np.median([level[h.id] for h in hits if h.layer == lane]))
               for lane in lanes if any(h.layer == lane for h in hits)}
    messages = []
    if len(medians) >= 2:
        for h in hits:
            if min(medians, key=lambda lane: abs(medians[lane] - level[h.id])) != h.layer:
                h.flags.append("layer_mismatch")
        ordered = [lane for lane in lanes if lane in medians]
        softer = next(((a, b) for a, b in zip(ordered, ordered[1:]) if medians[b] <= medians[a]), None)
        if softer:
            a, b = softer
            messages.append(f"{b} ({medians[b]:.0f} dBFS) is no louder than {a} ({medians[a]:.0f} dBFS), so "
                            f"{LANES_OUT_OF_ORDER}")
    for h in hits:
        if "layer_mismatch" in h.flags:
            continue
        aim = (aims or {}).get(h.layer) if h.layer != reference else None
        if aim is not None and abs(level[h.id] - aim) > AIM_TOLERANCE_DB:
            h.flags.append("off_aim")
        elif sum(o.layer == h.layer for o in hits) > 1 and abs(level[h.id] - medians[h.layer]) > AIM_TOLERANCE_DB:
            h.flags.append("uneven")
    for lane, m in medians.items():
        aim = (aims or {}).get(lane)
        if lane != reference and aim is not None and abs(m - aim) > AIM_TOLERANCE_DB:
            messages.append(off_aim_message(lane, reference, m, aim, (aims or {}).get(reference, aim),
                                            evened_out))
    if messages:
        first = next((t for t in results if t.hits), None)
        if first is not None:
            first.message = "; ".join(filter(None, [first.message, *messages]))


LANES_OUT_OF_ORDER = "playing harder wouldn't sound louder"
ON_OTHER_NOTES = "than on the other notes"  # in every off-aim message, so checking again replaces it


def off_aim_message(lane: str, reference: str | None, level: float, aim: float, reference_level: float,
                    evened_out: bool) -> str:
    """What a velocity played off its aim means, in words: its sound, and its loudness."""
    harder = level > aim
    text = (f"{lane} was played {'harder' if harder else 'softer'} {ON_OTHER_NOTES}, next to {reference} "
            f"({abs(level - aim):.0f} dB {'over' if harder else 'under'} its aim of {aim:.0f} dB), so it may "
            f"sound {'brighter' if harder else 'duller'}. ")
    if evened_out:
        return text + "Its loudness is evened out in the instrument, so record it again only if it sounds out of place"
    closer = harder == (aim < reference_level)
    return text + f"The step to {reference} is also {'smaller' if closer else 'larger'} {ON_OTHER_NOTES}"


class Manifest(BaseModel):
    samples_dir: str
    setup: str = ""  # plan.slicing_setup() at slicing time
    finder: int = 0  # slicing.HIT_FINDER of the slicing that found all its hits; 0: before it was kept
    cleanup: str = ""  # slicing.cleanup_setup() of the slicing that wrote all its samples
    source: Literal["takes", "samples"] = "takes"  # sliced recorded takes, or imported samples
    noise_floor_db: float | None = None
    denoised: bool = False
    takes: list[TakeResult] = []

    def counts(self) -> dict[str, int]:
        hits = [h for t in self.takes for h in t.hits]
        return {
            "takes_ok": sum(t.status == "ok" for t in self.takes),
            "takes": sum(t.kind != "noise_floor" for t in self.takes),
            "takes_missing": sum(t.status == "missing" for t in self.takes),
            "takes_problem": sum(t.status in ("count_mismatch", "error") for t in self.takes),
            "hits": len(hits),
            "errors": sum(h.severity() == "error" and not h.rejected for h in hits),
            "warnings": sum(h.severity() == "warning" and not h.rejected for h in hits),
            "rejected": sum(h.rejected for h in hits),
        }


def samples_dir(inst: Instrument) -> Path | None:
    rec = inst.recording
    if rec.samples_dir:
        return Path(rec.samples_dir).expanduser()
    if rec.source == "samples":
        # Copies with uniform names; the imported folder itself is never written to.
        from vinst.store import instrument_dir

        return instrument_dir(inst.meta.slug) / "imported-samples"
    if rec.takes_dir:
        return Path(rec.takes_dir).expanduser() / "sliced"
    return None


def load(path: Path) -> Manifest | None:
    return Manifest.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def save(manifest: Manifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=1), encoding="utf-8")
