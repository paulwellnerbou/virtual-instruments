"""The system's own Trash call, on a disk image of the test's own."""

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vinst import trash

SYSTEM_TRASH = trash._system_trash  # before the autouse fixture swaps it for a test Trash


def put_back_recorded(trashes: Path, name: str) -> bool:
    """The Finder's Put Back records (ptbL, ptbN) for `name`, written a moment after the move."""
    store = trashes / ".DS_Store"
    for _ in range(50):
        data = store.read_bytes() if store.exists() else b""
        at = data.find(name.encode("utf-16-be") + b"ptbL")
        if at != -1:
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def disk(tmp_path):
    """A small disk of its own, attached for the test: nothing reaches the real Trash."""
    if sys.platform != "darwin" or not shutil.which("hdiutil"):
        pytest.skip("macOS disk images")
    image, mount = tmp_path / "disk.dmg", tmp_path / "mnt"
    for step in (["create", "-quiet", "-size", "8m", "-fs", "ExFAT", "-volname", "VINSTTEST", str(image)],
                 ["attach", "-quiet", "-nobrowse", "-mountpoint", str(mount), str(image)]):
        done = subprocess.run(["hdiutil", *step], capture_output=True, text=True)
        if done.returncode:
            pytest.skip(f"hdiutil {step[0]} isn't allowed here: {done.stderr.strip()}")
    yield mount
    subprocess.run(["hdiutil", "detach", "-quiet", "-force", str(mount)], check=False)


def test_files_go_to_their_disks_trash_where_put_back_finds_them(disk):
    import os

    takes = disk / "takes"
    takes.mkdir()
    for name in ("060_C-0_p_pickup.wav", "060_C-0_f_pickup.wav"):
        (takes / name).write_bytes(b"audio")
    went = [SYSTEM_TRASH(takes / name) for name in ("060_C-0_p_pickup.wav", "060_C-0_f_pickup.wav")]
    trashes = disk / ".Trashes" / str(os.getuid())
    assert [p.parent for p in went] == [trashes, trashes] and all(p.exists() for p in went)
    assert not any(takes.iterdir())
    # Both, though trashed right after each other: the process lives on, as the app's server does.
    assert put_back_recorded(trashes, "060_C-0_p_pickup.wav") and put_back_recorded(trashes, "060_C-0_f_pickup.wav")


def test_a_file_that_isnt_there_is_an_error_not_a_missing_trash(disk):
    with pytest.raises(OSError) as e:
        SYSTEM_TRASH(disk / "gone.wav")
    assert not isinstance(e.value, trash.NoTrash)


def test_without_the_system_call_there_is_no_trash(monkeypatch, tmp_path):
    monkeypatch.setattr(trash, "_objc", lambda: None)
    assert not trash.available()
    (tmp_path / "a.wav").write_bytes(b"x")
    with pytest.raises(trash.NoTrash):
        SYSTEM_TRASH(tmp_path / "a.wav")
    trash.remove(tmp_path / "a.wav")
    assert not (tmp_path / "a.wav").exists()


def test_any_refusal_on_a_network_disk_means_it_has_no_trash(disk, monkeypatch):
    monkeypatch.setattr(trash, "_on_local_disk", lambda objc, url: False)
    with pytest.raises(trash.NoTrash):
        SYSTEM_TRASH(disk / "gone.wav")  # refused for another reason: there, too, it can't go to a Trash


def test_a_name_the_system_cant_take_is_an_error_not_a_crash(tmp_path):
    if not trash.available():
        pytest.skip("macOS only")
    with pytest.raises(OSError) as e:
        SYSTEM_TRASH(tmp_path / "bad\udcff.wav")
    assert not isinstance(e.value, trash.NoTrash)
