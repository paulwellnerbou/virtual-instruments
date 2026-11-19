"""Results of slicing and analysing the takes: instruments/<slug>/hits.json."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, field_validator

from vinst.plan import MAIN_PASS, take_key
from vinst.spec import Instrument

MANIFEST_FILE = "hits.json"

# Flag -> (severity, explanation). "error" hits shouldn't go into an instrument unless reviewed.
FLAGS: dict[str, tuple[str, str]] = {
    "wrong_note": ("error", "pitch is more than 50 cents away from the expected note"),
    "clipped": ("error", "the signal reached full scale"),
    "no_pitch": ("warning", "no stable pitch found"),
    "layer_mismatch": ("warning", "loudness fits a different velocity layer"),
    "cut_off": ("warning", "still ringing when the next hit (or the file) started"),
    "out_of_tune": ("info", "more than 20 cents off; the instrument will correct it"),
    "loop_poor": ("warning", "the automatic loop may click or pulse; listen to it"),
    "no_loop": ("warning", "no sustained part long enough to loop"),
    "loop_in_fade": ("warning", "the loop marker ends in the sample's final fade-out, so the loop pulses; "
                                "move the marker earlier"),
}
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


class Hit(BaseModel):
    id: str
    index: int
    layer: str | None = None
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

    def severity(self) -> str | None:
        levels = [FLAGS[f][0] for f in self.flags if f in FLAGS]
        return min(levels, key=SEVERITY_ORDER.__getitem__) if levels else None


class TakeResult(BaseModel):
    number: int
    base: str
    pass_id: str = MAIN_PASS
    title: str
    kind: str
    status: Literal["ok", "missing", "count_mismatch", "error", "noise_floor"]

    @field_validator("status", "kind", mode="before")
    @classmethod
    def _renamed(cls, v: str) -> str:
        return "noise_floor" if v == "room_tone" else v
    message: str = ""
    expected_hits: int
    source: str = ""  # name, size and mtime of the take files; rejections only carry over if unchanged
    sample_rate: int | None = None
    length: int | None = None
    envelope: list[float] = []  # normalised peak per bucket, for the waveform thumbnail
    hits: list[Hit] = []

    @property
    def key(self) -> str:
        return take_key(self.base, self.pass_id)


class Manifest(BaseModel):
    samples_dir: str
    setup: str = ""  # plan.slicing_setup() at slicing time
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
    if rec.takes_dir:
        return Path(rec.takes_dir).expanduser() / "sliced"
    return None


def load(path: Path) -> Manifest | None:
    return Manifest.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None


def save(manifest: Manifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest.model_dump_json(indent=1), encoding="utf-8")
