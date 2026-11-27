"""Messages about the instrument, as boxes: the kind's colour and icon on the left, the text in the page's own
colour, so a long message stays as easy to read as the rest of the page."""

import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from html import escape
from typing import Literal

from nicegui import ui

from vinst.ui.anim import Collapse

Kind = Literal["error", "warning", "info"]
ICONS: dict[str, str] = {"error": "error_outline", "warning": "warning_amber", "info": "info_outline"}
# (heading, text): the heading in bold, e.g. a pass's or a recording's name, or what a build message is about.
Item = str | tuple[str | None, str]
SHORT = 70  # characters: a message up to this long reads as a heading in a list

# The tints are mixed from Quasar's colours, so they follow light and dark mode with them.
CSS = """
/* As wide as the column it's in, so boxes one below the other line up whatever their text. */
.vi-notice { display: flex; align-items: flex-start; align-self: stretch; gap: 8px; padding: 8px 12px;
             border-radius: 6px;
             font-size: 13px; line-height: 1.45; color: var(--vi-text); overflow-wrap: anywhere;
             background: color-mix(in srgb, var(--vi-notice) 9%, var(--vi-panel));
             border: 1px solid color-mix(in srgb, var(--vi-notice) 30%, var(--vi-panel)); }
.vi-notice > .q-icon { flex: none; font-size: 18px; margin-top: 0; color: var(--vi-notice); }
.vi-notice-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.vi-notice-title { font-weight: 600; font-size: 12px; color: var(--vi-notice); }
.vi-notice--info .vi-notice-title { color: var(--vi-muted); }
.vi-notice-heading { font-weight: 600; }
.vi-notice-list { gap: 10px; }
/* With more than one message in a box, each gets a bullet, its lines indented past it. */
.vi-notice-item { position: relative; padding-left: 14px; }
.vi-notice-item::before { content: ""; position: absolute; left: 2px; top: calc(.725em - 2.5px); width: 5px;
                          height: 5px; border-radius: 50%; background: var(--vi-notice); }
/* A message on a row with its button: centred on the row. */
.row.vi-notice-item::before { top: 50%; margin-top: -2.5px; }
.vi-notice-list .vi-notice-text { color: var(--vi-muted); }
.vi-notice--error { --vi-notice: var(--q-negative); }
.vi-notice--warning { --vi-notice: var(--q-warning); }
.vi-notice--info { --vi-notice: var(--vi-muted); }
.vi-notice.vi-notice--info { background: var(--vi-panel-2); border-color: var(--vi-border); }
/* In a card's rows, e.g. a note's: smaller, as the text around it. */
.vi-notice--dense { padding: 5px 10px; font-size: 12px; gap: 6px; }
.vi-notice--dense > .q-icon { font-size: 16px; }
.vi-notice--dense .vi-notice-body { gap: 4px; }
.vi-notice-body .q-btn { align-self: flex-start; }
/* On a line with other things, e.g. beside a recording's name: only the icon is coloured. */
.vi-notice-inline > .q-icon { flex: none; font-size: 1.3em; color: var(--vi-notice); }
"""


def add_css() -> None:
    """Once per page."""
    ui.add_css(CSS)


@contextmanager
def box(kind: Kind = "warning", *, dense: bool = False, title: str | None = None) -> Iterator[ui.element]:
    """An empty notice: what's added inside goes next to its icon, one below the other."""
    with ui.element("div").classes(f"vi-notice vi-notice--{kind}" + (" vi-notice--dense" if dense else "")):
        ui.icon(ICONS[kind])
        with ui.element("div").classes("vi-notice-body") as body:
            if title:
                ui.label(title).classes("vi-notice-title")
            yield body


def show(items: Item | Iterable[Item], kind: Kind = "warning", *, dense: bool = False,
           title: str | None = None) -> None:
    """One box for a message, or for several of a kind, each a paragraph of its own."""
    many = [items] if isinstance(items, (str, tuple)) else list(items)
    with box(kind, dense=dense, title=title):
        for item in many:
            paragraph(item).classes("vi-notice-item" if len(many) > 1 else "")


def inline(text: str, kind: Kind = "warning", *, truncate: bool = True) -> ui.row:
    """A message on one line with other things, after the kind's icon; cut off, it shows whole when hovered."""
    with ui.row().classes(f"vi-notice-inline vi-notice--{kind} items-center no-wrap gap-1 min-w-0") as row:
        ui.icon(ICONS[kind])
        label = ui.label(text).classes("truncate" if truncate else "")
        if truncate:
            label.tooltip(text)
    return row


def paragraph(item: Item) -> ui.element:
    heading, text = item if isinstance(item, tuple) else (None, item)
    if heading:
        return ui.html(f'<span class="vi-notice-heading">{escape(heading)}</span> {escape(text)}', sanitize=False)
    return ui.label(text)


def item_list(items: Iterable[Item], kind: Kind, *, title: str | None = None, limit: int = 8) -> None:
    """Several messages, each with its heading on a line of its own and its text, quieter, below. Past `limit`,
    the rest open with a button, so a long list doesn't push what follows off the page."""
    many = list(items)
    with box(kind, title=title) as body:
        body.classes("vi-notice-list")
        bullets = len(many) > 1
        for item in many[:limit]:
            _entry(item, bullets)
        rest = many[limit:]
        if rest:
            with Collapse(False, inner_classes="flex flex-col gap-[10px]") as more:
                for item in rest:
                    _entry(item, bullets)

            def toggle() -> None:
                more.set_open(not more.is_open)
                button.text = "Show fewer" if more.is_open else f"Show {len(rest)} more"

            button = ui.button(f"Show {len(rest)} more", on_click=toggle).props("flat dense no-caps size=sm")


def _entry(item: Item, bullet: bool) -> None:
    heading, text = item if isinstance(item, tuple) else (None, item)
    if heading is None and len(text) <= SHORT:  # e.g. "Level lowered by 3.0 dB.": a heading of its own
        heading, text = text.removesuffix("."), ""
    with ui.column().classes("gap-0" + (" vi-notice-item" if bullet else "")):
        if heading:
            ui.label(heading).classes("vi-notice-heading")
        if text:
            ui.label(text).classes("vi-notice-text" if heading else "")


# What a message is about, before its first ": ", when that's a short phrase and not a sentence of its own.
_HEADING = re.compile(r"^([^:]{3,70}?)(?<!\.): (.+)$", re.S)


def headed(message: str) -> tuple[str | None, str]:
    """A message of the build's, "<what>: <details>", as its heading and the details."""
    m = _HEADING.match(message)
    if not m or ". " in m[1]:
        return None, message
    rest = m[2]
    # A word, not a file or sample name, which keeps its case.
    return m[1], rest[:1].upper() + rest[1:] if re.match(r"[a-z]+ ", rest) else rest
