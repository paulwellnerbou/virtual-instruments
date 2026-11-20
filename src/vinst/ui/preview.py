"""The instrument as DecentSampler shows it, drawn as SVG for the builder: title, knobs in the
preset's own knob style at their starting values, and DecentSampler's keyboard with its tints.

Measured from DecentSampler 1.43 at its smallest window, which shows the interface down to y = 325:
the keyboard covers it from y = 215, 100 high, beside two wheels; it doesn't scale with the
interface, so an octave is 112 wide, and it starts at MIDI note 0 whatever the centerNote, with a
scroll arrow on the right only. DecentSampler names MIDI note 60 C3.
"""

import math
from html import escape

from vinst import preset
from vinst.spec import CONTROLS, Instrument

KB_Y, KB_H = 215, 100
VIEW_H = 325  # what DecentSampler's window shows of the interface
KEYS_X0, KEYS_X1 = 61, 790.5
WHITE_W = 112.3 / 7
BLACK_W, BLACK_H = WHITE_W * 0.62, KB_H * 0.7
WHITE_STEPS = [0, 2, 4, 5, 7, 9, 11]  # the white keys of an octave, in semitones from its C
FONT = "'Work Sans', 'Helvetica Neue', Arial, sans-serif"


def css(argb: str) -> str:
    try:
        a, rgb = int(argb[:2], 16) / 255, argb[2:]
        return f"rgba({int(rgb[0:2], 16)},{int(rgb[2:4], 16)},{int(rgb[4:6], 16)},{a:.2f})"
    except ValueError:
        return "transparent"


def knob_keys(inst: Instrument, cutting: bool) -> list[str]:
    """The controls the built instrument has a knob for (as preset.active_controls, without a build)."""
    p = inst.preset
    out = []
    for c in CONTROLS:
        if c == "lowcut":
            if cutting:
                out.append(c)
        elif c in p.controls:
            if c == "mix" and len(preset.mixable_signals(inst)) < 2:
                continue
            if c == "noise" and not any(x.use == "release" for x in inst.extras):
                continue
            if c == "tone" and "eq" in p.controls:
                continue
            if c == "eq" and not any(b.on for b in p.eq):
                continue
            out.append(c)
    return out


def _point(cx: float, cy: float, r: float, angle: float) -> tuple[float, float]:
    """On a circle, `angle` degrees clockwise from 12 o'clock, as knob styles measure it."""
    a = math.radians(angle)
    return cx + r * math.sin(a), cy - r * math.cos(a)


def _arc(cx: float, cy: float, r: float, start: float, end: float) -> str:
    (x0, y0), (x1, y1) = _point(cx, cy, r, start), _point(cx, cy, r, end)
    return f"M{x0:.2f},{y0:.2f} A{r},{r} 0 {1 if end - start > 180 else 0} 1 {x1:.2f},{y1:.2f}"


def knob(x: float, y: float, size: float, k: preset.Knob, accent: str, text: str) -> str:
    """A labelled knob as preset.knob_style draws it: a small label on top, the dial below it."""
    dial = size - 15.5
    cx, cy, u = x + size / 2, y + 11 + dial / 2, dial / 100  # u: the style's percent of the square
    share = (k.value - k.lo) / (k.hi - k.lo) if k.hi > k.lo else 0
    angle = -135 + 270 * min(max(share, 0), 1)
    px0, py0 = _point(cx, cy, 14 * u, angle)
    px1, py1 = _point(cx, cy, 30 * u, angle)
    lit = (f"<path d='{_arc(cx, cy, 46 * u, -135, angle)}' stroke='{accent}' stroke-width='{6 * u:.2f}' "
           f"fill='none' stroke-linecap='round' filter='url(#glow)'/>") if share > 0.005 else ""
    return (f"<text x='{cx:.1f}' y='{y + 9:.1f}' text-anchor='middle' font-size='7.5' fill='{text}' "
            f"opacity='.8'>{escape(k.label)}</text>"
            f"<path d='{_arc(cx, cy, 46 * u, -135, 135)}' stroke='rgba(255,255,255,.13)' "
            f"stroke-width='{6 * u:.2f}' fill='none' stroke-linecap='round'/>{lit}"
            f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='{34 * u:.2f}' fill='url(#cap)' filter='url(#shadow)'/>"
            f"<line x1='{px0:.2f}' y1='{py0:.2f}' x2='{px1:.2f}' y2='{py1:.2f}' stroke='{text}' "
            f"stroke-width='{5 * u:.2f}' stroke-linecap='round'/>")


def white_index(n: int) -> int:
    """How many white keys lie below MIDI note `n`."""
    return (n // 12) * 7 + sum(1 for s in WHITE_STEPS if s < n % 12)


def keyboard(tints: dict[int, str]) -> str:
    """DecentSampler's keyboard: its wheels, its keys from MIDI note 0 and its arrow, `tints` by key."""
    whites, blacks = [], []
    for n in range(128):
        if n % 12 in WHITE_STEPS:
            x = KEYS_X0 + white_index(n) * WHITE_W
            if x > KEYS_X1:
                break
            whites.append(f"<rect x='{x:.2f}' y='{KB_Y}' width='{WHITE_W:.2f}' height='{KB_H}' "
                          f"fill='{tints.get(n, '#fafafa')}' stroke='#9a9a9a' stroke-width='.7'/>")
            if n % 12 == 0:
                # Too narrow a key for the minus octaves' digit, DecentSampler cuts it off.
                name = "C-" if n < 24 else f"C{n // 12 - 2}"
                whites.append(f"<text x='{x + WHITE_W / 2:.2f}' y='{KB_Y + KB_H - 5}' text-anchor='middle' "
                              f"font-size='9' fill='#222'>{name}</text>")
        else:
            x = KEYS_X0 + white_index(n) * WHITE_W - BLACK_W / 2
            if x < KEYS_X1:
                blacks.append(f"<rect x='{x:.2f}' y='{KB_Y}' width='{BLACK_W:.2f}' height='{BLACK_H:.1f}' "
                              f"fill='url(#black)'/>")
    mid = KB_Y + KB_H / 2
    controls = (f"<rect x='10' y='{KB_Y}' width='19' height='{KB_H}' fill='#d9d9d9'/>"
                f"<rect x='10' y='{mid - 1.5}' width='19' height='1.5' fill='#828282'/>"
                f"<rect x='36' y='{KB_Y}' width='18.5' height='{KB_H}' fill='#d9d9d9'/>"
                f"<rect x='36' y='{KB_Y + KB_H * 0.55}' width='18.5' height='{KB_H * 0.45}' fill='#828282'/>"
                f"<rect x='{KEYS_X1}' y='{KB_Y}' width='11' height='{KB_H}' fill='#d9d9d9'/>"
                f"<text x='{KEYS_X1 + 5.5}' y='{mid + 3}' text-anchor='middle' font-size='9' fill='#828282'>▶</text>")
    return (f"{controls}<svg x='{KEYS_X0}' y='{KB_Y}' width='{KEYS_X1 - KEYS_X0}' height='{KB_H}' "
            f"viewBox='{KEYS_X0} {KB_Y} {KEYS_X1 - KEYS_X0} {KB_H}'>{''.join(whites)}{''.join(blacks)}</svg>")


def render(inst: Instrument, cutting: bool, background_url: str | None = None, max_width: int = 640,
           mapping: preset.Mapping | None = None) -> str:
    """The SVG; `cutting`: whether the build applies the low cut, so it has that knob. With `mapping`,
    the keyboard marks the keys that really play, as the built instrument does; without one (nothing
    sliced yet), the range the settings ask for."""
    p = inst.preset
    accent, text = css(p.accent_color), css(p.text_color)
    keys = knob_keys(inst, cutting)
    knobs = preset.knobs_for(inst, keys, preset.effects_for(inst, keys))
    x0, step, size = preset.knob_layout(len(knobs))
    parts = [f"<rect width='{preset.UI_W}' height='{VIEW_H}' fill='{css(p.bg_color)}'/>"]
    if background_url:
        parts.append(f"<image href='{escape(background_url)}' width='{preset.UI_W}' height='{preset.UI_H}' "
                     "preserveAspectRatio='none'/>")
    m = preset.MARGIN
    switched = preset.switched_passes(inst)
    # Clipped where DecentSampler's label ends, before the passes menu if there is one.
    title = (f"<text x='0' y='49' font-size='28' fill='{text}'>{escape(inst.meta.name)}</text>"
             + (f"<text x='0' y='68' font-size='13' fill='{text}' opacity='.6'>by {escape(inst.meta.author)}</text>"
                if inst.meta.author else ""))
    parts.append(f"<svg x='{m}' y='0' width='{preset.title_width(bool(switched))}' height='84' overflow='hidden'>"
                 f"{title}</svg>")
    parts.append(f"<line x1='{m}' y1='84.5' x2='{preset.UI_W - m}' y2='84.5' stroke='rgba(255,255,255,.12)'/>")
    if switched:
        menu_x = preset.UI_W - m - preset.MENU_W
        parts.append(f"<rect x='{menu_x}' y='26' width='{preset.MENU_W}' height='26' fill='none' "
                     f"stroke='{text}'/><text x='{menu_x + 6}' y='43' font-size='13' fill='{text}'>"
                     f"{escape(switched[0].name)} ▾</text>")
    parts += [knob(round(x0 + i * step), preset.KNOB_Y, size, k, accent, text) for i, k in enumerate(knobs)]
    if mapping is not None:
        span, extra_keys = mapping.span, list(mapping.fx_keys)
    else:
        wanted = inst.range.span()
        span = (max(0, wanted[0] - p.extend_below), min(127, wanted[1] + p.extend_above)) if wanted else None
        extra_keys = [k for x in inst.extras if x.use == "key"
                      for k in ([x.key, x.down_key, x.up_key] if x.stroke_keys else [x.key])]
    tints: dict[int, str] = {}
    if span:
        tints.update({n: css(preset.pale(p.accent_color, 0.4)) for n in range(span[0], span[1] + 1)})
    for key in extra_keys:
        tints[key] = css(preset.pale(f"FF{preset.EXTRA_KEY_RGB}", 0.6))
    parts.append(keyboard(tints))
    defs = ("<defs><linearGradient id='cap' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#353a42'/>"
            "<stop offset='1' stop-color='#1b1e23'/></linearGradient>"
            "<linearGradient id='black' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#858585'/>"
            "<stop offset='.87' stop-color='#858585'/><stop offset='.87' stop-color='#555'/></linearGradient>"
            "<filter id='shadow' x='-30%' y='-30%' width='160%' height='160%'>"
            "<feDropShadow dx='0' dy='2' stdDeviation='2' flood-color='#000' flood-opacity='.65'/></filter>"
            "<filter id='glow' x='-30%' y='-30%' width='160%' height='160%'>"
            "<feGaussianBlur stdDeviation='1.5'/><feMerge><feMergeNode/><feMergeNode in='SourceGraphic'/>"
            "</feMerge></filter></defs>")
    return (f"<svg viewBox='0 0 {preset.UI_W} {VIEW_H}' xmlns='http://www.w3.org/2000/svg' "
            f"style='width:100%;max-width:{max_width}px;height:auto;display:block;border-radius:8px;"
            f"font-family:{FONT}'>{defs}{''.join(parts)}</svg>")
