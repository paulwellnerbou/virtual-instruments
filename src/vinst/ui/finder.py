"""The system's file manager and folder picker, used from the (local) server."""

import os
import subprocess
import sys
from pathlib import Path

# Native dialog via AppleScript; "default location" must exist, so it falls back to the home folder.
# osascript runs in the background server, so it must bring itself to the front, or the dialog
# opens behind the browser.
CHOOSE_FOLDER = """
on run argv
    activate
    set startAt to POSIX file (item 2 of argv) as alias
    try
        return POSIX path of (choose folder with prompt (item 1 of argv) default location startAt)
    on error number -128
        return ""
    end try
end run
"""


def open_with(app: str | None, files: list[Path]) -> bool:
    """Open `files` in the application `app`, or with the system's default for them; False if that
    didn't work, e.g. the application is gone."""
    if sys.platform == "darwin":
        result = subprocess.run(["open", *(["-a", app] if app else []), *map(str, files)],
                                capture_output=True, check=False)
        return result.returncode == 0
    if sys.platform == "win32":
        try:
            for f in files:
                os.startfile(f)  # type: ignore[attr-defined]
        except OSError:
            return False
        return True
    return all(subprocess.run(["xdg-open", str(f)], check=False).returncode == 0 for f in files)


def reveal(path: Path) -> None:
    """Select the file in Finder on macOS; elsewhere open its folder."""
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], check=False)
    elif sys.platform == "win32":
        os.startfile(path.parent)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path.parent)], check=False)


def can_choose_folder() -> bool:
    return sys.platform == "darwin"


def nearest_existing(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.is_dir():
            return candidate
    return Path.home()


def choose_folder(prompt: str, start: str = "") -> str | None:
    """Ask with the macOS folder dialog; None if cancelled. Blocks until the user answers."""
    begin = nearest_existing(Path(start).expanduser()) if start else Path.home()
    result = subprocess.run(["osascript", "-e", CHOOSE_FOLDER, prompt, str(begin)],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip().rstrip("/") or None


def ensure_folder(path: Path) -> None:
    """Create the folder if needed, but never a fake mount point for a disconnected disk."""
    path = path.expanduser()
    parts = path.parts
    if len(parts) > 2 and parts[1] == "Volumes" and not Path("/", "Volumes", parts[2]).is_dir():
        raise OSError(f"The disk “{parts[2]}” isn't connected.")
    path.mkdir(parents=True, exist_ok=True)

