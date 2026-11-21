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
.q-btn-toggle > .q-btn:not(.bg-primary) { background: var(--vi-panel); color: var(--vi-text) !important; }
/* One highlight slides to the chosen segment; CONTROLS_JS sets where. Without it, Quasar's own colours stay. */
@property --vi-seg-x { syntax: "<length>"; inherits: false; initial-value: 0px; }
@property --vi-seg-w { syntax: "<length>"; inherits: false; initial-value: 0px; }
.q-btn-toggle.vi-seg {
  background-color: var(--vi-panel);
  background-image: linear-gradient(var(--q-primary), var(--q-primary));
  background-repeat: no-repeat;
  background-position: var(--vi-seg-x) 0;
  background-size: var(--vi-seg-w) 100%;
}
.q-btn-toggle.vi-seg-anim { transition: --vi-seg-x .22s ease, --vi-seg-w .22s ease; }
.q-btn-toggle.vi-seg > .q-btn { background: transparent !important; transition: color .22s ease; }
.q-btn-toggle.vi-seg > .q-btn[aria-pressed="true"] { color: #fff !important; }

/* Controls on one line are as tall as a field beside them. */
.row:has(> .q-field) > .q-btn:not(.q-btn--round) { min-height: 36px; }
.row:has(> .q-field) > .q-btn-group > .q-btn:not(.q-btn--round) { min-height: 34px; }  /* + its border */
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
/* Number fields step with a quiet stepper (CONTROLS_JS) instead of the browser's spin buttons. */
.q-field input.vi-stepped { appearance: textfield; -moz-appearance: textfield; }
.q-field input.vi-stepped::-webkit-inner-spin-button,
.q-field input.vi-stepped::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
.vi-stepper { display: flex; flex-direction: column; justify-content: center; gap: 1px; align-self: center;
              margin: 0 -5px 0 6px; }
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

# Segmented controls: places the highlight under the pressed segment. It only moves (animated) when the
# choice changes; on first sight and on resizes it's set in place, so nothing slides in from the left.
# Number fields: adds the stepper. Its buttons aren't in the tab order (the arrow keys step), keep the
# field's focus, and repeat while held, as the native ones do.
CONTROLS_JS = """
(() => {
  const seen = new WeakMap();  // group -> index of its pressed segment
  const widths = new WeakMap();
  // Where each control's highlight last was, so a redraw that replaces the control (a refreshed section)
  // still slides from there. Keyed by its data-vi-key, else by its labels.
  const last = new Map();
  const stepped = new WeakSet();
  const pressedIndex = group => [...group.children].findIndex(b => b.getAttribute('aria-pressed') === 'true');
  const labels = group => [...group.children].map(b => b.textContent.trim()).join('|');
  function keyOf(group) {
    if (group.dataset.viKey) return group.dataset.viKey;
    const same = [...document.querySelectorAll('.q-btn-toggle')].filter(g => labels(g) === labels(group));
    return labels(group) + '#' + same.indexOf(group);
  }
  function show(group, x, w, animate) {
    group.classList.add('vi-seg');
    group.classList.toggle('vi-seg-anim', animate);
    group.style.setProperty('--vi-seg-x', x + 'px');
    group.style.setProperty('--vi-seg-w', w + 'px');
  }
  const SLIDE_MS = 220;  // as the CSS transition
  function place(group, animate, from) {
    if (!group.offsetWidth) return;
    const i = pressedIndex(group), on = group.children[i];
    const x = on ? on.offsetLeft : 0, w = on ? on.offsetWidth : 0;
    const key = keyOf(group), prev = from || last.get(key) || {x, w};
    last.set(key, {i, x, w, t: animate ? performance.now() : 0, fromX: prev.x, fromW: prev.w});
    show(group, x, w, animate);
  }
  function now(at) {  // where a highlight is at this moment, also partway through its slide
    const p = at.t ? Math.min(1, (performance.now() - at.t) / SLIDE_MS) : 1;
    return {x: at.fromX + (at.x - at.fromX) * p, w: at.fromW + (at.w - at.fromW) * p};
  }
  // Only real resizes re-place it: the first report comes right after observing, and would cut short
  // a slide that has just started.
  const sized = new ResizeObserver(entries => entries.forEach(e => {
    if (!e.target.isConnected) { sized.unobserve(e.target); return; }  // replaced by a redraw
    const w = e.contentRect.width, known = widths.has(e.target);
    if (known && widths.get(e.target) === w) return;
    widths.set(e.target, w);
    if (known) place(e.target, false);
  }));
  const chevron = d => '<svg viewBox="0 0 10 6" width="9" height="6" aria-hidden="true"><path d="' + d +
    '" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  function step(input, dir) {
    if (input.disabled || input.readOnly) return;
    try { dir > 0 ? input.stepUp() : input.stepDown(); } catch (e) { return; }  // step="any" can't step
    input.dispatchEvent(new Event('input', {bubbles: true}));
  }
  function addStepper(input) {
    stepped.add(input);
    const control = input.closest('.q-field__control');
    if (!control) return;
    const box = document.createElement('div');
    box.className = 'vi-stepper';
    box.innerHTML =
      '<button type="button" tabindex="-1" aria-label="Increase" data-dir="1">' + chevron('M1 5l4-4 4 4') + '</button>' +
      '<button type="button" tabindex="-1" aria-label="Decrease" data-dir="-1">' + chevron('M1 1l4 4 4-4') + '</button>';
    // While held, each step starts from the last one: an older value the server echoes back in
    // between would otherwise set it back.
    let timer, held = null;
    const stop = () => { clearTimeout(timer); held = null; };
    const stepFrom = dir => {
      if (held !== null) input.value = held;
      step(input, dir);
      held = input.value;
    };
    box.addEventListener('pointerdown', e => {
      const button = e.target.closest('button');
      if (!button || e.button !== 0) return;
      e.preventDefault();
      const dir = Number(button.dataset.dir);
      stepFrom(dir);
      const repeat = delay => { timer = setTimeout(() => { stepFrom(dir); repeat(70); }, delay); };
      repeat(400);
    });
    box.querySelectorAll('button').forEach(b => b.addEventListener('pointerleave', stop));
    ['pointerup', 'pointercancel'].forEach(type => box.addEventListener(type, stop));
    window.addEventListener('blur', stop);
    control.appendChild(box);
    input.classList.add('vi-stepped');
  }
  function scan() {
    document.querySelectorAll('.q-field input[type=number]').forEach(input => {
      if (!stepped.has(input)) addStepper(input);
    });
    document.querySelectorAll('.q-btn-toggle').forEach(group => {
      const on = pressedIndex(group);
      const before = seen.get(group);
      seen.set(group, on);
      if (before === undefined) {
        sized.observe(group);
        const was = last.get(keyOf(group));
        const target = group.children[on];
        if (was && target && group.offsetWidth) {
          // The replaced control's click usually started the slide already: carry on from where it is.
          const at = now(was);
          if (Math.abs(at.x - target.offsetLeft) > 0.5 || Math.abs(at.w - target.offsetWidth) > 0.5) {
            show(group, at.x, at.w, false);
            requestAnimationFrame(() => place(group, true, at));
            return;
          }
        }
      }
      if (before !== on) place(group, before !== undefined);
    });
  }
  const WATCHED = '.q-btn-toggle, .q-field input[type=number]';
  const matters = m => m.type === 'attributes' || [...m.addedNodes].some(n =>
    n.nodeType === 1 && (n.matches(WATCHED) || n.querySelector(WATCHED)));
  let queued = false;
  new MutationObserver(mutations => {
    if (queued || !mutations.some(matters)) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; scan(); });
  }).observe(document.body, {subtree: true, childList: true, attributes: true, attributeFilter: ['aria-pressed']});
  scan();
})();
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


def add_assets() -> None:
    """Once per page."""
    ui.add_css(CSS)
    ui.add_body_html(f"<script>{CONTROLS_JS}</script>")
