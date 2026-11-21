"""A desktop-app look over Quasar's Material defaults: system font, hairline borders instead of shadows,
compact controls, and colours that follow the system's light or dark mode."""

from nicegui import ui

from vinst.ui.controls import NumberField

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
  --vi-bar: #0a6ee6;
  color-scheme: light;
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
  --vi-bar: #1a4f8c;  /* the light bar's blue would glare on a dark page */
  --q-primary: #0a84ff !important;
  --q-positive: #30d158 !important;
  --q-negative: #ff453a !important;
  --q-warning: #ff9f0a !important;
}

/* Native widgets (scrollbars, pickers) follow. The page's own scrollbar takes it from the root element. */
html:has(> body.body--dark), body.body--dark { color-scheme: dark; }

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

.q-header.q-header {
  background: var(--vi-bar) !important;
  color: #fff;
  border-bottom: none;
  box-shadow: none;
}
.q-header { min-height: 44px; }

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
.q-item:not(.q-item--dense) { min-height: 32px; padding: 4px 12px; }
.q-item.q-hoverable:hover > .q-focus-helper { background: var(--vi-hover); opacity: 1; }
/* Menu rows highlight as rounded insets, as in a native menu. */
.q-menu .q-item > .q-focus-helper { top: 2px; bottom: 2px; left: 4px; right: 4px; width: auto; height: auto;
                                    border-radius: 5px; }

.q-btn { font-size: 13px; font-weight: 500; text-transform: none; letter-spacing: normal;
         transition: background-color .15s, border-color .15s, color .15s, opacity .15s; }
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
/* The uploader's own buttons are white icons on its coloured header. */
.q-uploader .q-btn--flat, .q-uploader .q-btn--flat:hover { background: transparent; border: none; }

.q-btn-group { border-radius: 6px; overflow: hidden; box-shadow: none; border: 1px solid var(--vi-border-strong); }
.q-btn-group > .q-btn:not(.q-btn--round) { border-radius: 0; border: none; min-height: 26px; }
.q-btn-group > .q-btn + .q-btn { border-left: 1px solid var(--vi-border-strong); }

/* Segmented control (controls.Segmented). */
.vi-segmented { position: relative; display: inline-flex; max-width: 100%; overflow: hidden; border-radius: 6px;
                border: 1px solid var(--vi-border-strong); background: var(--vi-panel); vertical-align: middle; }
.vi-segmented-thumb { position: absolute; top: 0; bottom: 0; background: var(--q-primary); }
.vi-segmented-ready .vi-segmented-thumb { transition: left .22s ease, width .22s ease; }
.vi-segment { position: relative; min-height: 26px; padding: 2px 12px; border: 0; background: transparent;
              color: var(--vi-text); font: inherit; font-weight: 500; cursor: default;  /* wraps only if too narrow */
              transition: color .22s ease, background-color .15s; }
.vi-segment + .vi-segment { border-left: 1px solid var(--vi-border-strong); }
.vi-segment:hover:not([aria-pressed="true"]):not(:disabled) { background: var(--vi-hover); }
.vi-segment[aria-pressed="true"] { color: #fff; }
.vi-segment:focus-visible { outline: 2px solid var(--vi-focus); outline-offset: -2px; }
.vi-segment:disabled { opacity: .5; }

/* Velocity splits (controls.VelocitySplits): the layers' bar, a knob on each boundary. The side margins
   keep the end knobs inside the column. */
.vi-splits { position: relative; height: 26px; margin: 10px 9px 0; }
.vi-splits-track { position: absolute; inset: 0; overflow: hidden; border-radius: 6px; }
.vi-splits-seg { position: absolute; top: 0; bottom: 0; display: flex; align-items: center; justify-content: center;
                 overflow: hidden; white-space: nowrap; color: #fff; font-size: 11.5px;
                 transition: left .2s ease, width .2s ease; }
.vi-splits-knob { position: absolute; top: 50%; width: 18px; height: 18px; margin: -9px 0 0 -9px; border-radius: 50%;
                  background: #fff; border: .5px solid rgba(0, 0, 0, .2); box-shadow: 0 1px 3px rgba(0, 0, 0, .35);
                  cursor: ew-resize; touch-action: none; transition: left .2s ease, transform .1s; }
.vi-splits-dragging .vi-splits-seg, .vi-splits-dragging .vi-splits-knob { transition: transform .1s; }
.vi-splits-knob:active { transform: scale(1.12); }
.vi-splits-knob:focus-visible { outline: none; box-shadow: 0 0 0 3px var(--vi-focus), 0 1px 3px rgba(0, 0, 0, .35); }
.vi-splits-value { position: absolute; bottom: 24px; left: 50%; transform: translateX(-50%); padding: 1px 6px;
                   border-radius: 4px; background: var(--vi-text); color: var(--vi-bg); font-size: 11px;
                   pointer-events: none; }

/* Controls on one line are as tall as a field beside them. */
.row:has(> .q-field) > .q-btn:not(.q-btn--round) { min-height: 36px; }
.row:has(> .q-field) > .q-btn-group > .q-btn:not(.q-btn--round),
.row:has(> .q-field) > .vi-segmented > .vi-segment { min-height: 34px; }  /* + their border */
.row:has(> .q-field) > .q-btn--round:not(.q-btn--dense) { min-width: 36px; min-height: 36px; }

/* Fields: a compact box with its label above-inside, as in a settings pane. */
.q-field { font-size: 13px; }
.q-field--outlined .q-field__control { border-radius: 6px; background: var(--vi-panel); padding: 0 10px; }
.q-field--outlined .q-field__control:before { border-color: var(--vi-border-strong); transition: border-color .15s; }
.q-field--outlined:hover .q-field__control:before { border-color: var(--vi-muted); }
.q-field--outlined .q-field__control:after { transition: border-color .2s, box-shadow .2s; }
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
/* Number field (controls.NumberField): its stepper instead of the browser's spin buttons. */
.q-field:has(.vi-stepper) input[type=number] { appearance: textfield; -moz-appearance: textfield; }
.q-field:has(.vi-stepper) input::-webkit-inner-spin-button,
.q-field:has(.vi-stepper) input::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
.q-field__append:has(> .vi-stepper) { padding-left: 6px; margin-right: -5px; }
.vi-stepper { display: flex; flex-direction: column; justify-content: center; gap: 1px; }
.vi-stepper button { display: flex; align-items: center; justify-content: center; width: 16px; height: 13px;
                     padding: 0; border: 0; border-radius: 3px; background: transparent; color: var(--vi-muted);
                     transition: background-color .15s, color .15s; }
.vi-stepper button:hover { background: var(--vi-hover); color: var(--vi-text); }
.vi-stepper button:active { background: var(--vi-border-strong); }
.q-field--disabled .vi-stepper, .q-field--readonly .vi-stepper { opacity: .4; pointer-events: none; }

/* macOS-style switches: the thumb inside the track. Quasar sizes them in em off a 40px font. */
.q-toggle .q-toggle__inner { font-size: 20px; width: 34px; min-width: 34px; height: 20px; padding: 0; margin: 0 4px; }
.q-toggle .q-toggle__track { position: absolute; inset: 0; height: 20px; border-radius: 10px; opacity: 1;
                             background: var(--vi-border-strong); transition: background-color .2s, box-shadow .2s; }
.q-toggle .q-toggle__inner--truthy .q-toggle__track { background: currentColor; }
/* Quasar only animates the thumb under an explicit dir attribute. */
.q-toggle .q-toggle__thumb { top: 2px; left: 2px; width: 16px; height: 16px; transition: left .2s ease; }
.q-toggle .q-toggle__inner--truthy .q-toggle__thumb { left: 16px; }
.q-toggle .q-toggle__thumb:after { box-shadow: 0 1px 2px rgba(0, 0, 0, .3); background: #fff !important; }
/* Quasar's focus halo; a ring around the track takes its place. */
.q-toggle .q-toggle__thumb:before { display: none; }
.q-toggle:focus-visible .q-toggle__track { box-shadow: 0 0 0 3px var(--vi-focus); }

.q-chip { border-radius: 5px !important; font-size: 12px; }
.q-tooltip { max-width: 22rem; font-size: 12px; line-height: 1.4; border-radius: 6px;
             background: rgba(40, 38, 36, .95); }  /* long hints wrap instead of spanning the page */
body.body--dark .q-tooltip { background: rgba(84, 81, 77, .97); }  /* stands out from the dark panels */
.q-notification { border-radius: 8px; }
.q-linear-progress { border-radius: 4px; }
.q-slider__track { border-radius: 2px; }
.q-slider__inner { background: var(--vi-border); opacity: 1; }
.q-slider__thumb-shape { transform: scale(1.35); filter: drop-shadow(0 1px 1.5px rgba(0, 0, 0, .35)); }
.q-slider__thumb-shape path { fill: #fff; stroke: rgba(0, 0, 0, .2); stroke-width: .5; }

.q-stepper { background: transparent; box-shadow: none; border: none; color: var(--vi-text); }
.q-stepper__header { border-bottom: 1px solid var(--vi-border); min-height: 0; }
.q-stepper__tab { padding: 8px 10px; min-height: 0; font-size: 13px; }
.q-stepper__dot { width: 20px; min-width: 20px; height: 20px; font-size: 12px; margin-right: 6px;
                  transition: background-color .2s; }
.q-stepper__dot .q-icon { font-size: 12px; }
.q-stepper__dot:before, .q-stepper__dot:after, .q-stepper__line:before, .q-stepper__line:after { display: none; }
.q-stepper__title { font-size: 13px; line-height: 1.3; letter-spacing: normal; }
/* The current step's underline grows from its middle. A background, as an inset shadow would also draw a
   hairline down the tab's right edge. */
.q-stepper__tab { background: linear-gradient(var(--q-primary), var(--q-primary)) center bottom / 0 2px no-repeat;
                  transition: background-size .25s ease, color .2s; }
.q-stepper__tab--active { background-size: 100% 2px; }
/* Hover and keyboard focus: rounded like the buttons, inset so it stays clear of the underline. */
.q-stepper__tab > .q-focus-helper { top: 3px; bottom: 5px; left: 2px; right: 2px; width: auto; height: auto;
                                    border-radius: 6px; }
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
    # NumberField took ui.number's defaults when it was defined, so it needs its own.
    for element in (ui.input, ui.number, NumberField, ui.select, ui.textarea):
        element.default_props("outlined dense")
    _applied = True


def add_css() -> None:
    """Once per page."""
    ui.add_css(CSS)
