"""Deleting recordings, one or all of an instrument's, with the samples sliced from them.

Recordings go to the Trash, where Put Back returns them; on a disk without a Trash they stay until
asked again (see Found.no_trash). Their sliced samples are deleted outright, as slicing the
recordings again writes them anew.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from vinst import store, takes_meta
from vinst.plan import plan_takes
from vinst.review import Manifest, TakeResult
from vinst.slicing import check_all_lanes, process_take, recordings, remove_stale_files
from vinst.spec import Instrument
from vinst.trash import NoTrash, remove, trash

# The names the app gives takes: notes ("060_C-0_p_pickup.wav"), the noise floor and extras, also of
# notes, velocities, signals and extras the instrument no longer has; ".part": a recording cut short.
TAKE_FILE = re.compile(r"^(\d{3}|noisefloor|x)_.+\.wav(\.part)?$")

Progress = Callable[[int, int, str], None]


@dataclass
class Found:
    """What deleting would remove; sizes as found, before anything went."""

    keys: set[str] = field(default_factory=set)  # recordings (TakeResult.key) that go
    files: list[Path] = field(default_factory=list)
    samples: list[Path] = field(default_factory=list)
    size: int = 0
    samples_size: int = 0
    failed: list[Path] = field(default_factory=list)  # after deleting: files that couldn't go
    no_trash: list[Path] = field(default_factory=list)  # after deleting: kept, as their disk has no Trash
    trashed: int = 0  # after deleting: files moved to the Trash
    for_good: int = 0  # after deleting with for_good: files deleted, not moved to the Trash
    files_of: dict[str, list[Path]] = field(default_factory=dict, repr=False)
    sizes: dict[Path, int] = field(default_factory=dict, repr=False)  # of the files, as found

    def size_of(self, paths: list[Path]) -> int:
        return sum(self.sizes.get(p, 0) for p in paths)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def size_text(n: int) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{max(1, round(n / 1e3))} kB" if n else "0 kB"


def went(found: Found) -> str:
    """What happened, after deleting."""
    stayed = set(found.failed + found.no_trash)
    gone = [p for p in found.files if p not in stayed]
    size = size_text(found.size_of(gone))
    n, t, d = len(gone), found.trashed, found.for_good
    if not t:
        return f"{n} file{'' if n == 1 else 's'} ({size}) deleted."
    if not d:
        return f"{n} file{'' if n == 1 else 's'} ({size}) moved to the Trash; empty it to free the space."
    return f"{n} files ({size}): {t} moved to the Trash (empty it to free the space), {d} deleted."


def takes_folder(inst: Instrument) -> Path | None:
    rec = inst.recording
    return Path(rec.takes_dir).expanduser() if rec.takes_dir and rec.source != "samples" else None


def sharing(inst: Instrument) -> list[str]:
    """Names of the other instruments that record into the same folder: their takes can't be told apart."""
    mine = takes_folder(inst)
    if mine is None:
        return []
    mine = mine.resolve()
    return [o.meta.name for o in store.list_instruments()
            if o.meta.slug != inst.meta.slug and (f := takes_folder(o)) is not None and f.resolve() == mine]


def find(inst: Instrument, manifest: Manifest | None, keys: set[str] | None = None) -> Found:
    """The files of the recordings `keys`, or with None every file in the takes folder named as the app names
    takes (TAKE_FILE); other files there are not the instrument's. All of them only where no other instrument
    records into the folder (see sharing)."""
    out = Found()
    takes_dir = takes_folder(inst)
    if takes_dir is None:
        return out
    for t in recordings(inst, takes_dir):
        if keys is not None and t.key not in keys:
            continue
        there = [takes_dir / f for f in t.files if (takes_dir / f).is_file()]
        if there:
            out.keys.add(t.key)
            out.files_of[t.key] = there
            out.files += [p for p in there if p not in out.files]
    if keys is None and takes_dir.is_dir():
        out.files += sorted(p for p in takes_dir.iterdir()
                            if TAKE_FILE.match(p.name) and p.is_file() and p not in out.files)
    if manifest is not None:
        folder = Path(manifest.samples_dir)
        gone = [r for r in manifest.takes if keys is None or r.key in keys]
        out.keys |= {r.key for r in gone if r.status != "missing"}
        out.samples = [folder / f for r in gone for h in r.hits for f in h.files.values() if (folder / f).is_file()]
    out.sizes = {p: _size(p) for p in out.files}
    out.size = sum(out.sizes.values())
    out.samples_size = sum(_size(p) for p in out.samples)
    return out


def delete(inst: Instrument, manifest: Manifest | None, keys: set[str] | None = None,
           progress: Progress | None = None, for_good: bool = False) -> tuple[Manifest | None, Found]:
    """Deletes the recordings `keys` (all with None, see find) and returns the manifest without them: a
    planned recording is then not recorded, others are gone. Rejections and cuts of their hits go too.
    Files on a disk without a Trash stay (Found.no_trash), unless `for_good`; a recording whose files
    didn't all go stays in the manifest. `inst` is only read; clean-up EQs that picked them are left to
    cleanup_eq.forget_missing."""
    found = find(inst, manifest, keys)
    for i, p in enumerate(found.files):
        if progress:
            progress(i, len(found.files), p.name)
        try:
            # One by one: Put Back then returns each where it was, not a folder of them all.
            trash(p)
            found.trashed += 1
        except NoTrash:
            if not for_good:
                found.no_trash.append(p)
                continue
            try:
                remove(p)
                found.for_good += 1
            except OSError:
                found.failed.append(p)
        except Exception:  # whatever went wrong, the others still go and the manifest follows what went
            found.failed.append(p)
    stayed_files = found.failed + found.no_trash
    takes_meta.forget(inst.meta.slug, [p.name for p in found.files if p not in stayed_files])
    if manifest is None:
        return None, found
    out = manifest.model_copy(deep=True)
    failed = {p.name for p in stayed_files}

    def stayed(r: TakeResult) -> bool:
        if r.key in found.files_of:
            return any(p.name in failed for p in found.files_of[r.key])
        return any(n.startswith(f"{r.base}_") for n in failed)  # of a note or signal the plan no longer has

    went = {r.key for r in out.takes if r.key in found.keys and not stayed(r)}
    gone = [r for r in out.takes if r.key in went]
    planned = {t.key: t for t in plan_takes(inst)}
    takes_dir = takes_folder(inst) or Path()
    kept = []
    for r in out.takes:
        if r.key not in went:
            if keys is None and r.status == "missing" and r.key not in planned:
                continue  # never recorded, and no longer planned either
            kept.append(r)
        elif r.key in planned:  # as slicing finds it now: not recorded
            kept.append(process_take(inst, planned[r.key], takes_dir, Path(out.samples_dir), None))
    out.takes = kept
    remove_stale_files(Manifest(samples_dir=out.samples_dir, takes=gone), out)
    check_all_lanes(inst, out)
    return out, found
