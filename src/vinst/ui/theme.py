"""A softer look than Quasar's defaults: rounded corners, soft shadows, inputs drawn as boxes."""

from nicegui import ui

RADIUS = "12px"
SMALL_RADIUS = "8px"

CSS = f"""
.q-card {{ border-radius: {RADIUS} !important; }}
.q-card--bordered {{ border-color: rgba(0, 0, 0, .09); }}
.q-card.q-card--flat.q-card--bordered {{ box-shadow: 0 1px 2px rgba(0, 0, 0, .04), 0 2px 10px rgba(0, 0, 0, .04); }}
.q-dialog .q-card {{ border-radius: 16px !important; }}
.q-btn:not(.q-btn--round) {{ border-radius: {SMALL_RADIUS}; }}
.q-btn-group {{ border-radius: {SMALL_RADIUS}; overflow: hidden; }}
/* More specific than the rule above, so a toggle's segments stay square inside the rounded group. */
.q-btn-group > .q-btn:not(.q-btn--round) {{ border-radius: 0; }}
.q-field--outlined .q-field__control {{ border-radius: {SMALL_RADIUS}; }}
.q-field--outlined .q-field__control:before {{ border-color: rgba(0, 0, 0, .2); }}
.q-chip {{ border-radius: 6px !important; }}
.q-menu, .q-notification, .q-tooltip {{ border-radius: {SMALL_RADIUS}; }}
.q-linear-progress {{ border-radius: 4px; }}
.q-stepper {{ border-radius: {RADIUS}; }}
.q-header {{ box-shadow: 0 1px 8px rgba(0, 0, 0, .12); }}
body.body--dark .q-card--bordered {{ border-color: rgba(255, 255, 255, .12); }}
body.body--dark .q-field--outlined .q-field__control:before {{ border-color: rgba(255, 255, 255, .25); }}
"""

_applied = False


def apply() -> None:
    """Once per process: every input-like element is drawn as an outlined box, at its usual height
    (compact only where an element asks for it)."""
    global _applied
    if _applied:
        return
    for element in (ui.input, ui.number, ui.select, ui.textarea):
        element.default_props("outlined")
    _applied = True


def add_css() -> None:
    """Once per page."""
    ui.add_css(CSS)
