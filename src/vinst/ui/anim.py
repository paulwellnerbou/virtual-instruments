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
/* Closed, it gives back the gap before it in its column too, so nothing below moves until it opens. */
.vi-collapse.vi-gapless { transition: grid-template-rows .25s ease, opacity .25s ease, margin-top .25s ease; }
.vi-collapse.vi-gapless:not(.vi-open) { margin-top: calc(-1 * var(--nicegui-default-gap)); }
/* Leaving: e.g. a recording's hits while it goes to the Trash. Arriving: what takes their place. */
.vi-going { opacity: 0; transform: scale(.96); pointer-events: none; transition: opacity .25s ease, transform .25s ease; }
.vi-arriving { animation: vi-arrive .25s ease both; }
@keyframes vi-arrive { from { opacity: 0; } }
"""


def add_css() -> None:
    """Once per page."""
    ui.add_css(CSS)


class Collapse:
    """A container that slides open (axis "y": downwards, "x": sideways, for items in a row)."""

    def __init__(self, open_: bool, axis: Literal["x", "y"] = "y", inner_classes: str = "",
                 gapless: bool = False) -> None:
        """`gapless`: in a column with a gap, closed it takes none (axis "y" only)."""
        self.outer = ui.element("div").classes(f"vi-collapse vi-{axis}" + (" vi-gapless" if gapless else "")
                                               + (" vi-open" if open_ else ""))
        with self.outer:
            self.inner = ui.element("div").classes(f"vi-collapse-inner {inner_classes}".strip())

    def __enter__(self) -> "Collapse":
        self.inner.__enter__()
        return self

    def __exit__(self, *exc: object) -> None:
        self.inner.__exit__(*exc)

    @property
    def is_open(self) -> bool:
        return "vi-open" in self.outer.classes

    def set_open(self, value: bool) -> None:
        if value:
            self.outer.classes(add="vi-open")
        else:
            self.outer.classes(remove="vi-open")
