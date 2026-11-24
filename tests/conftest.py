"""Shared test helpers."""

import shutil

import pytest


@pytest.fixture(autouse=True)
def trash_bin(tmp_path, monkeypatch):
    """A Trash of the test's own: nothing a test deletes reaches the real one."""
    from vinst import trash

    bin_ = tmp_path / "Trash"
    bin_.mkdir()

    def system_trash(path):
        target, n = bin_ / path.name, 2
        while target.exists():
            target, n = bin_ / f"{path.stem} {n}{path.suffix}", n + 1
        shutil.move(str(path), str(target))
        return target

    monkeypatch.setattr(trash, "_system_trash", system_trash)
    return bin_


def piezo_and_mic(inst):
    """The two-signal setup most slicing/preset tests exercise."""
    from vinst.spec import Signal

    inst.signals = [Signal(id="piezo", name="Piezo"), Signal(id="mic", name="Internal mic")]
    return inst


def lane_ranges(samples) -> set[tuple[str, str]]:
    """(loVel, hiVel) per sample file, across the velocity slices it's written in."""
    by_file: dict[str, list[tuple[int, int]]] = {}
    for s in samples:
        by_file.setdefault(s.get("path"), []).append((int(s.get("loVel")), int(s.get("hiVel"))))
    return {(str(min(lo for lo, _ in r)), str(max(hi for _, hi in r))) for r in by_file.values()}
