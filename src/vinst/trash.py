"""Moving files to the Trash with the system's own call (NSFileManager), as the Finder does: into the Trash of
the disk they're on, with what Put Back needs to return them where they were.

A disk without a Trash (a network disk: the Finder deletes from it at once) raises NoTrash; on a system other
than macOS there's no Trash at all (available() is False). The caller asks before deleting such files for good.
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
        foundation = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Foundation.framework/Foundation")
        objc.volume_is_local_key = ctypes.c_void_p.in_dll(foundation, "NSURLVolumeIsLocalKey").value
    except (OSError, ValueError):
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


def available() -> bool:
    """Whether this system has a Trash the app can move files to (macOS)."""
    return _objc() is not None


def _on_local_disk(objc: ctypes.CDLL, url: int) -> bool:
    """[url getResourceValue:… forKey:NSURLVolumeIsLocalKey …]; True when that can't be told."""
    value, error = ctypes.c_void_p(), ctypes.c_void_p()
    ok = _send(objc, ctypes.c_bool, url, "getResourceValue:forKey:error:",
               (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(value)), (ctypes.c_void_p, objc.volume_is_local_key),
               (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(error)))
    return not ok or not value.value or bool(_send(objc, ctypes.c_bool, value.value, "boolValue"))


def _system_trash(path: Path) -> Path | None:
    """[NSFileManager trashItemAtURL:…]: where `path` went in the Trash, if the system says."""
    objc = _objc()
    if objc is None:
        raise NoTrash(f"No Trash on this system for {path.name}")
    try:
        raw = str(path).encode()
    except UnicodeEncodeError:
        raise OSError(f"{path.name}: its name isn't valid UTF-8, so it can't be moved to the Trash") from None
    pool = objc.objc_autoreleasePoolPush()
    try:
        cls = objc.objc_getClass
        string = _send(objc, ctypes.c_void_p, cls(b"NSString"), "stringWithUTF8String:", (ctypes.c_char_p, raw))
        # A nil URL would raise an Objective-C exception, which ends the whole process.
        url = _send(objc, ctypes.c_void_p, cls(b"NSURL"), "fileURLWithPath:", (ctypes.c_void_p, string)) \
            if string else None
        if not url:
            raise OSError(f"{path.name}: its name can't be handed to the system")
        manager = _send(objc, ctypes.c_void_p, cls(b"NSFileManager"), "defaultManager")
        went, error = ctypes.c_void_p(), ctypes.c_void_p()
        ok = _send(objc, ctypes.c_bool, manager, "trashItemAtURL:resultingItemURL:error:",
                   (ctypes.c_void_p, url), (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(went)),
                   (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(error)))
        if ok:
            where = _text(objc, _send(objc, ctypes.c_void_p, went.value, "path")) if went.value else ""
            return Path(where) if where else None
        code = _send(objc, ctypes.c_long, error.value, "code") if error.value else 0
        domain = _text(objc, _send(objc, ctypes.c_void_p, error.value, "domain")) if error.value else ""
        why = _text(objc, _send(objc, ctypes.c_void_p, error.value, "localizedDescription")) if error.value else ""
        # The code a network disk answers with isn't documented: any refusal there means it has no Trash.
        if code == NS_FEATURE_UNSUPPORTED_ERROR or not _on_local_disk(objc, url):
            raise NoTrash(f"{path.name}: its disk has no Trash ({domain} {code})")
        raise OSError(f"{path.name}: {why or 'it could not be moved to the Trash'} ({domain} {code})")
    finally:
        objc.objc_autoreleasePoolPop(pool)


def trash(path: Path) -> Path | None:
    """Moves `path` (a file or folder) to the Trash and returns where it went, if the system says.
    NoTrash: there is none for it."""
    return _system_trash(path)


def remove(path: Path) -> None:
    """Deletes `path` for good."""
    shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
