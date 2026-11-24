"""Moving files to the Trash with the system's own call (NSFileManager), as the Finder does: into the Trash of
the disk they're on, with what Put Back needs to return them where they were.

A disk without a Trash (a network disk: the Finder deletes from it at once) raises NoTrash, and so does a
system other than macOS: the caller asks before deleting such files for good.
"""

import ctypes
import ctypes.util
import shutil
import sys
from functools import cache
from pathlib import Path

NS_FEATURE_UNSUPPORTED_ERROR = 3328  # NSFileManager's answer for a disk without a Trash


class NoTrash(OSError):
    """There's no Trash for this file: deleting it would be for good."""


@cache
def _objc() -> ctypes.CDLL | None:
    if sys.platform != "darwin":
        return None
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
    except OSError:
        return None
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    objc.objc_autoreleasePoolPush.restype = ctypes.c_void_p
    objc.objc_autoreleasePoolPop.argtypes = [ctypes.c_void_p]
    return objc


def _send(objc: ctypes.CDLL, restype: object, receiver: object, selector: str, *args: tuple[object, object]) -> object:
    """[receiver selector:args…]; each arg is (ctypes type, value). objc_msgSend must be called through a
    prototype of the method's own types, not as a variadic function."""
    fn = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *(t for t, _ in args))(
        ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value)
    return fn(receiver, objc.sel_registerName(selector.encode()), *(v for _, v in args))


def _text(objc: ctypes.CDLL, ns_string: int | None) -> str:
    raw = _send(objc, ctypes.c_char_p, ns_string, "UTF8String") if ns_string else None
    return raw.decode() if raw else ""


def _system_trash(path: Path) -> Path:
    """[NSFileManager trashItemAtURL:…]: where `path` went in the Trash."""
    objc = _objc()
    if objc is None:
        raise NoTrash(f"No Trash on this system for {path.name}")
    pool = objc.objc_autoreleasePoolPush()
    try:
        cls = objc.objc_getClass
        string = _send(objc, ctypes.c_void_p, cls(b"NSString"), "stringWithUTF8String:",
                       (ctypes.c_char_p, str(path).encode()))
        url = _send(objc, ctypes.c_void_p, cls(b"NSURL"), "fileURLWithPath:", (ctypes.c_void_p, string))
        manager = _send(objc, ctypes.c_void_p, cls(b"NSFileManager"), "defaultManager")
        went, error = ctypes.c_void_p(), ctypes.c_void_p()
        ok = _send(objc, ctypes.c_bool, manager, "trashItemAtURL:resultingItemURL:error:",
                   (ctypes.c_void_p, url), (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(went)),
                   (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(error)))
        if ok:
            return Path(_text(objc, _send(objc, ctypes.c_void_p, went.value, "path")))
        code = _send(objc, ctypes.c_long, error.value, "code") if error.value else 0
        why = _text(objc, _send(objc, ctypes.c_void_p, error.value, "localizedDescription")) if error.value else ""
        if code == NS_FEATURE_UNSUPPORTED_ERROR:
            raise NoTrash(f"{path.name}: its disk has no Trash")
        raise OSError(f"{path.name}: {why or 'it could not be moved to the Trash'}")
    finally:
        objc.objc_autoreleasePoolPop(pool)


def trash(path: Path) -> Path:
    """Moves `path` (a file or folder) to the Trash and returns where it went. NoTrash: there is none for it."""
    return _system_trash(path)


def remove(path: Path) -> None:
    """Deletes `path` for good."""
    shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
