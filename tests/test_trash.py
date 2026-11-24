"""The Trash of the disk a file is on, and none on a network disk."""

import sys

import pytest

from vinst import trash
from vinst.trash import MNT_LOCAL, Mount

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the Trash is the Finder's")


@pytest.fixture
def disks(tmp_path, monkeypatch):
    """The home folder on one disk; `ext` a local disk of its own, `nas` a network one."""
    home, ext, nas = tmp_path / "home", tmp_path / "ext", tmp_path / "nas"
    for d in (home / ".Trash", ext, nas):
        d.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    mounts = {home: Mount(tmp_path, MNT_LOCAL), ext: Mount(ext, MNT_LOCAL), nas: Mount(nas, 0)}

    def mount_of(path):
        return next((m for root, m in mounts.items() if path.is_relative_to(root)), mounts[home])

    monkeypatch.setattr(trash, "mount_of", mount_of)
    return home, ext, nas


def test_each_disk_has_its_own_trash_and_a_network_disk_none(disks):
    import os

    home, ext, nas = disks
    assert trash.bin_for(home / "Music" / "a.wav") == home / ".Trash"
    assert trash.bin_for(ext / "takes" / "a.wav") == ext / ".Trashes" / str(os.getuid())
    assert trash.bin_for(nas / "a.wav") is None and not trash.has_trash(nas / "a.wav")


def test_files_go_to_their_disks_trash_or_for_good(disks):
    import os

    home, ext, nas = disks
    on_ext, on_nas = ext / "a.wav", nas / "a.wav"
    on_ext.write_bytes(b"a")
    on_nas.write_bytes(b"a")
    assert trash.trash(on_ext) == ext / ".Trashes" / str(os.getuid()) / "a.wav" and not on_ext.exists()
    assert trash.trash(on_nas) is None and not on_nas.exists()


def test_together_in_one_folder_named_apart_from_an_earlier_one(disks, monkeypatch):
    home, _, nas = disks
    for name in ("a.wav", "b.wav"):
        (home / name).write_bytes(b"x")
    (home / ".Trash" / "Uke recordings").mkdir()
    real_move = trash.shutil.move

    def move(src, dst):
        if src.endswith("b.wav"):
            raise PermissionError("locked")
        return real_move(src, dst)

    monkeypatch.setattr(trash.shutil, "move", move)
    folder, failed = trash.trash_together([home / "a.wav", home / "b.wav"], "Uke recordings")
    assert folder.parent == home / ".Trash" and folder.name.startswith("Uke recordings ")
    assert (folder / "a.wav").exists() and failed == [home / "b.wav"] and (home / "b.wav").exists()
    (nas / "c.wav").write_bytes(b"x")
    assert trash.trash_together([nas / "c.wav"], "Uke recordings") == (None, [])
    assert not (nas / "c.wav").exists()
