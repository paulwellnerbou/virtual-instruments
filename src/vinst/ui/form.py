"""One setting per row: its label in a fixed column on the left, its controls to the right.

Explanations go into an ⓘ tooltip next to the label, so rows never wrap into each other and
it's always clear which control a button belongs to.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from nicegui import ui

LABEL_W = "w-28"
LABEL_CHARS = 12  # what fits into LABEL_W


@contextmanager
def setting(label: str, hint: str | None = None) -> Iterator[ui.row]:
    with ui.row().classes("vi-setting w-full items-center no-wrap gap-2 min-h-[40px]"):
        with ui.row().classes(f"{LABEL_W} shrink-0 items-center gap-1 no-wrap"):
            name = ui.label(label).classes("text-sm opacity-80 truncate")
            if len(label) > LABEL_CHARS:  # only then can it be cut off
                name.tooltip(label)
            if hint:
                ui.icon("info_outline", size="16px").classes("opacity-50 cursor-help").tooltip(hint)
        with ui.row().classes("grow min-w-0 items-center gap-1 no-wrap") as controls:
            yield controls


def note(text: str, style: str = "text-warning") -> None:
    """A message about the settings above, across the whole row."""
    ui.label(text).classes(f"text-xs {style} w-full")
