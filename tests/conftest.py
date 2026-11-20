"""Shared test helpers."""


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
