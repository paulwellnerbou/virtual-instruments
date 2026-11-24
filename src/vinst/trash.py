"""Moving files to the Trash: on macOS into the bin of the disk they are on, elsewhere deleting them."""

import os
import shutil
import sys
import time
from pathlib import Path


def bin_for(path: Path) -> Path:
    """The Trash the Finder uses for `path`: the home folder's, or on another disk its own `.Trashes`,
    so nothing is copied across disks and the space is freed where it was taken."""
    home = Path.home() / ".Trash"
    try:
        dev = path.stat().st_dev
        if dev == Path.home().stat().st_dev:
            return home
    except OSError:
        return home
    mount = path
    while mount.parent != mount and mount.parent.stat().st_dev == dev:
        mount = mount.parent
    return mount / ".Trashes" / str(os.getuid())


def _free_name(folder: Path, name: str) -> Path:
    target = folder / name
    if target.exists():  # as the Finder names a second item of the same name
        target = folder / f"{name} {time.strftime('%H.%M.%S')}"
    n = 2
    while target.exists():
        target = folder / f"{name} {time.strftime('%H.%M.%S')} {n}"
        n += 1
    return target


def trash(path: Path) -> Path | None:
    """Moves `path` to the Trash on macOS and returns where it went; elsewhere deletes it (None)."""
    if sys.platform == "darwin":
        bin_ = bin_for(path)
        bin_.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = _free_name(bin_, path.name)
        shutil.move(str(path), str(target))
        return target
    shutil.rmtree(path) if path.is_dir() else path.unlink()
    return None


def trash_together(paths: list[Path], name: str) -> Path | None:
    """Moves files to one folder `name` in the Trash, to be put back in one go; elsewhere deletes them.
    Where the folder went (None when deleted or nothing to move)."""
    paths = [p for p in paths if p.exists()]
    if not paths:
        return None
    if sys.platform != "darwin":
        for p in paths:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        return None
    bin_ = bin_for(paths[0])
    bin_.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder = _free_name(bin_, name)
    folder.mkdir()
    for p in paths:
        shutil.move(str(p), str(_free_name(folder, p.name)))
    return folder
