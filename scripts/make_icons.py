"""Draws the app icon and writes every size the launcher and the page use, into src/vinst/ui/assets.

Needs rsvg-convert (brew install librsvg) and macOS's iconutil. Run: uv run python scripts/make_icons.py
"""

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "src" / "vinst" / "ui" / "assets"
SIZE = 1024
INSET, BODY, RADIUS = 100, 824, 185  # macOS's icon grid: the body leaves room for its shadow


def wave() -> str:
    """A plucked note: a sharp attack, then a decaying sine."""
    x0, x1, mid, amp, cycles, n = 190, 834, 395, 165, 6.5, 400
    pts = []
    for i in range(n + 1):
        t = i / n
        env = min(t / 0.04, 1.0) * math.exp(-3.2 * max(t - 0.04, 0))
        pts.append(f"{x0 + t * (x1 - x0):.1f},{mid - amp * env * math.sin(2 * math.pi * cycles * t):.1f}")
    return "M" + " L".join(pts)


def keys() -> str:
    top, bottom, width, gap = 610, INSET + BODY, BODY / 7, 10
    white = "".join(
        f'<rect x="{INSET + i * width + gap / 2:.1f}" y="{top}" width="{width - gap:.1f}" height="{bottom - top + 40}" '
        f'rx="14" fill="{"url(#lit)" if i == 2 else "#f7f4ee"}"/>' for i in range(7))
    black_w = width * 0.56
    black = "".join(
        f'<rect x="{INSET + i * width - black_w / 2:.1f}" y="{top - 6}" width="{black_w:.1f}" '
        f'height="{(bottom - top) * 0.6:.1f}" rx="10" fill="#0e1a2e"/>' for i in (1, 2, 4, 5, 6))
    return white + black


def svg(shadow: bool) -> str:
    """With the shadow, the whole macOS grid; without, cropped to the body, for the favicon."""
    view = f"0 0 {SIZE} {SIZE}" if shadow else f"{INSET} {INSET} {BODY} {BODY}"
    body = f'<rect x="{INSET}" y="{INSET}" width="{BODY}" height="{BODY}" rx="{RADIUS}" fill="url(#bg)"/>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.35" y2="1">
      <stop offset="0" stop-color="#1f7cf0"/><stop offset="1" stop-color="#0b2f66"/>
    </linearGradient>
    <linearGradient id="lit" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffd27a"/><stop offset="1" stop-color="#ff9f2e"/>
    </linearGradient>
    <linearGradient id="wave" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#ffe2a3"/><stop offset="1" stop-color="#ffb547"/>
    </linearGradient>
    <clipPath id="body"><rect x="{INSET}" y="{INSET}" width="{BODY}" height="{BODY}" rx="{RADIUS}"/></clipPath>
    <filter id="shadow" x="-10%" y="-10%" width="120%" height="125%">
      <feDropShadow dx="0" dy="12" stdDeviation="14" flood-color="#000" flood-opacity="0.35"/>
    </filter>
  </defs>
  {f'<g filter="url(#shadow)">{body}</g>' if shadow else body}
  <g clip-path="url(#body)">
    <rect x="{INSET}" y="584" width="{BODY}" height="{BODY}" fill="#0a1a33" opacity="0.55"/>
    {keys()}
  </g>
  <path d="{wave()}" fill="none" stroke="url(#wave)" stroke-width="36" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
'''


def png(source: Path, size: int, target: Path) -> None:
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(source), "-o", str(target)], check=True)


def main() -> None:
    if shutil.which("rsvg-convert") is None:
        raise SystemExit("rsvg-convert not found: brew install librsvg")
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon = ASSETS / "icon.svg"
    icon.write_text(svg(shadow=True), encoding="utf-8")
    (ASSETS / "favicon.svg").write_text(svg(shadow=False), encoding="utf-8")
    for size in (192, 512):  # what Chrome wants for an installed page
        png(icon, size, ASSETS / f"icon-{size}.png")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            png(icon, size, iconset / f"icon_{size}x{size}.png")
            png(icon, size * 2, iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "icon.icns")], check=True)


if __name__ == "__main__":
    main()
