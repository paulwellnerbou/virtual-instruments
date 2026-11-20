"""The instrument spec: everything the wizard asks, stored as instruments/<slug>/instrument.toml."""

import re
import tomllib
import unicodedata
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import AliasChoices, BaseModel, Field, model_validator


class InstrumentType(StrEnum):
    PLUCKED = "plucked"
    PITCHED_PERCUSSION = "pitched_percussion"
    SUSTAINED = "sustained"
    UNPITCHED = "unpitched"
    SINGLE_SAMPLE = "single_sample"


TYPE_LABELS = {
    InstrumentType.PLUCKED: "Plucked / struck string",
    InstrumentType.PITCHED_PERCUSSION: "Pitched percussion",
    InstrumentType.SUSTAINED: "Sustained (bowed, wind, pads)",
    InstrumentType.UNPITCHED: "Unpitched percussion",
    InstrumentType.SINGLE_SAMPLE: "Single sample",
}


# Signal ids and layer names end up in file names, hit ids ("<take>_<layer>_rr1") and URLs.
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")


# Notes that are held rather than decaying: they loop, and their loudness is their held level.
SUSTAINED_TYPES = {InstrumentType.SUSTAINED, InstrumentType.SINGLE_SAMPLE}


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    ascii_text = re.sub(r"['’]", "", ascii_text.lower())
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-") or "instrument"


def file_token(text: str) -> str:
    """Filename-safe token that keeps case, e.g. 'low G' -> 'lowG'."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9#]+", "", ascii_text).replace("#", "s") or "x"


class Meta(BaseModel):
    name: str
    slug: str
    type: InstrumentType
    author: str = ""
    version: str = "0.1.0"
    description: str = ""
    website: str = ""
    copyright: str = ""


class Signal(BaseModel):
    """One recorded source, e.g. piezo, internal mic, external mic.

    "together": recorded at the same time as the other "together" signals (one performance,
    phase-aligned, can be mixed). "separate": recorded in its own pass, e.g. because it shares
    one output with another source; the instrument switches between passes instead of mixing.
    """

    id: str
    name: str
    recorded: Literal["together", "separate"] = "together"


class InstrumentString(BaseModel):
    name: str
    open: int = Field(ge=0, le=127)
    frets: int = Field(ge=0, le=36)


class Sound(BaseModel):
    """An unpitched sound mapped to one key."""

    name: str
    key: int = Field(ge=0, le=127)


class Range(BaseModel):
    mode: Literal["strings", "notes", "sounds"]
    strings: list[InstrumentString] = []
    low: int = Field(default=60, ge=0, le=127)
    high: int = Field(default=72, ge=0, le=127)
    density: int = Field(default=1, ge=1, le=12, description="sample every n-th semitone")
    sounds: list[Sound] = []

    def span(self) -> tuple[int, int] | None:
        """Lowest and highest playable key, or None while the range is incomplete."""
        if self.mode == "strings":
            if not self.strings:
                return None
            return min(s.open for s in self.strings), max(s.open + s.frets for s in self.strings)
        if self.mode == "notes":
            return (self.low, self.high) if self.low <= self.high else None
        if not self.sounds:
            return None
        keys = [s.key for s in self.sounds]
        return min(keys), max(keys)


# Group tags the generated preset uses itself; a signal id must not shadow them.
NOTES_TAG, FX_TAG, RELEASE_TAG = "notes", "fx", "release-noise"
LOW_TAG = "low-cut-part"  # what the low cut took out, played along as much as the Low cut knob gives back
RESERVED_SIGNAL_IDS = {NOTES_TAG, FX_TAG, RELEASE_TAG, LOW_TAG}


class Dynamics(BaseModel):
    """The default velocity lanes of every note, and how many variations to record per lane."""

    layers: list[str] = ["p", "mf", "f"]  # lane names, quietest first
    round_robins: int = Field(default=4, ge=1, le=32)


class NoteLanes(BaseModel):
    """One note's own velocity lanes, used instead of the defaults."""

    layers: list[str]
    # The lowest velocity of each lane after the first; empty or not fitting: an even split.
    splits: list[int] = []


class Extra(BaseModel):
    """Additional non-note recordings, e.g. release noises, chucks, body taps."""

    id: str
    name: str
    hits: int = Field(default=10, ge=1)
    instructions: str = ""
    # How the instrument uses it: played on note release, on its own key, or not at all.
    use: Literal["release", "key", "off"] = "key"
    key: int = Field(default=48, ge=0, le=127)
    # On its own key: whether playing it stops the notes still ringing, as a chuck mutes the strings.
    mutes: bool = False
    # On its own key: hits recorded alternating down and up (1st, 3rd, … down) also each on a key of their own.
    stroke_keys: bool = False
    down_key: int = Field(default=49, ge=0, le=127)
    up_key: int = Field(default=51, ge=0, le=127)

    @model_validator(mode="before")
    @classmethod
    def _chuck_mutes(cls, data: Any) -> Any:
        # Instruments from before the setting: their chuck mutes, as one does on a real instrument.
        if isinstance(data, dict) and "mutes" not in data and data.get("id") == "chuck":
            return {**data, "mutes": True}
        return data


AUDIO_SUFFIXES = (".wav", ".aif", ".aiff", ".flac")


class Recording(BaseModel):
    # "takes": record new takes in the worksheet; "samples": import already-cut samples.
    source: Literal["takes", "samples"] = "takes"
    takes_dir: str = ""
    import_dir: str = ""
    # How to read note names like "C3" in imported file names when the pitch can't be measured:
    # "c3" = middle C is C3 (Decent Sampler, Cubase), "c4" = middle C is C4 (scientific, Logic).
    import_note_names: Literal["c3", "c4"] = "c3"
    samples_dir: str = Field(default="", description="sliced samples; empty means <takes_dir>/sliced")
    sample_rate: int = 48000
    bit_depth: int = 24
    # Recording in the app: signal id -> input number (1-based) on the audio interface.
    input_channels: dict[str, int] = {}
    ring_seconds: float = Field(default=6.0, gt=0, description="how long each hit rings or is held")
    # Silence with everything connected: the slicer's reference level and, if denoising is on,
    # the noise profile.
    noise_floor_seconds: int = Field(
        default=30, ge=0, validation_alias=AliasChoices("noise_floor_seconds", "room_tone_seconds"))


EqKind = Literal["peak", "notch", "low_shelf", "high_shelf", "highpass", "lowpass", "hum"]


class EqFilter(BaseModel):
    """One band of a clean-up EQ (see vinst.cleanup_eq)."""

    kind: EqKind = "peak"
    hz: float = Field(default=1000.0, ge=10, le=40000)
    gain_db: float = Field(default=-12.0, ge=-60, le=24, description="peak, shelves and hum")
    q: float = Field(default=1.0, ge=0.1, le=100)
    harmonics: int = Field(default=6, ge=1, le=40, description="hum: also at 2×, 3×, … the frequency")


class CleanupEq(BaseModel):
    """Filters that take a noise out of the sliced samples of chosen takes, e.g. mains hum on a pickup."""

    name: str
    signals: list[str] = []  # empty: all signals
    every_take: bool = False
    takes: list[str] = []  # take keys, when not every take
    filters: list[EqFilter] = []


class Processing(BaseModel):
    """Optional clean-up applied to the sliced samples (the takes themselves are never changed)."""

    denoise: bool = False  # off by default: takes are often already cleaned in WaveLab / Cubase
    denoise_reduction_db: float = Field(default=12.0, ge=1, le=40, description="maximum noise reduction")
    # Sustain loops: from loop markers in the takes (e.g. set in WaveLab), else found automatically.
    loops: bool = False
    loop_crossfade_ms: float = Field(default=30.0, ge=0, le=500)
    # Lowers what lies below each note's pitch, e.g. a body's low resonance (see vinst.lowcut); applied when
    # building, to the bundle's copies of the samples, so changing it needs no slicing.
    low_cut: bool = False
    low_cut_below: float = Field(default=0.75, ge=0.3, le=1.0, description="share of the note's pitch")
    low_cut_db: float = Field(default=12.0, ge=1, le=40)
    # Applied when slicing, to the takes each is for (see vinst.cleanup_eq).
    cleanup_eqs: list[CleanupEq] = []


CONTROLS = {
    "mix": "Signal mix",
    "tone": "Tone",
    "eq": "Equalizer",
    "lowcut": "Low cut",
    "attack": "Attack",
    "release": "Release",
    "reverb": "Reverb",
    "chorus": "Chorus",
    "noise": "Release noise",
}


class EqBand(BaseModel):
    """One band of the instrument's equalizer (see vinst.eq)."""

    type: Literal["low_shelf", "peak", "high_shelf", "highpass", "lowpass", "notch"] = "peak"
    freq: float = Field(default=1000.0, ge=20, le=20000)
    q: float = Field(default=0.7, ge=0.1, le=18)
    gain_db: float = Field(default=0.0, ge=-24, le=24)
    name: str = ""  # the knob's label; empty: its frequency
    on: bool = True


def default_eq() -> list[EqBand]:
    return [EqBand(type="low_shelf", freq=150, name="Low"), EqBand(type="peak", freq=1000, name="Mid"),
            EqBand(type="high_shelf", freq=5000, name="High")]


class Preset(BaseModel):
    """How the DecentSampler instrument plays and looks (wizard step 7)."""

    tuning: Literal["off", "partial", "full"] = "full"
    tuning_amount: float = Field(default=0.5, ge=0, le=1, description="share corrected when partial")
    extend_below: int = Field(default=0, ge=0, le=24, description="stretch the lowest sample down")
    extend_above: int = Field(default=0, ge=0, le=24, description="stretch the highest sample up")
    # Also use hits of recorded notes up to this many semitones below / above each key, pitched to fit.
    neighbours_below: int = Field(default=0, ge=0, le=2)
    neighbours_above: int = Field(default=0, ge=0, le=2)

    @model_validator(mode="before")
    @classmethod
    def _neighbours_both_ways(cls, data: Any) -> Any:
        # Instruments from before the split had one setting for both directions.
        if isinstance(data, dict) and "neighbour_round_robins" in data:
            n = data["neighbour_round_robins"]
            data = {k: v for k, v in data.items() if k != "neighbour_round_robins"}
            return {"neighbours_below": n, "neighbours_above": n, **data}
        return data
    # Signals recorded in separate passes are different performances: "switch" plays one pass at
    # a time (a menu in the instrument), "layer" plays them together like two players in unison.
    pass_mode: Literal["switch", "layer"] = "switch"
    attack: float = Field(default=0.0, ge=0, le=10)
    release: float = Field(default=1.0, ge=0, le=20)
    vel_track: float | None = Field(default=None, ge=0, le=1, description="None: 0.3 with layers, else 0.8")
    # The lowest velocity of each layer after the first, e.g. [80] for "f from 80". Empty, or not
    # fitting the layers (e.g. after adding one), means an even split.
    velocity_splits: list[int] = []
    # Keep every sample within this of its velocity's loudness on the notes around it (see
    # preset.balance_corrections).
    balance: bool = False
    balance_tolerance_db: float = Field(default=3.0, ge=0, le=12)
    controls: list[str] = ["mix", "tone", "release", "reverb", "noise"]  # the low cut's: see Processing.low_cut
    tone: float = Field(default=1.0, ge=0, le=1)
    eq: list[EqBand] = Field(default_factory=default_eq, max_length=8)
    reverb: float = Field(default=0.15, ge=0, le=1)
    reverb_room: float = Field(default=0.7, ge=0, le=1)
    # How fast the reverb's tail loses its highs. DecentSampler's own 0.3 lets a pluck's click ring on
    # as a hiss; 0.6 sounds like a furnished room.
    reverb_damping: float = Field(default=0.6, ge=0, le=1)
    # The reverb on buses fed per pitch band, without a pluck's click and bright start (see vinst.reverb_send).
    reverb_send: bool = False
    reverb_ir: str = ""  # with reverb_send: an impulse response file of your own; empty: DecentSampler's reverb
    chorus: float = Field(default=0.0, ge=0, le=1)
    background: str = ""  # image file; empty means a plain colour
    cover_art: str = ""  # square image for DecentSampler's File Browser
    bg_color: str = "FF1F2328"
    text_color: str = "FFFFFFFF"
    accent_color: str = "FFE0A458"  # the knobs' lit arc and the playable keys
    # What the panel shows under the title and at its foot; all of it stays in the metadata either way.
    show_author: bool = True
    show_version: bool = True
    show_copyright: bool = True
    show_website: bool = True


class Instrument(BaseModel):
    meta: Meta
    signals: list[Signal]
    range: Range
    dynamics: Dynamics = Field(default_factory=Dynamics)
    extras: list[Extra] = []
    recording: Recording = Field(default_factory=Recording)
    processing: Processing = Field(default_factory=Processing)
    preset: Preset = Field(default_factory=Preset)
    # Notes with their own velocity lanes, by note (its base name, e.g. "060_C-0").
    note_lanes: dict[str, NoteLanes] = {}

    def problems(self) -> list[str]:
        """Soft validation shown in the UI; an instrument with problems can still be saved."""
        out: list[str] = []
        if not self.signals:
            out.append("Add at least one signal.")
        _dupes(out, "signal id", [s.id for s in self.signals])
        _dupes(out, "extra id", [e.id for e in self.extras])
        if self.range.mode == "strings":
            if not self.range.strings:
                out.append("Add at least one string.")
            _dupes(out, "string name", [file_token(s.name) for s in self.range.strings])
        elif self.range.mode == "notes":
            if self.range.low > self.range.high:
                out.append("Lowest note is above highest note.")
        elif self.range.mode == "sounds":
            if not self.range.sounds:
                out.append("Add at least one sound.")
            _dupes(out, "sound key", [str(s.key) for s in self.range.sounds])
        if not self.dynamics.layers:
            out.append("Add at least one velocity.")
        _dupes(out, "velocity name", self.dynamics.layers)
        for note, own in self.note_lanes.items():
            if not own.layers:
                out.append(f"Note {note} has no velocities; give it one or use the defaults.")
            _dupes(out, f"velocity name for note {note}", own.layers)
        for c in self.preset.controls:
            if c not in CONTROLS:
                out.append(f"Unknown control “{c}”.")
        if {"tone", "eq"} <= set(self.preset.controls):
            out.append("Choose either the Tone knob or the Equalizer, not both.")
        for color in (self.preset.bg_color, self.preset.text_color, self.preset.accent_color):
            if not re.fullmatch(r"[0-9A-Fa-f]{8}", color):
                out.append(f"Colour “{color}” must be 8 hex digits (AARRGGBB).")
        keys = [x.key for x in self.extras if x.use == "key"]
        _dupes(out, "extra key", [str(k) for k in keys])
        if self.recording.source == "samples":
            if not self.recording.import_dir:
                out.append("Choose the folder with the samples to import (step 5).")
            if any(sig.recorded == "separate" for sig in self.signals):
                out.append("Imported samples can't be in separate recording passes; set all signals to "
                           "“Together” (step 2).")
        span = self.range.span()
        if span is not None and self.range.mode != "sounds" and self.recording.source == "takes":
            lo, hi = span[0] - self.preset.extend_below, span[1] + self.preset.extend_above
            for x in self.extras:
                if x.use == "key" and lo <= x.key <= hi:
                    out.append(f"Extra “{x.name}” is on key {x.key}, inside the playable range {lo}–{hi}; "
                               "move it outside (step 6).")
        for sig in self.signals:
            if sig.id in RESERVED_SIGNAL_IDS or sig.id.startswith("pass-"):
                out.append(f"Signal id “{sig.id}” is reserved; choose another.")
        if self.processing.denoise and not self.recording.noise_floor_seconds:
            out.append("Denoising needs a noise-floor recording (set its length in step 4).")
        lanes = self.dynamics.layers + [lane for own in self.note_lanes.values() for lane in own.layers]
        for what, values in (("Signal id", [s.id for s in self.signals]), ("Velocity name", list(dict.fromkeys(lanes)))):
            for v in values:
                if not SAFE_NAME.fullmatch(v):
                    out.append(f"{what} “{v}” may only use letters, digits and “-”.")
        return out

    def to_toml(self) -> str:
        return tomli_w.dumps(self.model_dump(mode="json", exclude_none=True))

    @classmethod
    def from_toml(cls, text: str) -> "Instrument":
        return cls.model_validate(tomllib.loads(text))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_toml(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Instrument":
        return cls.from_toml(path.read_text(encoding="utf-8"))


def _dupes(out: list[str], what: str, values: list[str]) -> None:
    # Case-insensitive because the default macOS filesystem is.
    seen: set[str] = set()
    for v in values:
        if v.lower() in seen:
            out.append(f"Duplicate {what}: {v}")
        seen.add(v.lower())
