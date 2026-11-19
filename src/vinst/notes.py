"""MIDI note numbers and names, using DecentSampler's convention (middle C = C3 = 60)."""

import re

SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_RE = re.compile(r"^\s*([A-Ga-g])([#b]?)\s*(-?\d+)\s*$")


def name(midi: int) -> str:
    return f"{SHARP_NAMES[midi % 12]}{midi // 12 - 2}"


def label(midi: int) -> str:
    """Human label with both forms, e.g. 'E3 (64)'."""
    return f"{name(midi)} ({midi})"


def is_black(midi: int) -> bool:
    return midi % 12 in (1, 3, 6, 8, 10)


def parse(value: str | int) -> int:
    """Accept a MIDI number or a note name like 'G2', 'c#3', 'Db4'."""
    if isinstance(value, int):
        midi = value
    else:
        text = value.strip()
        if re.fullmatch(r"\d+", text):
            midi = int(text)
        else:
            m = _NOTE_RE.match(text)
            if not m:
                raise ValueError(f"not a note: {value!r}")
            letter, accidental, octave = m.groups()
            letter = letter.upper()
            # Same rule as DecentSampler: sharps only on C D F G A, flats only on D E G A B.
            if (accidental == "#" and letter in "EB") or (accidental == "b" and letter in "CF"):
                raise ValueError(f"not a note: {value!r}")
            midi = _PITCH_CLASS[letter] + (12 * (int(octave) + 2))
            midi += {"#": 1, "b": -1, "": 0}[accidental]
    if not 0 <= midi <= 127:
        raise ValueError(f"note out of MIDI range: {value!r}")
    return midi
