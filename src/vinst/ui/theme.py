"""A desktop-app look over Quasar's Material defaults: system font, hairline borders instead of shadows,
compact controls, and colours that follow the system's light or dark mode."""

from nicegui import ui

CSS = """
/* Quasar reads its brand colours from these. NiceGUI sets its defaults inline on body; !important wins. */
body {
  --vi-bg: #f6f5f2;
  --vi-panel: #ffffff;
  --vi-panel-2: #f1f0ec;
  --vi-border: #e1ded8;
  --vi-border-strong: #cbc7bf;
  --vi-text: #1f1d1a;
  --vi-muted: #7d7870;
  --vi-hover: rgba(0, 0, 0, .05);
  --vi-focus: rgba(10, 110, 230, .35);
  --q-primary: #0a6ee6 !important;
  --q-positive: #248a3d !important;
  --q-negative: #d70015 !important;
  --q-warning: #c97a00 !important;
  --q-dark-page: #1b1a19 !important;
  --q-dark: #232220 !important;
}
body.body--dark {
  --vi-bg: #1b1a19;
  --vi-panel: #232220;
  --vi-panel-2: #2b2a27;
  --vi-border: #3a3835;
  --vi-border-strong: #4d4a46;
  --vi-text: #ebe8e3;
  --vi-muted: #9a958d;
  --vi-hover: rgba(255, 255, 255, .06);
  --vi-focus: rgba(10, 132, 255, .45);
  --q-primary: #0a84ff !important;
  --q-positive: #30d158 !important;
  --q-negative: #ff453a !important;
  --q-warning: #ff9f0a !important;
}

/* Tailwind sizes are in rem: a smaller root makes text and spacing desktop-sized together. */
html { font-size: 14px; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 13px;
  line-height: 1.4;
  color: var(--vi-text);
  background: var(--vi-bg);
  -webkit-font-smoothing: antialiased;
}
body.body--dark { background: var(--vi-bg); color: var(--vi-text); }

/* Toolbar instead of the Material app bar. */
.q-header.q-header {
  background: var(--vi-panel) !important;
  color: var(--vi-text);
  border-bottom: 1px solid var(--vi-border);
  box-shadow: none;
}
.q-header { min-height: 44px; }

/* Panels: a hairline border, no shadow. */
.q-card {
  background: var(--vi-panel);
  color: var(--vi-text);
  border: 1px solid var(--vi-border);
  border-radius: 8px;
  box-shadow: none;
}
.q-dialog .q-card { border-radius: 12px; box-shadow: 0 12px 40px rgba(0, 0, 0, .22); }
.q-menu { background: var(--vi-panel); color: var(--vi-text); border: 1px solid var(--vi-border);
          border-radius: 8px; box-shadow: 0 8px 24px rgba(0, 0, 0, .14); }
.q-separator { background: var(--vi-border); }
.q-item { min-height: 32px; padding: 4px 12px; }
.q-item.q-hoverable:hover > .q-focus-helper { background: var(--vi-hover); opacity: 1; }

/* Buttons: small, sentence case, no Material shadow or ripple. */
.q-btn { font-size: 13px; font-weight: 500; text-transform: none; letter-spacing: normal; }
.q-btn:not(.q-btn--round) { border-radius: 6px; min-height: 28px; padding: 2px 12px; }
.q-btn.q-btn--dense:not(.q-btn--round) { min-height: 24px; padding: 0 8px; }
.q-btn:before { box-shadow: none !important; }
.q-btn .q-ripple { display: none; }
.q-btn .q-icon { font-size: 1.25em; }
.q-btn .q-icon.on-left { margin-right: 6px; }
.q-btn .q-icon.on-right { margin-left: 6px; }
.q-btn--round { min-width: 2.4em; min-height: 2.4em; }
.q-btn--round.q-btn--dense { min-width: 2em; min-height: 2em; }
/* Flat buttons with words are bordered push buttons; icon-only ones stay bare. */
.q-btn--flat:not(.q-btn--round) {
  background: var(--vi-panel);
  border: 1px solid var(--vi-border-strong);
}
.q-btn--flat:not(.q-btn--round):hover { background: var(--vi-panel-2); }
.q-btn--flat:hover > .q-focus-helper { opacity: 0 !important; }
.q-btn--flat.q-btn--round:hover { background: var(--vi-hover); }
.q-btn--outline:before { border-color: var(--vi-border-strong); }

/* Segmented controls. */
.q-btn-group { border-radius: 6px; overflow: hidden; box-shadow: none; border: 1px solid var(--vi-border-strong); }
.q-btn-group > .q-btn:not(.q-btn--round) { border-radius: 0; border: none; min-height: 26px; }
.q-btn-group > .q-btn + .q-btn { border-left: 1px solid var(--vi-border-strong); }
.q-btn-toggle > .q-btn:not(.bg-primary) { background: var(--vi-panel); color: var(--vi-text) !important; }

/* Fields: a compact box with its label above-inside, as in a settings pane. */
.q-field { font-size: 13px; }
.q-field--outlined .q-field__control { border-radius: 6px; background: var(--vi-panel); padding: 0 10px; }
.q-field--outlined .q-field__control:before { border-color: var(--vi-border-strong); }
.q-field--outlined:hover .q-field__control:before { border-color: var(--vi-muted); }
.q-field--outlined.q-field--focused .q-field__control:after {
  border-width: 1px; box-shadow: 0 0 0 3px var(--vi-focus); }
/* Auto-height fields (a select with chips) grow with their content. */
.q-field--dense:not(.q-field--auto-height) .q-field__control, .q-field--dense .q-field__marginal { height: 36px; }
.q-field--dense .q-field__native, .q-field--dense .q-field__input { min-height: 36px; }
.q-field--dense.q-field--auto-height .q-field__control { min-height: 36px; }
/* A labelled select puts its value under the label, level with an input's. */
.q-field--dense.q-field--auto-height.q-field--labeled .q-field__native { min-height: 22px; padding: 0 0 2px; }
.q-field--dense.q-field--float .q-field__label { transform: translateY(-40%) scale(.8); }
.q-field__label { color: var(--vi-muted); font-size: 13px; }
.q-field__bottom { padding-top: 3px; font-size: 11.5px; }
.q-textarea.q-field--dense .q-field__control { height: auto; }

/* macOS-style switches: the thumb inside the track. Quasar sizes them in em off a 40px font. */
.q-toggle .q-toggle__inner { font-size: 20px; width: 34px; min-width: 34px; height: 20px; padding: 0; margin: 0 4px; }
.q-toggle .q-toggle__track { position: absolute; inset: 0; height: 20px; border-radius: 10px; opacity: 1;
                             background: var(--vi-border-strong); }
.q-toggle .q-toggle__inner--truthy .q-toggle__track { background: var(--q-primary); }
.q-toggle .q-toggle__thumb { top: 2px; left: 2px; width: 16px; height: 16px; }
.q-toggle .q-toggle__inner--truthy .q-toggle__thumb { left: 16px; }
.q-toggle .q-toggle__thumb:after { box-shadow: 0 1px 2px rgba(0, 0, 0, .3); background: #fff !important; }
.q-toggle .q-toggle__thumb:before { display: none; }

.q-chip { border-radius: 5px !important; font-size: 12px; }
.q-tooltip { max-width: 22rem; font-size: 12px; line-height: 1.4; border-radius: 6px;
             background: rgba(40, 38, 36, .95); }  /* long hints wrap instead of spanning the page */
.q-notification { border-radius: 8px; }
.q-linear-progress { border-radius: 4px; }
.q-slider__track { border-radius: 2px; }
.q-slider__inner { background: var(--vi-border); opacity: 1; }
.q-slider__thumb-shape { transform: scale(1.35); filter: drop-shadow(0 1px 1.5px rgba(0, 0, 0, .35)); }
.q-slider__thumb-shape path { fill: #fff; stroke: rgba(0, 0, 0, .2); stroke-width: .5; }

/* Steps as a quiet tab row. */
.q-stepper { background: transparent; box-shadow: none; border: none; color: var(--vi-text); }
.q-stepper__header { border-bottom: 1px solid var(--vi-border); min-height: 0; }
.q-stepper__tab { padding: 8px 10px; min-height: 0; font-size: 13px; }
.q-stepper__dot { width: 20px; min-width: 20px; height: 20px; font-size: 12px; margin-right: 6px; }
.q-stepper__dot .q-icon { font-size: 12px; }
.q-stepper__dot:before, .q-stepper__dot:after, .q-stepper__line:before, .q-stepper__line:after { display: none; }
.q-stepper__title { font-size: 13px; line-height: 1.3; letter-spacing: normal; }
/* An inset shadow would also draw a hairline down the tab's right edge. */
.q-stepper__tab--active { background: linear-gradient(var(--q-primary), var(--q-primary)) bottom / 100% 2px no-repeat; }
.q-stepper__tab--active .q-stepper__title { font-weight: 600; }
.q-stepper__tab--done:not(.q-stepper__tab--active) .q-stepper__dot { background: var(--vi-muted) !important; }
.q-stepper__tab--done:not(.q-stepper__tab--active) { color: var(--vi-text) !important; }
.q-stepper__step-inner { padding: 16px 0 0; }
.q-stepper__nav { padding: 16px 0 0; }
"""

_applied = False


def apply() -> None:
    """Once per process: every input-like element is a compact outlined box."""
    global _applied
    if _applied:
        return
    for element in (ui.input, ui.number, ui.select, ui.textarea):
        element.default_props("outlined dense")
    _applied = True


def add_css() -> None:
    """Once per page."""
    ui.add_css(CSS)
