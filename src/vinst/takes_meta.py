"""What the app knows about a take beyond its audio: where its hits were planned, and cuts set by hand.

Slicing uses the click grid of takes recorded in the app to tell hits from handling noise: a piezo
hears every touch of the instrument. Cuts set by hand replace the found ones. Both are kept per
instrument, keyed by file name, and only used while the file's size and modification time match,
so a take replaced from a DAW is sliced without them. A take opened for editing from the app keeps
them as long as its length stays the same: a click taken out moves no hit.
"""

import json
from pathlib import Path

from vinst import store

FILE = "recorded-in-app.json"


def _stamp(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _load(slug: str) -> dict:
    try:
        return json.loads((store.instrument_dir(slug) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(slug: str, data: dict) -> None:
    if not store.exists(slug):  # deleted meanwhile: writing would bring its folder back without settings
        return
    target = store.instrument_dir(slug) / FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=1), encoding="utf-8")


def _shape(path: Path) -> list[int]:
    """[frames, sample rate]: what an edit that moves no hit leaves as it was."""
    import soundfile as sf

    info = sf.info(str(path))
    return [int(info.frames), int(info.samplerate)]


def _entry(slug: str, path: Path) -> dict | None:
    """The file's entry, if it's still the file it was made for, or that file edited in place."""
    data = _load(slug)
    entry = data.get(path.name)
    if not entry:
        return None
    try:
        stamp = _stamp(path)
        if entry.get("stamp") == stamp:
            return entry
        if entry.get("editing") != _shape(path):
            return None
    except (OSError, RuntimeError):  # gone, or not audio any more
        return None
    entry["stamp"] = stamp
    _save(slug, data)
    return entry


def recorded_now(slug: str, path: Path) -> bool:
    """Whether the file is as the app recorded it: a new recording, not one edited elsewhere."""
    entry = _load(slug).get(path.name)
    try:
        return bool(entry) and entry.get("stamp") == _stamp(path) and "editing" not in entry
    except OSError:
        return False


def editing(slug: str, path: Path) -> None:
    """The file is about to be edited elsewhere: its grid and hand cuts stay while its length does."""
    data = _load(slug)
    entry = _entry(slug, path)
    if entry is None:
        return
    entry["editing"] = _shape(path)
    data[path.name] = entry
    _save(slug, data)


def save(slug: str, path: Path, hits_at: list[float]) -> None:
    """hits_at: seconds from the start of the file where each hit was asked for (beat 1). A new
    recording, so cuts set by hand for the file it replaces are dropped."""
    data = _load(slug)
    data[path.name] = {"stamp": _stamp(path), "hits_at": [round(t, 4) for t in hits_at]}
    _save(slug, data)


def save_cuts(slug: str, path: Path, cuts: list[tuple[int, int]] | None) -> None:
    """(start, end) in frames per hit, set by hand; None goes back to finding them."""
    data = _load(slug)
    entry = _entry(slug, path) or {"stamp": _stamp(path)}
    if cuts is None:
        entry.pop("cuts", None)
    else:
        entry["cuts"] = [[int(a), int(b)] for a, b in sorted(cuts)]
    data[path.name] = entry
    _save(slug, data)


def cuts(slug: str, path: Path) -> list[tuple[int, int]] | None:
    """The cuts set by hand, if any and this is still the same file."""
    entry = _entry(slug, path)
    return [(a, b) for a, b in entry["cuts"]] if entry and "cuts" in entry else None


def rename(slug: str, old_name: str, new_name: str) -> None:
    """After a recording's file was renamed: its click grid goes with it."""
    data = _load(slug)
    if old_name not in data:
        return
    data[new_name] = data.pop(old_name)
    _save(slug, data)


def hits_at(slug: str, path: Path) -> list[float] | None:
    """The planned hit times, if this exact file was recorded in the app."""
    entry = _entry(slug, path)
    return entry.get("hits_at") if entry else None


def forget(slug: str, names: list[str]) -> None:
    """The files were deleted: a recording made later under the same name starts without their grid and cuts."""
    data = _load(slug)
    if any(n in data for n in names):
        _save(slug, {k: v for k, v in data.items() if k not in names})
