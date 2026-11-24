"""Moving files to the Trash: on macOS into the Trash of the disk they are on, elsewhere deleting them.

A network disk has no Trash (the Finder deletes from it at once), so files there are deleted too;
has_trash tells beforehand, for the question asked before deleting.
"""

import ctypes
import ctypes.util
import os
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

MNT_LOCAL = 0x1000
MNT_RDONLY = 0x1


class _StatFs(ctypes.Structure):
    """struct statfs with 64-bit inodes (sys/mount.h)."""

    _fields_ = [("f_bsize", ctypes.c_uint32), ("f_iosize", ctypes.c_int32), ("f_blocks", ctypes.c_uint64),
                ("f_bfree", ctypes.c_uint64), ("f_bavail", ctypes.c_uint64), ("f_files", ctypes.c_uint64),
                ("f_ffree", ctypes.c_uint64), ("f_fsid", ctypes.c_int32 * 2), ("f_owner", ctypes.c_uint32),
                ("f_type", ctypes.c_uint32), ("f_flags", ctypes.c_uint32), ("f_fssubtype", ctypes.c_uint32),
                ("f_fstypename", ctypes.c_char * 16), ("f_mntonname", ctypes.c_char * 1024),
                ("f_mntfromname", ctypes.c_char * 1024), ("f_flags_ext", ctypes.c_uint32),
                ("f_reserved", ctypes.c_uint32 * 7)]


@dataclass(frozen=True)
class Mount:
    point: Path
    flags: int


@cache
def _statfs() -> Callable | None:
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        # On Intel Macs the plain symbol is the 32-bit-inode layout.
        return getattr(libc, "statfs$INODE64", None) or libc.statfs
    except (OSError, AttributeError):
        return None


def mount_of(path: Path) -> Mount | None:
    """The disk `path` is on (its nearest existing folder's); None where that can't be told."""
    fn = _statfs() if sys.platform == "darwin" else None
    if fn is None:
        return None
    while not path.exists() and path.parent != path:
        path = path.parent
    buf = _StatFs()
    if fn(os.fsencode(str(path)), ctypes.byref(buf)) != 0:
        return None
    return Mount(Path(os.fsdecode(buf.f_mntonname)), buf.f_flags)


def bin_for(path: Path) -> Path | None:
    """The Trash the Finder uses for `path`: the home folder's, or on another disk that disk's `.Trashes`, so
    nothing is copied across disks and the space is freed where it was taken. None: the disk has none."""
    home = Path.home() / ".Trash"
    mount = mount_of(path)
    if mount is None:
        return home
    own = mount_of(Path.home())
    if own is not None and own.point == mount.point:
        return home
    if not mount.flags & MNT_LOCAL or mount.flags & MNT_RDONLY:
        return None
    return mount.point / ".Trashes" / str(os.getuid())


def has_trash(path: Path) -> bool:
    """Whether deleting `path` moves it to a Trash, or deletes it for good."""
    return sys.platform == "darwin" and bin_for(path) is not None


def _free_name(folder: Path, name: str) -> Path:
    target = folder / name
    if target.exists():  # as the Finder names a second item of the same name
        target = folder / f"{name} {time.strftime('%H.%M.%S')}"
    n = 2
    while target.exists():
        target = folder / f"{name} {time.strftime('%H.%M.%S')} {n}"
        n += 1
    return target


def _remove(path: Path) -> None:
    shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()


def trash(path: Path) -> Path | None:
    """Moves `path` to the Trash and returns where it went; None when it was deleted (see has_trash)."""
    bin_ = bin_for(path) if sys.platform == "darwin" else None
    if bin_ is None:
        _remove(path)
        return None
    bin_.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = _free_name(bin_, path.name)
    shutil.move(str(path), str(target))
    return target


def trash_together(paths: list[Path], name: str,
                   progress: Callable[[int, int, str], None] | None = None) -> tuple[Path | None, list[Path]]:
    """Moves the files to one folder `name` in the Trash, to be put back in one go, or deletes them where there
    is no Trash. Returns that folder (None when deleted or nothing went) and the files that couldn't go."""
    paths = [p for p in paths if p.exists()]
    if not paths:
        return None, []
    bin_ = bin_for(paths[0]) if sys.platform == "darwin" else None
    folder = None
    if bin_ is not None:
        bin_.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder = _free_name(bin_, name)
        folder.mkdir()
    failed = []
    for i, p in enumerate(paths):
        if progress:
            progress(i, len(paths), p.name)
        try:
            if folder is None:
                _remove(p)
            else:
                shutil.move(str(p), str(_free_name(folder, p.name)))
        except OSError:
            failed.append(p)
    if folder is not None and len(failed) == len(paths):
        folder.rmdir()
        folder = None
    return folder, failed
