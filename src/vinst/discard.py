"""Deleting recordings, one or all of an instrument's, with the samples sliced from them.

Recordings go to the Trash, to be put back if deleted by mistake; their sliced samples are deleted
outright, as slicing the recordings again writes them anew.
"""

import glob
from dataclasses import dataclass, field
from pathlib import Path

from vinst import takes_meta
from vinst.plan import plan_takes
from vinst.review import Manifest
from vinst.slicing import check_all_lanes, process_take, recordings, remove_stale_files
from vinst.spec import Instrument
from vinst.trash import trash, trash_together


@dataclass
class Found:
    """What deleting would remove; sizes as found, before anything went."""

    keys: set[str] = field(default_factory=set)  # recordings (TakeResult.key) that go
    files: list[Path] = field(default_factory=list)
    samples: list[Path] = field(default_factory=list)
    size: int = 0
    samples_size: int = 0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def size_text(n: int) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} kB" if n else "0 kB"


def find(inst: Instrument, manifest: Manifest | None, keys: set[str] | None = None) -> Found:
    """The files of the recordings `keys`, or with None of all the instrument's recordings, also ones of
    velocities and signals it no longer has; files in the folder named otherwise are not its own."""
    out = Found()
    if not inst.recording.takes_dir or inst.recording.source == "samples":
        return out
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    for t in recordings(inst, takes_dir):
        if keys is not None and t.key not in keys:
            continue
        there = [takes_dir / f for f in t.files if (takes_dir / f).is_file()]
        if there:
            out.keys.add(t.key)
            out.files += [p for p in there if p not in out.files]
    if keys is None and takes_dir.is_dir():
        bases = {t.note_base or t.base_name for t in plan_takes(inst)}
        for base in sorted(bases):
            for p in sorted(takes_dir.glob(glob.escape(base) + "_*.wav")):
                if p.is_file() and p not in out.files:
                    out.files.append(p)
    if manifest is not None:
        folder = Path(manifest.samples_dir)
        gone = [r for r in manifest.takes if keys is None or r.key in keys]
        out.keys |= {r.key for r in gone if r.status != "missing"}
        out.samples = [folder / f for r in gone for h in r.hits for f in h.files.values() if (folder / f).is_file()]
    out.size = sum(_size(p) for p in out.files)
    out.samples_size = sum(_size(p) for p in out.samples)
    return out


def delete(inst: Instrument, manifest: Manifest | None, keys: set[str] | None = None) -> tuple[Manifest | None, Found]:
    """Deletes the recordings `keys` (all with None, see find) and returns the manifest without them: a
    planned recording is then not recorded, others are gone. Rejections and cuts of their hits go too.
    `inst` is only read; clean-up EQs that picked them are left to cleanup_eq.forget_missing."""
    found = find(inst, manifest, keys)
    if keys is None:
        trash_together(found.files, f"{inst.meta.name} recordings")
    else:
        for p in found.files:
            trash(p)
    takes_meta.forget(inst.meta.slug, [p.name for p in found.files])
    if manifest is None:
        return None, found
    out = manifest.model_copy(deep=True)
    gone = [r for r in out.takes if r.key in found.keys]
    planned = {t.key: t for t in plan_takes(inst)}
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    kept = []
    for r in out.takes:
        if r.key not in found.keys:
            kept.append(r)
        elif r.key in planned:  # as slicing finds it now: not recorded
            kept.append(process_take(inst, planned[r.key], takes_dir, Path(out.samples_dir), None))
    out.takes = kept
    remove_stale_files(Manifest(samples_dir=out.samples_dir, takes=gone), out)
    if inst.recording.source != "samples":
        check_all_lanes(inst, out)
    return out, found
