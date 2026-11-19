"""Animated show/hide: content slides open and fades instead of appearing or vanishing at once."""

from typing import Literal

from nicegui import ui

# Animating grid tracks from 0fr to 1fr lets the browser animate to the content's natural size,
# which height/width transitions can't do.
CSS = """
.vi-collapse { display: grid; transition: grid-template-rows .25s ease, grid-template-columns .25s ease,
               opacity .25s ease; }
.vi-collapse.vi-y { grid-template-rows: 0fr; }
.vi-collapse.vi-x { grid-template-columns: 0fr; }
.vi-collapse.vi-y.vi-open { grid-template-rows: 1fr; }
.vi-collapse.vi-x.vi-open { grid-template-columns: 1fr; }
.vi-collapse:not(.vi-open) { opacity: 0; pointer-events: none; }
.vi-collapse > .vi-collapse-inner { overflow: hidden; min-width: 0; min-height: 0; }
"""


def add_css() -> None:
    """Once per page."""
    ui.add_css(CSS)


class Collapse:
    """A container that slides open (axis "y": downwards, "x": sideways, for items in a row)."""

    def __init__(self, open_: bool, axis: Literal["x", "y"] = "y", inner_classes: str = "") -> None:
        self.outer = ui.element("div").classes(f"vi-collapse vi-{axis}" + (" vi-open" if open_ else ""))
        with self.outer:
            self.inner = ui.element("div").classes(f"vi-collapse-inner {inner_classes}".strip())

    def __enter__(self) -> "Collapse":
        self.inner.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self.inner.__exit__(*exc)

    def set_open(self, value: bool) -> None:
        if value:
            self.outer.classes(add="vi-open")
        else:
            self.outer.classes(remove="vi-open")
