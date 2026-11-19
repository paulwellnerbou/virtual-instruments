"""Shared test helpers."""


def piezo_and_mic(inst):
    """The two-signal setup most slicing/preset tests exercise."""
    from vinst.spec import Signal

    inst.signals = [Signal(id="piezo", name="Piezo"), Signal(id="mic", name="Internal mic")]
    return inst
