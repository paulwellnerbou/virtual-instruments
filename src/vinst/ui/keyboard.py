"""SVG keyboard showing which notes get sampled and on which string."""

from html import escape

from vinst import notes
from vinst.plan import SampledNote

PALETTE = ["#2a9d8f", "#e76f51", "#457b9d", "#e9c46a", "#8e6cbd", "#f4a261", "#43aa8b", "#d1495b"]
SINGLE = "#2a9d8f"
WHITE_W, WHITE_H, BLACK_W, BLACK_H = 14, 64, 9, 40


def string_colors(names: list[str]) -> dict[str, str]:
    return {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(names)}


def render(sampled: list[SampledNote], span: tuple[int, int] | None, string_names: list[str]) -> str:
    if span is None:
        return "<div style='opacity:.6'>No range yet.</div>"
    colors = string_colors(string_names)
    lo = max(0, span[0] - span[0] % 12)
    hi = min(127, span[1] + (11 - span[1] % 12))
    by_midi = {n.midi: n for n in sampled}

    def fill(midi: int, black: bool) -> str:
        n = by_midi.get(midi)
        if n is not None:
            return colors.get(n.string, SINGLE) if n.string else SINGLE
        if span[0] <= midi <= span[1]:
            return "#9aa0a6" if black else "#e3e6ea"
        return "#222" if black else "#fff"

    whites, blacks, labels = [], [], []
    x = 0
    for midi in range(lo, hi + 1):
        if notes.is_black(midi):
            continue
        title = escape(_title(midi, by_midi.get(midi)))
        whites.append(f"<rect x='{x}' y='0' width='{WHITE_W}' height='{WHITE_H}' fill='{fill(midi, False)}' "
                      f"stroke='#888' stroke-width='0.6'><title>{title}</title></rect>")
        if midi % 12 == 0:
            labels.append(f"<text x='{x + 2}' y='{WHITE_H + 11}' font-size='9' fill='currentColor'>"
                          f"{notes.name(midi)}</text>")
        if midi + 1 <= hi and notes.is_black(midi + 1):
            bx = x + WHITE_W - BLACK_W / 2
            btitle = escape(_title(midi + 1, by_midi.get(midi + 1)))
            blacks.append(f"<rect x='{bx}' y='0' width='{BLACK_W}' height='{BLACK_H}' "
                          f"fill='{fill(midi + 1, True)}' stroke='#111' stroke-width='0.8'>"
                          f"<title>{btitle}</title></rect>")
            if midi + 1 in by_midi:  # darken so sampled black keys stay distinguishable from white ones
                blacks.append(f"<rect x='{bx}' y='0' width='{BLACK_W}' height='{BLACK_H}' fill='#000' "
                              f"fill-opacity='0.35' pointer-events='none'/>")
        x += WHITE_W
    width = x
    legend = ""
    if string_names:
        legend = "<div style='display:flex;gap:10px;flex-wrap:wrap;margin-top:4px;font-size:12px'>" + "".join(
            f"<span><span style='display:inline-block;width:10px;height:10px;border-radius:2px;"
            f"background:{c};margin-right:4px'></span>{escape(n)}</span>" for n, c in colors.items()
        ) + "</div>"
    return (f"<svg viewBox='0 0 {width} {WHITE_H + 14}' width='100%' style='max-width:{width * 1.6}px' "
            f"xmlns='http://www.w3.org/2000/svg'>{''.join(whites)}{''.join(blacks)}{''.join(labels)}</svg>{legend}")


def _title(midi: int, n: SampledNote | None) -> str:
    if n is None:
        return notes.label(midi)
    if n.string:
        return f"{notes.label(midi)} · {n.string} string, fret {n.fret}"
    if n.sound:
        return f"{notes.label(midi)} · {n.sound}"
    return f"{notes.label(midi)} · sampled"
