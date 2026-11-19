"""Per-type defaults and tuning presets the wizard starts from."""

import getpass
import os

from vinst import notes
from vinst.spec import (
    Dynamics,
    Extra,
    Instrument,
    InstrumentString,
    InstrumentType,
    Meta,
    Range,
    Recording,
    SUSTAINED_TYPES,
    Signal,
    Sound,
    slugify,
)

def system_author() -> str:
    """The logged-in user's full name from the operating system, else the login name."""
    try:
        import pwd

        full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
        if full:
            return full
    except (ImportError, KeyError):  # Windows has no pwd module
        pass
    return getpass.getuser()


def _strings(spec: str, frets: int) -> list[InstrumentString]:
    """'G2 C3 E3 A3' -> strings named by pitch class, uniquified with the octave if needed."""
    opens = [notes.parse(n) for n in spec.split()]
    letters = [notes.name(m).rstrip("-0123456789") for m in opens]
    names = [
        notes.name(m) if letters.count(letter) > 1 else letter
        for m, letter in zip(opens, letters)
    ]
    return [InstrumentString(name=n, open=m, frets=frets) for n, m in zip(names, opens)]


# name -> (open notes low-to-high as played, default fret count)
TUNINGS: dict[str, tuple[str, int]] = {
    "Ukulele, low G (G C E A)": ("G2 C3 E3 A3", 12),
    "Ukulele, re-entrant (g C E A)": ("G3 C3 E3 A3", 12),
    "Baritone ukulele (D G B E)": ("D2 G2 B2 E3", 18),
    "Guitar (E A D G B E)": ("E1 A1 D2 G2 B2 E3", 19),
    "Mandolin / violin (G D A E)": ("G2 D3 A3 E4", 17),
    "Cavaquinho (D G B D)": ("D3 G3 B3 D4", 17),
}


def tuning_strings(tuning: str, frets: int | None = None) -> list[InstrumentString]:
    spec, default_frets = TUNINGS[tuning]
    return _strings(spec, frets if frets is not None else default_frets)


def type_defaults(kind: InstrumentType) -> tuple[Range, Dynamics, list[Extra], Recording]:
    match kind:
        case InstrumentType.PLUCKED:
            return (
                Range(mode="strings", strings=tuning_strings("Ukulele, low G (G C E A)")),
                Dynamics(layers=["p", "mf", "f"], round_robins=4),
                [
                    Extra(id="release", name="Release noise", hits=12, use="release",
                          instructions="Fret a note, let it ring briefly, lift the finger off; vary the fret."),
                    Extra(id="chuck", name="Chuck / muted strum", hits=10, use="key", key=notes.parse("C2"),
                          instructions="Muted strums, alternate down and up."),
                    Extra(id="knock", name="Body knock", hits=10, use="key", key=notes.parse("D2"),
                          instructions="Knuckle or fingertip taps on the body."),
                ],
                Recording(ring_seconds=6.0),
            )
        case InstrumentType.PITCHED_PERCUSSION:
            return (
                Range(mode="notes", low=notes.parse("C3"), high=notes.parse("C5")),
                Dynamics(layers=["p", "mf", "f"], round_robins=3),
                [],
                Recording(ring_seconds=6.0),
            )
        case InstrumentType.SUSTAINED:
            return (
                Range(mode="notes", low=notes.parse("G2"), high=notes.parse("E5"), density=3),
                Dynamics(layers=["mp", "f"], round_robins=2),
                [],
                Recording(ring_seconds=5.0),
            )
        case InstrumentType.UNPITCHED:
            return (
                Range(mode="sounds", sounds=[Sound(name="Hit", key=notes.parse("C3"))]),
                Dynamics(layers=["soft", "hard"], round_robins=6),
                [],
                Recording(ring_seconds=2.0),
            )
        case InstrumentType.SINGLE_SAMPLE:
            return (
                Range(mode="notes", low=notes.parse("C3"), high=notes.parse("C3")),
                Dynamics(layers=["normal"], round_robins=1),
                [],
                Recording(ring_seconds=10.0, noise_floor_seconds=10),
            )
    raise ValueError(kind)


def new_instrument(name: str, kind: InstrumentType, author: str | None = None) -> Instrument:
    rng, dyn, extras, rec = type_defaults(kind)
    signals = (
        [Signal(id="pickup", name="Pickup")]
        if kind == InstrumentType.PLUCKED
        else [Signal(id="mic", name="Mic")]
    )
    inst = Instrument(
        meta=Meta(name=name, slug=slugify(name), type=kind, author=system_author() if author is None else author),
        signals=signals,
        range=rng,
        dynamics=dyn,
        extras=extras,
        recording=rec,
    )
    inst.processing.loops = kind in SUSTAINED_TYPES
    return inst


SIGNAL_SUGGESTIONS = ["Pickup", "Piezo", "Internal mic", "External mic", "DI", "Room mic", "Close mic"]
