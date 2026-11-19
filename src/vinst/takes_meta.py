"""Where the hits were planned in takes recorded in the app.

Slicing uses this click grid to tell hits from handling noise: a piezo hears every touch of the
instrument. It is kept per instrument, keyed by file name, and only used while the file's size
and modification time match, so a take replaced from a DAW is sliced without it.
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


def save(slug: str, path: Path, hits_at: list[float]) -> None:
    """hits_at: seconds from the start of the file where each hit was asked for (beat 1)."""
    data = _load(slug)
    data[path.name] = {"stamp": _stamp(path), "hits_at": [round(t, 4) for t in hits_at]}
    target = store.instrument_dir(slug) / FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=1), encoding="utf-8")


def hits_at(slug: str, path: Path) -> list[float] | None:
    """The planned hit times, if this exact file was recorded in the app."""
    entry = _load(slug).get(path.name)
    try:
        return entry["hits_at"] if entry and entry["stamp"] == _stamp(path) else None
    except OSError:
        return None
