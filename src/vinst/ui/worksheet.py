"""Wizard step 5, the worksheet: every note with its velocity lanes, recorded, sliced and reviewed.

Each lane is its own take. Recording one in the app slices it right away; takes exported from a DAW
are picked up with "Slice all". Hits can be dragged between a note's lanes, and a note can get its
own lanes and velocity ranges instead of the defaults from Dynamics.
"""

import asyncio
import io
import itertools
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import numpy as np
import soundfile as sf
from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from nicegui import app, run, ui

from vinst import cleanup_eq, loops, relocate, review, slicing, store, takes_meta
from vinst.bundle import CLEANUP_MESSAGE, FINDER_MESSAGE, STALE_MESSAGE, changed_message, eq_message
from vinst.plan import (
    Take,
    lane_bounds,
    more_base,
    more_file,
    note_lanes,
    plan_takes,
    recording_passes,
    slicing_setup,
    take_key,
)
from vinst.slicing import SUBTYPES
from vinst.review import FLAGS, Hit, Manifest, TakeResult, check_lanes, lane_of, settle
from vinst.spec import SAFE_NAME as SAFE_LANE, CleanupEq, EqFilter, NoteLanes
from vinst.ui import form, open_with, recording
from vinst.ui.anim import Collapse
from vinst.ui.finder import can_choose_folder, choose_folder, ensure_folder, reveal

if TYPE_CHECKING:
    from vinst.ui.app import Editor

COLORS = {"error": "#d1495b", "warning": "#e9a23b", "info": "#4a90c2", None: "#2a9d8f", "rejected": "#9aa0a6"}
STATUS = {
    "ok": ("OK", "positive"),
    "missing": ("Not recorded", "grey"),
    "count_mismatch": ("Hit count", "negative"),
    "error": ("Error", "negative"),
    "noise_floor": ("Noise floor", "info"),
}


@app.get("/audio/{slug}/{name}")
def audio_file(slug: str, name: str) -> FileResponse:
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(404)
    folder = review.samples_dir(store.load(slug))
    path = folder / name if folder else None
    if path is None or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/wav")


@app.get("/loop-preview/{slug}/{name}")
def loop_preview_file(slug: str, name: str, start: int, end: int) -> Response:
    """The sample with its loop played a few times, so loops can be judged in the browser."""
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(404)
    inst = store.load(slug)
    folder = review.samples_dir(inst)
    path = folder / name if folder else None
    if path is None or not path.is_file():
        raise HTTPException(404)
    audio, sr = sf.read(path, dtype="float64", always_2d=True)
    audio = audio.mean(axis=1)
    if not 0 <= start < end < len(audio):
        raise HTTPException(400)
    crossfade = int(inst.processing.loop_crossfade_ms / 1000 * sr)
    out = loops.loop_preview(audio, loops.Loop(start, end, 1.0, ""), sr, crossfade=crossfade)
    buf = io.BytesIO()
    sf.write(buf, out, sr, format="WAV", subtype="PCM_16")
    return Response(buf.getvalue(), media_type="audio/wav")


COMPARE_EVERY_S = 0.75
COMPARE_FADE_S = 0.03


@app.get("/compare/{slug}")
def compare_file(slug: str, names: list[str] = Query(), every: float = COMPARE_EVERY_S) -> Response:
    """Several samples with their attacks `every` seconds apart, to hear how alike they are.

    The attack matters most, so each sample is cut short (with a quick fade) when the next one
    starts; the last one rings out.
    """
    folder = review.samples_dir(store.load(slug))
    if folder is None or any("/" in n or "\\" in n or n.startswith(".") for n in names):
        raise HTTPException(404)
    sounds, rate = [], None
    for name in names:
        path = folder / name
        if not path.is_file():
            raise HTTPException(404)
        audio, sr = sf.read(path, dtype="float64", always_2d=True)
        if rate not in (None, sr):
            continue  # imported files can differ in rate; joining them would change their pitch
        rate = sr
        sounds.append(audio.mean(axis=1))
    if not sounds or rate is None:
        raise HTTPException(404)
    step = int(min(max(every, 0.1), 3.0) * rate)
    fade = np.linspace(1.0, 0.0, int(COMPARE_FADE_S * rate))
    parts = []
    for sound in sounds[:-1]:
        part = np.zeros(step)
        part[: min(step, len(sound))] = sound[:step]
        part[-len(fade):] *= fade
        parts.append(part)
    parts.append(sounds[-1])
    buf = io.BytesIO()
    sf.write(buf, np.concatenate(parts), rate, format="WAV", subtype="PCM_24")
    return Response(buf.getvalue(), media_type="audio/wav")


@app.post("/drop/{slug}/{key}")
async def drop_file(slug: str, key: str, request: Request, name: str = "") -> dict:
    """An audio file dropped onto a take's row, e.g. exported from a DAW.

    It becomes the take, or if the take is already recorded, more variations of it; nothing
    recorded is overwritten. Several signals of a take are told apart by their ids in the file name.
    """
    # Only the worksheet sends this header. Setting it makes the browser ask first (CORS preflight),
    # which this server never allows, so other web pages can't write into the takes folder.
    if request.headers.get(DROP_HEADER) != "1":
        raise HTTPException(403)
    inst = store.load(slug)
    takes_dir = Path(inst.recording.takes_dir).expanduser()
    take = next((t for t in plan_takes(inst) if t.key == key), None)
    if take is None or not inst.recording.takes_dir:
        raise HTTPException(404)
    stem = Path(name).stem.lower()
    signal = take.signals[0] if len(take.signals) == 1 else next(
        (s for s in take.signals if re.search(rf"(^|[^a-z0-9]){re.escape(s.lower())}([^a-z0-9]|$)", stem)), None)
    if signal is None:
        return {"ok": False, "message": f"Which signal is “{name}”? Put one of {', '.join(take.signals)} in its name."}
    try:
        audio, sr = sf.read(io.BytesIO(await request.body()), always_2d=True)
    except Exception:
        return {"ok": False, "message": f"“{name}” isn't an audio file this app can read (WAV, AIFF, FLAC)."}
    index = take.signals.index(signal)
    target = takes_dir / take.files[index]
    result_key = take.key
    if target.exists():
        if take.kind != "note":
            return {"ok": False, "message": f"{take.title} is already recorded; record it again with ●."}
        from vinst.slicing import more_numbers

        # The other signals of the same "more" recording share its number.
        numbers = more_numbers(take, takes_dir)
        open_ = [n for n in numbers if not (takes_dir / more_file(take, n, signal)).exists()]
        number = open_[0] if open_ else max(numbers, default=0) + 1
        target = takes_dir / more_file(take, number, signal)
        result_key = take_key(more_base(take, number), take.pass_id)
    try:
        ensure_folder(takes_dir)
    except OSError as e:
        return {"ok": False, "message": str(e)}
    part = target.with_name(target.name + ".part")
    sf.write(part, audio, sr, subtype=SUBTYPES.get(inst.recording.bit_depth, "PCM_24"), format="WAV")
    part.replace(target)
    note = "" if sr == inst.recording.sample_rate else \
        f" It runs at {sr} Hz, the instrument at {inst.recording.sample_rate} Hz."
    return {"ok": True, "key": result_key, "message": f"“{name}” is now {target.name}.{note}"}


# One player for the whole page: a second click on the same button stops it.
PLAY_JS = """(url) => {
  const a = window.viPlayer || (window.viPlayer = new Audio());
  if (!a.paused && a.dataset.url === url) { a.pause(); return; }
  a.src = url; a.dataset.url = url; a.play();
}"""


def plain_title(take: TakeResult) -> str:
    """Note and velocity; which of its recordings a hit is from is in the hit's file name."""
    return re.sub(r" · more \(\d+\)", "", take.title)


def hit_color(h: Hit) -> str:
    return COLORS["rejected"] if h.rejected else COLORS[h.severity()]


_svg_ids = itertools.count()


def waveform(take: TakeResult, highlight: str | set[str] | None = None, height: int = 60) -> str:
    """The take's waveform, dimmed; the highlighted hits (all if None) that aren't rejected are drawn
    bright, so what isn't used here recedes. A take without hits is drawn bright, to find one in."""
    env = take.envelope
    if not env:
        return ""
    w, h = 800, height
    n = len(env)
    top = " ".join(f"{i * w / (n - 1):.1f},{h / 2 - v * h / 2:.1f}" for i, v in enumerate(env))
    bottom = " ".join(f"{i * w / (n - 1):.1f},{h / 2 + v * h / 2:.1f}" for i, v in reversed(list(enumerate(env))))
    clip_id = f"vi-wave-{next(_svg_ids)}"
    marks, bright = [], []
    if take.length:
        for hit in take.hits:
            if not (highlight is None or hit.id == highlight or (isinstance(highlight, set) and hit.id in highlight)):
                continue
            x0, x1 = hit.start / take.length * w, hit.end / take.length * w
            c = hit_color(hit)
            marks.append(f"<rect x='{x0:.1f}' y='0' width='{max(1, x1 - x0):.1f}' height='{h}' fill='{c}' "
                         f"fill-opacity='0.12'/>"
                         f"<line x1='{x0:.1f}' x2='{x0:.1f}' y1='0' y2='{h}' stroke='{c}' stroke-width='1.5'/>")
            if not hit.rejected:
                bright.append(f"<rect x='{x0:.1f}' y='0' width='{max(1, x1 - x0):.1f}' height='{h}'/>")
    polygon = f"<polygon points='{top} {bottom}' fill='currentColor'"
    return (f"<svg viewBox='0 0 {w} {h}' width='100%' height='{h}' preserveAspectRatio='none' "
            f"xmlns='http://www.w3.org/2000/svg'><defs><clipPath id='{clip_id}'>{''.join(bright)}</clipPath></defs>"
            f"{''.join(marks)}{polygon} fill-opacity='{0.15 if take.hits else 0.6}'/>"
            f"{polygon} fill-opacity='0.6' clip-path='url(#{clip_id})'/></svg>")


def hit_peak_db(hit: Hit) -> float:
    return max(hit.peak_db.values(), default=-120.0)


def hit_wave(take: TakeResult, hit: Hit, height: int, loudest_db: float) -> str:
    """Just this hit's stretch of its take's waveform. Takes are normalised each on their own, so the
    hit is scaled to its peak against `loudest_db`: across takes, a hit 6 dB softer is half as tall."""
    env, length = take.envelope, take.length or 0
    if not env or not length:
        return ""
    n = len(env)
    # The envelope's buckets wholly within this hit: the one it ends in can hold the next sound.
    first = min(n - 1, int(hit.start / length * n))
    part = env[first:max(first + 1, int(hit.end / length * n))] or [0.0, 0.0]
    if len(part) == 1:
        part = part * 2
    top_of = max(part)
    if top_of > 0:
        target = min(1.0, 10 ** ((hit_peak_db(hit) - loudest_db) / 20))
        part = [v / top_of * target for v in part]
    w, h = 400, height
    top = " ".join(f"{i * w / (len(part) - 1):.1f},{h / 2 - v * h / 2:.1f}" for i, v in enumerate(part))
    bottom = " ".join(f"{i * w / (len(part) - 1):.1f},{h / 2 + v * h / 2:.1f}"
                      for i, v in reversed(list(enumerate(part))))
    c = hit_color(hit)
    return (f"<svg viewBox='0 0 {w} {h}' width='100%' height='{h}' preserveAspectRatio='none' "
            f"xmlns='http://www.w3.org/2000/svg'><rect width='{w}' height='{h}' fill='{c}' fill-opacity='0.12'/>"
            f"<line x1='0' x2='0' y1='0' y2='{h}' stroke='{c}' stroke-width='3'/>"
            f"<polygon points='{top} {bottom}' fill='currentColor' "
            f"fill-opacity='{0.2 if hit.rejected else 0.6}'/></svg>")


CSS = """
.vi-vel { position: relative; width: 14px; margin: 4px 30px 4px 4px; border-radius: 7px; background: #eee; }
/* Clips the segments to the track's rounded ends; the dots sit outside it, so they aren't cut. */
.vi-vel-segs { position: absolute; inset: 0; border-radius: 7px; overflow: hidden; }
.vi-vel-seg { position: absolute; left: 0; right: 0; transition: top .15s, height .15s; }
.vi-vel-dot { position: absolute; left: 50%; width: 18px; height: 18px; margin: -9px 0 0 -9px; border-radius: 50%;
              background: white; border: 2px solid #1565c0; cursor: ns-resize; touch-action: none;
              box-shadow: 0 1px 3px rgba(0,0,0,.25); transition: transform .1s; }
.vi-vel-dot.active { transform: scale(1.2); }
.vi-vel-dot span { position: absolute; left: 22px; top: -1px; font-size: 11px; opacity: .75; pointer-events: none; }
.vi-lane { border-radius: 6px; padding: 2px 4px; transition: background-color .15s; }
.vi-hits { display: flex; flex-wrap: nowrap; gap: 6px; width: 100%; }
/* As wide as the hit is long; when a row runs out of room, all shrink alike and stay to scale. */
.vi-hit { display: flex; flex-direction: column; align-items: center; min-width: 14px; cursor: grab;
          border-radius: 4px; overflow: hidden; transition: background-color .12s; }
.vi-hit:hover { background: rgba(25, 118, 210, .14); }
.vi-hit > div:first-child { width: 100%; }
/* On the waveform's corner: this hit's samples went through a clean-up EQ. */
.vi-hit-eq { position: absolute; top: 1px; right: 1px; font-size: 12px; opacity: .75; pointer-events: none; }
.vi-hit-label { font-size: 11px; font-weight: 600; line-height: 15px; white-space: nowrap; }
.vi-hit-level { font-size: 10px; line-height: 13px; white-space: nowrap; opacity: .6; }
.vi-hit-empty { height: 36px; padding: 0 8px; display: flex; align-items: center; font-size: 11px; opacity: .7;
                border: 1px dashed currentColor; border-radius: 4px; cursor: pointer; white-space: nowrap; }
.vi-hit-empty:hover { background: rgba(25, 118, 210, .14); }
.q-dialog__inner--right .vi-settings { height: 100vh; max-height: 100vh !important; overflow-y: auto;
                                        border-radius: 16px 0 0 16px !important; }
.vi-lane.vi-drop { background: rgba(25, 118, 210, .12); outline: 2px dashed #1976d2; }
.vi-chip-drag { cursor: grab; }
.vi-cut { position: relative; width: 100%; user-select: none; }
/* As tall as the dialog's waveform (60 px), not the times below it. */
.vi-cut-range { position: absolute; top: 0; height: 60px; background: rgba(25, 118, 210, .10);
                box-shadow: inset 0 0 0 1px rgba(25, 118, 210, .5); pointer-events: none; }
.vi-cut-handle { position: absolute; top: -4px; height: 68px; width: 14px; margin-left: -7px; cursor: ew-resize;
                 touch-action: none; display: flex; justify-content: center; }
.vi-cut-handle::before { content: ""; width: 3px; border-radius: 2px; background: #1565c0; }
.vi-cut-handle:hover::before, .vi-cut-handle.active::before { width: 5px; }
"""
DRAG_JS = "(e) => e.dataTransfer.setData('text/plain', {ref})"
DRAG_OVER_JS = "(e) => { e.preventDefault(); e.currentTarget.classList.add('vi-drop'); }"
DRAG_LEAVE_JS = "(e) => e.currentTarget.classList.remove('vi-drop')"
DROP_HEADER = "X-Vinst-Drop"
# A hit chip carries its reference as text; files dropped from the Finder are uploaded first, then
# the server's answers are sent on. URL is replaced per row.
DROP_JS = ("(e) => { e.preventDefault(); e.currentTarget.classList.remove('vi-drop'); "
           "const files = [...(e.dataTransfer.files || [])]; "
           "if (!files.length) { emit(e.dataTransfer.getData('text/plain')); return; } "
           "Promise.all(files.map(f => fetch(URL + '?name=' + encodeURIComponent(f.name), "
           "{method: 'POST', body: f, headers: {'X-Vinst-Drop': '1'}})"
           ".then(r => r.json()).catch(err => ({ok: false, message: String(err)})))).then(rs => emit(rs)); }")
SUGGESTED_LANES = ["pp", "p", "mp", "mf", "f", "ff"]
VELOCITY_COLOURS = ["#90caf9", "#42a5f5", "#1e88e5", "#1565c0", "#0d47a1"]
WAVE_H = 36  # px; a row's buttons sit in a strip this tall, so they line up with the middle of its wave
ROW_H = 80  # px per velocity row (a wave lane and its labels); the velocity slider spans the rows

# Dragging a velocity dot: values follow the pointer between the neighbouring dots; on release the
# bar sends all starts to the server ("vel-change").
JS = r"""
window.viVel || (() => {
  let drag = null;
  const at = (track, y) => { const r = track.getBoundingClientRect();
    return Math.round(1 + Math.min(1, Math.max(0, (y - r.top) / r.height)) * 127); };
  const place = (track, dots) => {
    const starts = [1, ...dots.map(d => +d.dataset.value), 128];
    track.querySelectorAll('.vi-vel-seg').forEach((s, k) => {
      s.style.top = ((starts[k] - 1) / 127 * 100) + '%';
      s.style.height = ((starts[k + 1] - starts[k]) / 127 * 100) + '%'; });
  };
  document.addEventListener('pointerdown', e => {
    const dot = e.target.closest('.vi-vel-dot'); if (!dot) return;
    const track = dot.closest('.vi-vel'), dots = [...track.querySelectorAll('.vi-vel-dot')];
    drag = {dot, track, dots, i: dots.indexOf(dot), before: dots.map(d => d.dataset.value).join()};
    dot.classList.add('active'); try { dot.setPointerCapture(e.pointerId); } catch (_) {} e.preventDefault();
  });
  document.addEventListener('pointermove', e => {
    if (!drag) return;
    const {dot, track, dots, i} = drag;
    const lo = i > 0 ? +dots[i - 1].dataset.value + 1 : 2;
    const hi = i < dots.length - 1 ? +dots[i + 1].dataset.value - 1 : 127;
    const v = Math.min(hi, Math.max(lo, at(track, e.clientY)));
    dot.dataset.value = v; dot.style.top = ((v - 1) / 127 * 100) + '%'; dot.querySelector('span').textContent = v;
    place(track, dots);
  });
  document.addEventListener('pointerup', () => {
    if (!drag) return;
    const {dot, track, dots, before} = drag; drag = null; dot.classList.remove('active');
    if (dots.map(d => d.dataset.value).join() === before) return;  // a click, not a move
    track.dispatchEvent(new CustomEvent('vel-change', {detail: dots.map(d => +d.dataset.value)}));
  });
  window.viVel = true;
})();

// Dragging a cut's start or end in the hit dialog: it stays between its neighbours and on its side
// of the other edge; on release the box sends both as fractions of the recording ("cut-change").
window.viCut || (() => {
  let drag = null;
  const show = (box, start, end) => {
    box.querySelector('[data-edge=start]').style.left = start * 100 + '%';
    box.querySelector('[data-edge=end]').style.left = end * 100 + '%';
    const range = box.querySelector('.vi-cut-range');
    range.style.left = start * 100 + '%'; range.style.width = (end - start) * 100 + '%';
    const len = +box.dataset.seconds;
    box.querySelector('.vi-cut-times').textContent =
      (start * len).toFixed(2) + ' – ' + (end * len).toFixed(2) + ' s (' + ((end - start) * len).toFixed(2) + ' s)';
  };
  document.addEventListener('pointerdown', e => {
    const handle = e.target.closest('.vi-cut-handle'); if (!handle) return;
    const box = handle.closest('.vi-cut');
    drag = {handle, box, edge: handle.dataset.edge, start: +box.dataset.start, end: +box.dataset.end};
    drag.before = drag.start + ':' + drag.end;
    handle.classList.add('active'); try { handle.setPointerCapture(e.pointerId); } catch (_) {} e.preventDefault();
  });
  document.addEventListener('pointermove', e => {
    if (!drag) return;
    const {box, edge} = drag, r = box.getBoundingClientRect(), gap = 0.05 / +box.dataset.seconds;
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    if (edge === 'start') drag.start = Math.min(drag.end - gap, Math.max(+box.dataset.lo, x));
    else drag.end = Math.max(drag.start + gap, Math.min(+box.dataset.hi, x));
    show(box, drag.start, drag.end);
  });
  document.addEventListener('pointerup', () => {
    if (!drag) return;
    const {handle, box, start, end, before} = drag; drag = null; handle.classList.remove('active');
    if (start + ':' + end === before) return;
    box.dataset.start = start; box.dataset.end = end;
    box.dispatchEvent(new CustomEvent('cut-change', {detail: {start, end}}));
  });
  window.viCut = true;
})();
"""


def settings_cards(ed: "Editor") -> None:
    """The worksheet's settings for the right column: what comes in (recording, or the folder to
    import) and what goes out (the samples), each in its own card, apart from the notes."""
    inst, rec, proc = ed.inst, ed.inst.recording, ed.inst.processing
    importing = rec.source == "samples"

    def folder_controls(current: Path | None, choose_prompt: str, on_pick: Any, finder: bool = True) -> None:
        """A folder's name (the full path on hover; click it to type or paste one), with Choose and
        Show in Finder."""
        name = ui.label(current.name if current else "–") \
            .classes("text-sm font-mono truncate grow min-w-0 cursor-text") \
            .tooltip(f"{current or 'No folder yet'} · click to type or paste a path")
        with name, ui.menu().props("anchor='bottom left' self='top left'") as menu, \
                ui.column().classes("p-2 w-[28rem] max-w-[90vw]"):
            def typed(value: str) -> None:
                folder = Path(value.strip()).expanduser()
                if not value.strip():
                    return
                if not folder.is_absolute():  # it would depend on where the builder was started
                    ui.notify("Enter the folder's full path, e.g. ~/Music/Takes.", type="warning")
                    return
                menu.close()
                if folder != current:
                    on_pick(str(folder))

            field = ui.input("Folder", value=str(current or "")).props("dense outlined autofocus") \
                .classes("w-full font-mono")
            field.on("keydown.enter", lambda: typed(field.value))
            with ui.row().classes("w-full justify-end gap-1"):
                ui.button("Cancel", on_click=menu.close).props("flat dense no-caps")
                ui.button("Use", on_click=lambda: typed(field.value)).props("flat dense no-caps color=primary")
        if can_choose_folder():
            async def pick() -> None:
                picked = await run.io_bound(choose_folder, choose_prompt, str(current or ""))
                if picked:
                    on_pick(picked)

            ui.button(icon="folder", on_click=pick).props("flat dense round").tooltip("Choose another folder")
        if finder and current is not None:
            ui.button(icon="folder_open", on_click=lambda: show(current)).props("flat dense round") \
                .tooltip("Show in Finder")

    def show(folder: Path) -> None:
        try:
            ensure_folder(folder)
        except OSError as e:
            ui.notify(str(e), type="warning")
            return
        reveal(folder)

    with ui.card().classes("w-full gap-1"):
        ui.label("Import from" if importing else "Recording").classes("text-base font-medium mb-1")

        @ui.refreshable
        def source() -> None:
            if importing:
                with form.setting("Folder", "The folder of already-cut samples; it is only read."):
                    ed.text("", rec, "import_dir", placeholder="/path/to/samples").props("dense outlined") \
                        .classes("grow min-w-0")
                with form.setting("Note names", "Only used when a sample's pitch can't be measured; otherwise "
                                                "the pitch decides which octave a name means."):
                    ui.select({"c3": "Middle C is C3", "c4": "Middle C is C4"}, value=rec.import_note_names,
                              on_change=lambda e: ed.set(rec, "import_note_names", e.value)) \
                        .props("dense outlined").classes("grow min-w-0")
                return
            folder = Path(rec.takes_dir or store.default_takes_dir(inst.meta.name)).expanduser()
            with form.setting("Takes", "Where the recorded takes are stored, one file per note and velocity."):
                folder_controls(folder, "Folder for the takes", lambda v: (ed.set(rec, "takes_dir", v),
                                                                           source.refresh()))
            with form.setting("Format", "Sample rate and bit depth of the takes and samples. The interface "
                                        "must run at the same rate."):
                ui.select({r: f"{r / 1000:g} kHz" for r in (44100, 48000, 88200, 96000)}, value=rec.sample_rate,
                          on_change=lambda e: ed.set(rec, "sample_rate", e.value)).props("dense outlined") \
                    .classes("grow min-w-0")
                ui.select({b: f"{b}-bit" for b in (16, 24, 32)}, value=rec.bit_depth,
                          on_change=lambda e: ed.set(rec, "bit_depth", e.value)).props("dense outlined") \
                    .classes("w-24 shrink-0")

        source()
        if not importing:
            recording.input_settings(ed)

    with ui.card().classes("w-full gap-1"):
        ui.label("Imported samples" if importing else "Sliced samples").classes("text-base font-medium mb-1")

        @ui.refreshable
        def target() -> None:
            folder = review.samples_dir(inst)
            with form.setting("Saved to", "Imported copies, under uniform names" if importing else
                              "Every hit as its own sample file; your takes are never changed."):
                folder_controls(folder, "Folder for the sliced samples",
                                lambda v: (ed.set(rec, "samples_dir", v), target.refresh()))
                if rec.samples_dir:
                    ui.button(icon="restart_alt", on_click=lambda: (ed.set(rec, "samples_dir", ""), target.refresh())) \
                        .props("flat dense round").tooltip("Back to the default folder")

        target()
        if not importing:  # imports have no noise-floor recording to learn from
            with form.setting("Denoise", "Uses the noise-floor recording as the noise profile and only changes the "
                                         "sliced samples. Leave it off if you already cleaned the takes, e.g. in "
                                         "WaveLab or Cubase."):
                ui.switch(value=proc.denoise,
                          on_change=lambda e: (ed.set(proc, "denoise", e.value), reduction.set_open(e.value)))
                with Collapse(proc.denoise, axis="x") as reduction:
                    ed.number("", proc, "denoise_reduction_db", cast=float, min=1, max=40, step=1,
                              suffix="dB max.").props("dense outlined").classes("w-32")
            cleanup_eqs(ed)
        with form.setting("Loops", "For sustained sounds: notes keep playing while the key is held. Loop markers "
                                   "in the takes (e.g. set in WaveLab) are used where they exist; otherwise a loop "
                                   "is searched in the steady part of each note."):
            ui.switch(value=proc.loops,
                      on_change=lambda e: (ed.set(proc, "loops", e.value), crossfade.set_open(e.value)))
            with Collapse(proc.loops, axis="x") as crossfade:
                ed.number("", proc, "loop_crossfade_ms", cast=float, min=0, max=500, step=5,
                          suffix="ms crossfade").props("dense outlined").classes("w-40")


def eq_summary(eq: CleanupEq) -> str:
    sigs = ", ".join(eq.signals) if eq.signals else "all signals"
    if not eq.filters:
        return f"{sigs} · no filters yet"
    takes = "every take" if eq.every_take else \
        f"{len(eq.takes)} take{'' if len(eq.takes) == 1 else 's'}" if eq.takes else "no takes picked yet"
    return f"{sigs} · {takes}"


def cleanup_eqs(ed: "Editor") -> None:
    """The clean-up EQs and their editor; which takes use one is picked in a hit's dialog."""
    inst, proc = ed.inst, ed.inst.processing
    with ui.dialog() as eq_dialog, ui.card().classes("w-[44rem] max-w-[95vw]") as eq_card:
        pass

    @ui.refreshable
    def eq_list() -> None:
        with ui.column().classes("gap-0 grow min-w-0"):
            for i, eq in enumerate(proc.cleanup_eqs):
                with ui.row().classes("items-center gap-2 no-wrap w-full"):
                    ui.label(eq.name).classes("text-sm truncate shrink-0 max-w-[45%]")
                    ui.label(eq_summary(eq)).classes("text-xs opacity-60 truncate grow min-w-0")
                    ui.button(icon="tune", on_click=lambda i=i: edit(i)).props("flat dense round size=sm") \
                        .tooltip("Change it")
            ui.button("EQ", icon="add", on_click=add).props("flat dense no-caps size=sm") \
                .tooltip("A new clean-up EQ, e.g. for mains hum on a pickup")

    def add() -> None:
        names = {e.name for e in proc.cleanup_eqs}
        name = next(f"EQ {n}" for n in itertools.count(1) if f"EQ {n}" not in names)
        proc.cleanup_eqs.append(CleanupEq(name=name))
        ed.changed()
        eq_list.refresh()
        edit(len(proc.cleanup_eqs) - 1)

    def remove(eq: CleanupEq) -> None:
        proc.cleanup_eqs.remove(eq)
        ed.changed()
        eq_dialog.close()

    def edit(i: int) -> None:
        eq = proc.cleanup_eqs[i]
        eq_card.clear()
        with eq_card:
            ui.label("Clean-up EQ").classes("text-lg font-medium")
            ui.label("Takes a noise out of the sliced samples, e.g. hum, buzz or a click's ring; your takes stay "
                     "as they are. Pick it for a take in a hit's dialog, or use it for every take.") \
                .classes("text-xs opacity-70")
            ed.text("Name", eq, "name").props("dense outlined").classes("w-full")
            with ui.row().classes("w-full items-center gap-4 no-wrap"):
                if len(inst.signals) > 1:
                    ui.select({s.id: s.name for s in inst.signals}, value=list(eq.signals), multiple=True,
                              label="Signals (none: all)",
                              on_change=lambda e: ed.set(eq, "signals", list(e.value or []))) \
                        .props("dense outlined use-chips").classes("grow min-w-0")
                ui.toggle({False: "Takes I pick", True: "Every take"}, value=eq.every_take,
                          on_change=lambda e: ed.set(eq, "every_take", bool(e.value))).props("dense no-caps")

            @ui.refreshable
            def bands() -> None:
                for f in eq.filters:
                    with ui.row().classes("w-full items-center gap-2 no-wrap"):
                        ui.select(cleanup_eq.KIND_LABELS, value=f.kind,
                                  on_change=lambda e, f=f: kind(f, e.value)).props("dense outlined") \
                            .classes("w-48 shrink-0")
                        ed.number("", f, "hz", cast=float, min=10, max=40000, step=1, suffix="Hz") \
                            .props("dense outlined").classes("w-28")
                        if f.kind in cleanup_eq.HAS_GAIN:
                            ed.number("", f, "gain_db", cast=float, min=-60, max=24, step=1, suffix="dB") \
                                .props("dense outlined").classes("w-24")
                        if f.kind not in ("highpass", "lowpass"):
                            ed.number("", f, "q", cast=float, min=0.1, max=100, step=0.5, prefix="Q") \
                                .props("dense outlined").classes("w-24") \
                                .tooltip("Higher is narrower")
                        if f.kind == "hum":
                            ed.number("", f, "harmonics", min=1, max=40, step=1, suffix="×") \
                                .props("dense outlined").classes("w-20") \
                                .tooltip("How many: the frequency and its multiples (100, 150, … Hz for 50 Hz)")
                        ui.space()
                        ui.button(icon="close", on_click=lambda f=f: drop(f)).props("flat dense round size=sm") \
                            .tooltip("Remove this filter")
                if not eq.filters:
                    ui.label("No filters yet. Mains hum is 50 Hz in Europe, 60 Hz in the Americas.") \
                        .classes("text-sm opacity-70")

            def kind(f: EqFilter, value: str) -> None:
                f.kind = value
                f.hz, f.gain_db, f.q = cleanup_eq.STARTS[value]
                ed.changed()
                bands.refresh()

            def drop(f: EqFilter) -> None:
                eq.filters.remove(f)
                ed.changed()
                bands.refresh()

            def more() -> None:
                hz, gain, q = cleanup_eq.STARTS["hum"]
                eq.filters.append(EqFilter(kind="hum", hz=hz, gain_db=gain, q=q))
                ed.changed()
                bands.refresh()

            with ui.column().classes("w-full gap-1 mt-2"):
                ui.label("Filters").classes("text-sm font-medium")
                bands()
            with ui.row().classes("w-full items-center"):
                ui.button("Filter", icon="add", on_click=more).props("flat dense no-caps")
                ui.space()
                ui.button("Delete this EQ", on_click=lambda: remove(eq)).props("flat color=red no-caps")
                ui.button("Close", on_click=eq_dialog.close).props("flat")
        eq_dialog.open()

    eq_dialog.on("hide", eq_list.refresh)
    with form.setting("Clean-up EQ", "Takes hum, buzz or a click's ring out of the takes you pick, or of every "
                                     "take. Only the sliced samples change; changing it slices those takes again."):
        eq_list()


def step_worksheet(ed: "Editor") -> None:
    inst = ed.inst
    slug = inst.meta.slug
    path = store.manifest_path(slug)
    state: dict[str, Any] = {"manifest": None, "problems_only": False, "busy": False,
                             "progress": 0.0, "label": "", "stale": None, "edited": []}
    ui.add_css(CSS)
    ui.add_body_html(f"<script>{JS}</script>")
    try:
        state["manifest"] = review.load(path)
    except Exception as e:  # e.g. written by an older version; slicing again replaces it
        ui.label(f"{path.name} can't be read ({e.__class__.__name__}); slice again to replace it. "
                 "Rejections stored in it are lost.").classes("text-sm text-orange-600")

    importing = inst.recording.source == "samples"
    rec = inst.recording


    def tick() -> None:
        progress.value = state["progress"]
        progress_label.text = state["label"]

    ui.timer(0.25, tick)

    async def slice_all(only: str | None = None) -> None:
        """Slice everything, or with `only` just that recording (right after recording it). While a slice
        runs, this one waits for it: a change made meanwhile (a recording moved, a cut set) is sliced too."""
        while state["busy"]:
            await asyncio.sleep(0.1)
        if not still_there():
            return
        ed.autosave()
        state["busy"] = True
        button.disable()
        progress_box.set_open(True)

        def report(i: int, n: int, title: str) -> None:
            state["progress"], state["label"] = (i / n if n else 1), f"{i}/{n} · {title}"

        try:
            # A snapshot: the wizard stays editable while the worker thread slices.
            snapshot = inst.model_copy(deep=True)
            if only and state["manifest"]:
                manifest = await run.io_bound(slicing.process_one, snapshot, state["manifest"], only)
            else:
                manifest = await run.io_bound(slicing.process, snapshot, state["manifest"], report)
        except ValueError as e:
            ui.notify(str(e), type="warning", multi_line=True)
        except Exception as e:  # unexpected I/O or audio errors: show them instead of failing silently
            ui.notify(f"Slicing failed: {e}", type="negative", multi_line=True)
        else:
            review.save(manifest, path)
            state["manifest"] = manifest
            state["stale"] = None
            results.refresh()
            ed.refresh()  # the summary shows what was recorded or imported
            if not only:
                c = manifest.counts()
                ui.notify(f"{c['hits']} hits from {c['takes_ok']} takes; {c['errors']} errors, "
                          f"{c['warnings']} warnings", type="positive")
        finally:
            state["busy"] = False
            button.enable()
            progress_box.set_open(False)
            state["label"] = ""


    # Dialogs live outside the refreshable results, so that redrawing them doesn't delete one that's open.
    with ui.dialog() as dialog, ui.card().classes("min-w-[28rem]") as dialog_card:
        pass
    with ui.dialog() as lane_dialog, ui.card().classes("min-w-[22rem]") as lane_card:
        pass
    anchor = ui.element("div")  # stays put while the results redraw; hosts the re-slice timers

    def resliced(key: str) -> None:
        """Right after recording: slice just that recording."""
        with anchor:
            ui.timer(0.1, lambda: slice_all(only=key), once=True)

    def note_results(note: str, pass_id: str) -> list[TakeResult]:
        manifest = state["manifest"]
        return [t for t in manifest.takes if t.note == note and t.pass_id == pass_id] if manifest else []

    def still_there() -> bool:
        """False once the instrument was deleted, e.g. from the list in another tab: then nothing is
        written, or its folder would come back without its settings."""
        if store.exists(slug):
            return True
        ui.notify("This instrument was deleted; nothing is saved.", type="warning")
        return False

    def saved(take: TakeResult) -> None:
        """After a decision: lanes and loudness checks again, save, redraw."""
        if not still_there():
            return
        if take.kind == "note" and not importing:
            lanes = note_lanes(inst, take.note)
            settle(take, lanes)
            check_lanes(note_results(take.note, take.pass_id), lanes)
        review.save(state["manifest"], path)
        results.refresh()

    def find(ref: str) -> tuple[TakeResult, Hit] | None:
        key, _, hit_id = ref.partition("|")
        take = next((t for t in state["manifest"].takes if t.key == key), None) if state["manifest"] else None
        hit = next((h for h in take.hits if h.id == hit_id), None) if take else None
        return (take, hit) if hit else None

    def move(ref: str, lane: str, note: str, pass_id: str) -> None:
        found = find(ref)
        if not found or importing:
            return
        take, hit = found
        if (take.note, take.pass_id) != (note, pass_id):
            ui.notify("Hits can only move between the velocities of their own note.", type="warning")
            return
        hit.layer_set = None if lane == take.lane else lane
        saved(take)

    def toggle_reject(take: TakeResult, hit: Hit, value: bool) -> None:
        hit.rejected = value
        saved(take)
        open_hit(take, hit, autoplay=False)

    def toggle_loop(take: TakeResult, hit: Hit, use: bool) -> None:
        hit.loop_off = not use
        saved(take)

    def choose_lane(take: TakeResult, hit: Hit, lane: str) -> None:
        move(f"{take.key}|{hit.id}", lane, take.note, take.pass_id)
        open_hit(take, hit, autoplay=False)

    def record_instead(take: TakeResult, hit: Hit) -> None:
        """A new hit for this lane; the old one is rejected once the new one is saved."""
        lane = lane_of(hit, take, note_lanes(inst, take.note))
        plan = next((t for t in plan_takes(inst) if t.note_base == take.note and t.lane == lane
                     and t.pass_id == take.pass_id), None)
        if plan is None:
            return

        def after(key: str) -> None:
            hit.rejected = True
            saved(take)
            resliced(key)

        dialog.close()
        recording.record_more(ed, plan.key, 1, after)

    def open_hit(take: TakeResult, hit: Hit, autoplay: bool = True) -> None:
        dialog_card.clear()
        with dialog_card:
            ui.label(plain_title(take)).classes("text-lg font-medium")
            ui.label(hit.id).classes("text-xs font-mono opacity-70")
            lanes = note_lanes(inst, take.note) if take.note else []
            lane = lane_of(hit, take, lanes) if take.note and not importing else hit.layer
            facts = [("Velocity", lane or "–"),
                     ("Length", f"{(hit.end - hit.start) / (hit.sample_rate or take.sample_rate or 1):.2f} s"),
                     ("Loudness (first 0.3 s)", f"{hit.rms_db:.1f} dBFS")]
            if hit.cents is not None:
                facts.append(("Tuning", f"{hit.cents:+.1f} cents"))
            facts += [(f"Peak {sig}", f"{p:.1f} dBFS") for sig, p in hit.peak_db.items()]
            with ui.grid(columns=2).classes("gap-x-6 gap-y-1 text-sm"):
                for k, v in facts:
                    ui.label(k).classes("opacity-70")
                    ui.label(v)
            if take.envelope:
                with ui.row().classes("w-full items-center justify-between no-wrap"):
                    ui.label("Where it was cut in its recording").classes("text-xs opacity-60")
                    recording_actions(take)
                if importing or not take.length:
                    ui.html(waveform(take, hit.id), sanitize=False).classes("w-full")
                else:
                    cut_editor(take, hit)
            with ui.row().classes("w-full items-center gap-4"):
                if take.kind == "note" and not importing and len(lanes) > 1:
                    ui.select({la: la for la in lanes}, value=lane, label="Move to velocity",
                              on_change=lambda e: choose_lane(take, hit, e.value)).props("dense").classes("w-40")
                if not importing:
                    eq_menu(take, hit)
            for f in hit.flags:
                sev, text = FLAGS.get(f, ("info", f))
                ui.label(f"● {f.replace('_', ' ')}: {text}").style(f"color:{COLORS[sev]}").classes("text-sm")
            folder = Path(state["manifest"].samples_dir)
            for i, (sig, name) in enumerate(hit.files.items()):
                mtime = int((folder / name).stat().st_mtime) if (folder / name).exists() else 0
                ui.label(sig).classes("text-sm font-medium mt-2")
                ui.audio(f"/audio/{slug}/{quote(name)}?v={mtime}", autoplay=autoplay and i == 0).classes("w-full")
            if hit.loop_start is not None and hit.loop_end is not None:
                sr = hit.sample_rate or take.sample_rate or 1
                source = "marker from the take" if hit.loop_source == "marker" else \
                    f"found automatically, match {hit.loop_score:.2f}"
                ui.label(f"Loop {hit.loop_start / sr:.2f}–{hit.loop_end / sr:.2f} s ({source})") \
                    .classes("text-sm font-medium mt-2")
                first = next(iter(hit.files.values()))
                mtime = int((folder / first).stat().st_mtime) if (folder / first).exists() else 0
                ui.audio(f"/loop-preview/{slug}/{quote(first)}?start={hit.loop_start}&end={hit.loop_end}"
                         f"&v={mtime}").classes("w-full")
            with ui.row().classes("w-full justify-between mt-2"):
                with ui.row().classes("gap-4"):
                    ui.switch("Rejected", value=hit.rejected, on_change=lambda e: toggle_reject(take, hit, e.value))
                    if hit.loop_start is not None:
                        ui.switch("Use loop", value=not hit.loop_off,
                                  on_change=lambda e: toggle_loop(take, hit, e.value))
                with ui.row().classes("gap-1"):
                    if take.kind == "note" and not importing and lane:
                        ui.button("Record a new one instead", icon="fiber_manual_record",
                                  on_click=lambda: record_instead(take, hit)).props("flat color=red") \
                            .tooltip("Records one more hit for this velocity; this one is rejected once it's saved")
                    ui.button("Close", on_click=dialog.close).props("flat")
        dialog.open()

    # Per-note lanes: changing them gives the note its own lanes instead of the defaults.
    def set_lanes(note: str, lanes: list[str], starts: list[int]) -> None:
        inst.note_lanes[note] = NoteLanes(layers=lanes, splits=starts)
        ed.changed()

    def set_starts(note: str, starts: Any) -> None:
        bounds = lane_bounds(inst, note)
        try:
            values = [int(v) for v in starts]
        except (TypeError, ValueError):
            return
        if len(values) == len(bounds) - 1 and values == sorted(set(values)) and all(2 <= v <= 127 for v in values):
            set_lanes(note, [lane for lane, _, _ in bounds], values)

    def remove_lane(note: str, lane: str) -> None:
        bounds = lane_bounds(inst, note)
        keep = [(name, lo) for name, lo, _ in bounds if name != lane]
        set_lanes(note, [name for name, _ in keep], [lo for _, lo in keep][1:] if keep else [])

    def use_defaults(note: str) -> None:
        inst.note_lanes.pop(note, None)
        ed.changed()

    def add_lane(note: str, title: str) -> None:
        bounds = lane_bounds(inst, note)
        existing = [lane for lane, _, _ in bounds]
        lane_card.clear()
        with lane_card:
            ui.label(f"A new velocity for {title}").classes("text-lg font-medium")
            suggestion = next((s for s in SUGGESTED_LANES[::-1] if s not in existing), "")
            name = ui.input("Name", value=suggestion).props("dense autofocus")
            start = ui.number("Starts at velocity", value=min(127, bounds[-1][1] - 10 if bounds else 64),
                              min=2, max=127, precision=0).props("dense")
            ui.label("Only this note gets it; the defaults in Dynamics stay as they are. The velocities stay "
                     "ordered by where they start.").classes("text-xs opacity-70")

            def add() -> None:
                lane, at = (name.value or "").strip(), int(start.value or 0)
                if not SAFE_LANE.fullmatch(lane) or lane.lower() in {e.lower() for e in existing}:
                    ui.notify("Use a new name with letters, digits and “-” only.", type="warning")
                    return
                if at <= 1 or at in {lo for _, lo, _ in bounds}:
                    ui.notify(f"Another velocity already starts at {at}; choose another.", type="warning")
                    return
                entries = sorted([(lo, n) for n, lo, _ in bounds if n] + [(at, lane)])
                entries = [(max(1, lo), n) for lo, n in entries]
                lanes = [n for _, n in entries]
                starts = sorted({lo for lo, _ in entries[1:]} - {1})
                lane_dialog.close()
                set_lanes(note, lanes, starts if len(starts) == len(lanes) - 1 else [])

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=lane_dialog.close).props("flat")
                ui.button("Add velocity", on_click=add)
        lane_dialog.open()

    def recording_actions(take: TakeResult) -> None:
        """What can be done with the whole recording a hit is from."""
        if importing:
            return
        with ui.row().classes("gap-1 no-wrap"):
            if take.cut_by_hand:
                ui.button("Find hits automatically", on_click=lambda: reslice(take, by_hand=False)) \
                    .props("flat dense no-caps size=sm") \
                    .tooltip("Forget the cuts set by hand and find this recording's hits again")
            else:
                ui.button("Re-slice", on_click=lambda: reslice(take)).props("flat dense no-caps size=sm") \
                    .tooltip("Find its hits again, e.g. after a fix in finding them; decisions about hits "
                             "that are cut where they were are kept")
            if take.kind == "note":
                ui.button("Move this recording…", on_click=lambda: move_take(take)) \
                    .props("flat dense no-caps size=sm") \
                    .tooltip("E.g. when it was played on the wrong note: all its hits go along")
            edit_buttons(take)

    def edit_buttons(take: TakeResult) -> None:
        """Open the recording, e.g. in an audio editor; slicing again then picks up what was changed."""
        folder = Path(rec.takes_dir).expanduser()

        def files() -> list[Path]:
            return [folder / f for f in take.files if (folder / f).is_file()]

        def editing(paths: list[Path]) -> None:
            for f in paths:
                takes_meta.editing(slug, f)

        open_with.open_button(files, "Opens the recording, e.g. in your audio editor to take out a click; slice it "
                              "again afterwards. Edit the recording, not the sliced samples: slicing writes those "
                              "anew.", before=editing)

    def eq_menu(take: TakeResult, hit: Hit) -> None:
        """Which clean-up EQs the recording uses; the button names them."""
        eqs = [eq for eq in inst.processing.cleanup_eqs
               if eq.filters and (not eq.signals or set(eq.signals) & set(take.signals))]
        if not eqs:
            return
        used = [eq.name for eq in eqs if eq.every_take or take.key in eq.takes]
        with ui.button("Clean-up EQ" + (f": {', '.join(used)}" if used else "")) \
                .props("flat dense no-caps icon=graphic_eq icon-right=arrow_drop_down").classes("max-w-[20rem]"):
            with ui.menu(), ui.column().classes("p-2 gap-0"):
                for eq in eqs:
                    if eq.every_take:
                        ui.checkbox(f"{eq.name} (every take)", value=True).disable()
                    else:
                        ui.checkbox(eq.name, value=take.key in eq.takes,
                                    on_change=lambda e, eq=eq: pick_eq(take, hit, eq, e.value))

    async def pick_eq(take: TakeResult, hit: Hit, eq: CleanupEq, on: bool) -> None:
        """The recording's samples with or without `eq`, sliced again right away to hear it."""
        ed.set(eq, "takes", [k for k in eq.takes if k != take.key] + ([take.key] if on else []))
        await slice_all(only=take.key)
        reopen(take.key, hit.start)

    def reopen(key: str, start: int) -> None:
        """After slicing a recording from its dialog: the dialog again, on the hit that starts at `start`."""
        found = next((t for t in state["manifest"].takes if t.key == key), None) if state["manifest"] else None
        again = next((h for h in found.hits if h.start == start), None) if found else None
        if found and again:
            open_hit(found, again)
        elif found:
            open_recording(found)
        else:
            dialog.close()

    def open_recording(take: TakeResult) -> None:
        """A recording in which no hit was found: mark the one that was played by hand, or move it."""
        dialog_card.clear()
        with dialog_card:
            ui.label(plain_title(take)).classes("text-lg font-medium")
            ui.label("No hit was found in this recording. Drag the blue lines over the one you played to cut "
                     "it by hand.").classes("text-sm opacity-80")
            with ui.row().classes("w-full items-center justify-between no-wrap"):
                ui.label("The recording").classes("text-xs opacity-60")
                recording_actions(take)
            if take.length:
                cut_editor(take, None)
            with ui.row().classes("w-full justify-end mt-2"):
                ui.button("Close", on_click=dialog.close).props("flat")
        dialog.open()

    def first_file(take: TakeResult) -> Path:
        return Path(rec.takes_dir).expanduser() / take.files[0]

    async def reslice(take: TakeResult, by_hand: bool = True) -> None:
        """`by_hand=False` forgets the cuts set by hand first."""
        dialog.close()
        if not by_hand:
            takes_meta.save_cuts(slug, first_file(take), None)
        await slice_all(only=take.key)
        found = next((t for t in state["manifest"].takes if t.key == take.key), None) if state["manifest"] else None
        if found is not None:
            ui.notify(f"{plain_title(found)}: {len(found.hits)} hit{'' if len(found.hits) == 1 else 's'}.",
                      type="positive")

    def cut_editor(take: TakeResult, hit: Hit | None) -> None:
        """The recording's waveform with this hit's cut: drag its start or end to set it by hand.
        Without a hit, a new one: at the first beat it was asked for, as long as notes ring."""
        length, sr = take.length or 1, take.sample_rate or 1
        if hit is None:
            beat = (takes_meta.hits_at(slug, first_file(take)) or [0.0])[0]
            first, last = int(beat * sr), int(beat * sr + inst.recording.ring_seconds * sr)
            first, last = min(first, length - sr // 10), min(last, length)
        else:
            first, last = hit.start, hit.end
        others = sorted((h for h in take.hits if hit is None or h.id != hit.id), key=lambda h: h.start)
        lo = max((h.end for h in others if h.end <= first), default=0) / length
        hi = min((h.start for h in others if h.start >= last), default=length) / length
        start, end = first / length, last / length
        with ui.element("div").classes("vi-cut").props(
                f"data-start={start:.6f} data-end={end:.6f} data-lo={lo:.6f} data-hi={hi:.6f} "
                f"data-seconds={length / sr:.4f}") as box:
            ui.html(waveform(take, hit.id if hit else set()), sanitize=False).classes("w-full")
            ui.html(f"<div class='vi-cut-range' style='left:{start * 100:.3f}%;width:{(end - start) * 100:.3f}%'>"
                    f"</div><div class='vi-cut-handle' data-edge='start' style='left:{start * 100:.3f}%'></div>"
                    f"<div class='vi-cut-handle' data-edge='end' style='left:{end * 100:.3f}%'></div>"
                    f"<div class='flex justify-between text-xs opacity-60 mt-1'><span class='vi-cut-times'>"
                    f"{first / sr:.2f} – {last / sr:.2f} s ({(last - first) / sr:.2f} s)</span>"
                    f"<span>drag the blue lines to change</span></div>", sanitize=False).classes("w-full")
        box.on("cut-change", lambda e: set_cut(take, hit, e.args["detail"]), args=["detail"])

    async def set_cut(take: TakeResult, hit: Hit | None, detail: Any) -> None:
        """Sets the recording's cuts by hand: this hit (or a new one) where it was dragged, the others
        as they are."""
        try:
            a, b = (int(round(float(detail[k]) * (take.length or 0))) for k in ("start", "end"))
        except (KeyError, TypeError, ValueError):
            return
        if b - a < int(0.05 * (take.sample_rate or 48000)):
            return
        cuts = [(h.start, h.end) for h in take.hits if hit is None or h.id != hit.id] + [(a, b)]
        takes_meta.save_cuts(slug, first_file(take), cuts)
        if hit is not None:
            hit.start, hit.end = a, b  # so its decisions stay with it when sliced again
        # The dialog stays open: its contents are replaced once the recording is sliced.
        await slice_all(only=take.key)
        reopen(take.key, a)

    def move_take(take: TakeResult) -> None:
        """The whole recording to another note or velocity; there it's sliced afresh."""
        dialog.close()
        notes: dict[str, str] = {}
        for t in plan_takes(inst):
            if t.kind == "note" and t.pass_id == take.pass_id:
                notes.setdefault(t.note_base, t.title.replace(f" · {t.lane}", ""))
        lane_card.clear()
        with lane_card:
            ui.label(f"Move this recording of {plain_title(take)}").classes("text-lg font-medium")
            note_select = ui.select(notes, value=take.note, label="To note").props("dense outlined") \
                .classes("w-full")
            lane_select = ui.select([], label="Velocity").props("dense outlined").classes("w-full")

            def lanes_for(note: str) -> None:
                lanes = note_lanes(inst, note)
                lane_select.set_options(lanes, value=take.lane if take.lane in lanes else lanes[0])

            note_select.on_value_change(lambda e: lanes_for(e.value))
            lanes_for(take.note)
            ui.label("If that velocity is recorded already, this becomes more hits of it; nothing is replaced. "
                     "Rejections and moves of its hits start over there.").classes("text-xs opacity-70")

            async def go() -> None:
                try:
                    key = relocate.move_recording(inst, Path(rec.takes_dir).expanduser(), take.key,
                                                  note_select.value, lane_select.value)
                except (ValueError, OSError) as e:
                    ui.notify(str(e), type="warning", multi_line=True)
                    return
                lane_dialog.close()
                if any(key in eq.takes for eq in inst.processing.cleanup_eqs):
                    ed.changed()  # its clean-up EQs went along
                manifest = state["manifest"]
                planned = any(t.key == take.key for t in plan_takes(inst))
                if manifest and not planned:  # its old name is gone, not just unrecorded
                    manifest.takes = [t for t in manifest.takes if t.key != take.key]
                if planned:
                    await slice_all(only=take.key)
                await slice_all(only=key)
                ui.notify(f"Moved to {notes[note_select.value]} · {lane_select.value}.", type="positive")

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=lane_dialog.close).props("flat")
                ui.button("Move", on_click=go)
        lane_dialog.open()

    def eq_tip(take: TakeResult) -> str:
        names = cleanup_eq.describe(inst, take.key, take.signals) if take.cleanup_eq else ""
        return f"clean-up EQ: {names}" if names else "clean-up EQ" if take.cleanup_eq else ""

    def hit_text(h: Hit) -> tuple[str, list[str]]:
        """A hit's short label (its tuning, its loop) and what's worth knowing about it."""
        text = f"{h.cents:+.0f}¢" if h.cents is not None else "●"
        tips = [f.replace("_", " ") for f in h.flags]
        if h.layer_set:
            tips.append("moved here")
        if h.loop_start is not None:
            # ⟳ loops, ⟳M uses a marker from the take, a crossed-out ⟳ was turned off.
            text += " ⟳̸" if h.loop_off else (" ⟳M" if h.loop_source == "marker" else " ⟳")
            tips.append("loop turned off" if h.loop_off else "loop from a marker" if h.loop_source == "marker"
                        else f"automatic loop, match {h.loop_score:.2f}")
        if h.rejected:
            tips.append("rejected")
        return text, tips

    def draggable_hit(element: Any, take: TakeResult, h: Hit) -> None:
        element.props("draggable").on("click", lambda t=take, hit=h: open_hit(t, hit))
        element.on("dragstart", js_handler=DRAG_JS.format(ref=json.dumps(f"{take.key}|{h.id}")))

    def chip(take: TakeResult, h: Hit, number: int | None = None) -> None:
        """A hit without a waveform to show it in: imported samples, hits of a removed velocity."""
        text, tips = hit_text(h)
        text += f" · {h.rms_db:.0f} dB"
        if take.cleanup_eq:
            tips.append(eq_tip(take))
        c = ui.chip(text if number is None else f"{number} {text}", color=None,
                    icon="graphic_eq" if take.cleanup_eq else None) \
            .props("dense square clickable").classes("vi-chip-drag").style(
                f"background:{hit_color(h)};color:white" + (";text-decoration:line-through" if h.rejected else ""))
        draggable_hit(c, take, h)
        if tips:
            c.tooltip(", ".join(tips))

    def compare_button(hits: list[tuple[TakeResult, Hit]]) -> None:
        kept = [h for _, h in hits if not h.rejected]  # rejected ones are decided; compare the rest
        button = ui.button(icon="play_arrow").props("flat dense round size=sm")
        if not kept:  # still there, disabled, so the waves of all rows start at the same x
            button.disable()
            return
        names = "&".join(f"names={quote(next(iter(h.files.values())))}" for h in kept)
        url = f"/compare/{slug}?{names}"
        button.on("click", js_handler=f"() => ({PLAY_JS})({json.dumps(url)})")
        button.tooltip("Play it (click again to stop)" if len(kept) == 1 else
                       f"Play the {len(kept)} kept ones with their attacks {COMPARE_EVERY_S:g} s apart "
                       "(click again to stop)")

    def is_problem(recs: list[TakeResult]) -> bool:
        return any(t.status == "error" or t.message or any(h.severity() in ("error", "warning") and not h.rejected
                                                             for h in t.hits) for t in recs)

    def take_card(take: Take, result: TakeResult | None) -> None:
        """An extra: one take, recorded as a whole. One line like the noise floor's until it's
        recorded, then its hits below."""
        recorded = result is not None and result.status != "missing"
        if state["problems_only"] and not is_problem([result] if recorded else []):
            return
        with ui.card().classes("w-full py-1").props("flat bordered"), \
                ui.column().classes("vi-lane gap-1 w-full") as lane:
            with ui.row().classes("items-center gap-2 w-full no-wrap"):
                record_button(take, recorded)
                if recorded:
                    compare_button([(result, h) for h in result.hits])
                ui.label(take.title).classes("font-medium whitespace-nowrap")
                if recorded and result.message:
                    ui.label(result.message).classes("text-xs text-orange-600 truncate")
                elif recorded and result.info:
                    ui.label(result.info).classes("text-xs opacity-60 truncate")
                elif not recorded:
                    drop_hint(take)
            if recorded and result.hits:
                hits = [(result, h) for h in result.hits]
                with ui.element("div").classes("w-full"):
                    hit_strip(hits, sum(hit_seconds(r, h) for r, h in hits),
                              max((hit_peak_db(h) for _, h in hits), default=0.0))
            drop_target(lane, take, "", take.pass_id)

    def noise_card(take: Take, result: TakeResult | None) -> None:
        """The noise floor: nothing to judge but whether it's there and how loud, so one line."""
        recorded = result is not None and result.status != "missing"
        with ui.card().classes("w-full py-1").props("flat bordered"):
            with ui.row().classes("vi-lane items-center gap-2 w-full no-wrap") as row:
                record_button(take, recorded)
                ui.label(take.title).classes("font-medium whitespace-nowrap")
                if result and result.status == "noise_floor":
                    level = result.level_db
                    if level is None and state["manifest"] and take.pass_id == recording_passes(inst)[0].id:
                        level = state["manifest"].noise_floor_db  # sliced before levels were kept per pass
                    text = f"{result.message} of silence" + (f" · {level:.1f} dBFS" if level is not None else "")
                    if state["manifest"] and state["manifest"].denoised:
                        text += " · used to denoise the samples"
                    ui.label(text).classes("text-sm opacity-70 truncate")
                elif recorded and result.message:
                    ui.label(result.message).classes("text-xs text-orange-600 truncate")
                elif not recorded:
                    drop_hint(take)
            drop_target(row, take, "", take.pass_id)

    def controls_strip() -> ui.row:
        return ui.row().classes("items-center gap-1 no-wrap shrink-0").style(f"height:{WAVE_H}px")

    def drop_hint(take: Take) -> None:
        ui.label("or drop an audio file here").classes("text-xs opacity-50") \
            .tooltip(f"E.g. a file exported from Cubase or WaveLab; it is stored as {', '.join(take.files)}")

    def record_button(take: Take, recorded: bool) -> None:
        """Whether it's recorded shows in the row itself: its waveform, or the drop hint. A recorded
        velocity gets more hits beside its own, never replaced: the bad ones are rejected instead. The
        noise floor and extras are single takes, so they're recorded again."""
        # As record_take decides: by the files, which may not be sliced yet (e.g. just dropped).
        folder = Path(rec.takes_dir).expanduser() if rec.takes_dir else None
        recorded = recorded or (folder is not None and all((folder / f).exists() for f in take.files))
        if not recorded:
            tip = "Record"
        elif take.kind == "note":
            tip = "Record more hits; the ones you have stay until you reject them"
        else:
            tip = "Record again (replaces this recording)"
        ui.button(icon="fiber_manual_record", color=None,
                  on_click=lambda t=take: recording.record_take(ed, t.number, resliced)) \
            .props("flat dense round size=sm text-color=red").tooltip(tip)

    def velocity_slider(note: str, bounds: list[tuple[str, int, int]]) -> None:
        """A vertical bar from velocity 1 (top) to 127, one draggable dot per boundary between velocities."""
        segs = "".join(
            f"<div class='vi-vel-seg' style='top:{(lo - 1) / 127 * 100:.2f}%;height:{(hi - lo + 1) / 127 * 100:.2f}%;"
            f"background:{VELOCITY_COLOURS[k % len(VELOCITY_COLOURS)]}'></div>"
            for k, (_, lo, hi) in enumerate(bounds))
        dots = "".join(
            f"<div class='vi-vel-dot' data-value='{lo}' style='top:{(lo - 1) / 127 * 100:.2f}%'>"
            f"<span>{lo}</span></div>" for _, lo, _ in bounds[1:])
        # As tall as the rows beside it, which grow with their wave lanes.
        track = ui.element("div").classes("vi-vel shrink-0 self-stretch").style(f"min-height:{len(bounds) * ROW_H - 8}px")
        with track:
            ui.html(f"<div class='vi-vel-segs'>{segs}</div>{dots}", sanitize=False)
        track.tooltip("Velocity 1 at the top, 127 at the bottom: drag a dot to move where the next velocity "
                      "starts for this note")
        track.on("vel-change", lambda e, n=note: set_starts(n, e.args["detail"]), args=["detail"])

    def drop_target(row: Any, take: Take, note: str, pass_id: str) -> None:
        """Hits dragged from another velocity of this note, or audio files dropped from the Finder."""
        url = json.dumps(f"/drop/{slug}/{quote(take.key, safe='')}")
        row.on("dragover", js_handler=DRAG_OVER_JS)
        row.on("dragleave", js_handler=DRAG_LEAVE_JS)
        row.on("drop", js_handler=DROP_JS.replace("URL", url),
               handler=lambda e, la=take.lane, n=note, ps=pass_id: dropped(e.args, la, n, ps))

    def dropped(args: Any, lane: str | None, note: str, pass_id: str) -> None:
        if isinstance(args, list):  # audio files, already stored by the /drop endpoint
            for r in args:
                ui.notify(r.get("message", ""), type="positive" if r.get("ok") else "warning", multi_line=True)
            keys = [r["key"] for r in args if r.get("ok")]
            if len(set(keys)) == 1:
                resliced(keys[0])
            elif keys:
                with anchor:
                    ui.timer(0.1, lambda: slice_all(), once=True)
        elif isinstance(args, str) and lane:
            move(args, lane, note, pass_id)

    def hit_seconds(r: TakeResult, h: Hit) -> float:
        return (h.end - h.start) / (h.sample_rate or r.sample_rate or 1)

    def hit_strip(hits: list[tuple[TakeResult, Hit]], span_s: float, loudest_db: float,
                  empty: list[TakeResult] = ()) -> None:
        """A row's hits side by side, whichever recording they come from, each as wide as it is long
        and as tall as it is loud: `span_s` seconds fill the row and `loudest_db` its height, the same
        for all rows of a note. Click a hit to review it, drag it to another velocity. Recordings of
        the row in which no hit was found get a tile of their own, to open them."""
        with ui.element("div").classes("vi-hits"):
            for r in empty:
                ui.label("no hit found").classes("vi-hit-empty").on("click", lambda t=r: open_recording(t))
            for r, h in hits:
                if not r.envelope:  # e.g. imported files: nothing to draw
                    chip(r, h)
                    continue
                text, tips = hit_text(h)
                basis = hit_seconds(r, h) / span_s * 100 if span_s else 100 / len(hits)
                with ui.element("div").classes("vi-hit").style(f"flex:0 1 {basis:.2f}%;position:relative") as tile:
                    ui.html(hit_wave(r, h, WAVE_H, loudest_db), sanitize=False)
                    if r.cleanup_eq:
                        ui.icon("graphic_eq").classes("vi-hit-eq")
                        tips.append(eq_tip(r))
                    ui.label(text).classes("vi-hit-label").style(
                        f"color:{hit_color(h)}" + (";text-decoration:line-through" if h.rejected else ""))
                    # The attack's loudness: what decides which velocity a hit fits.
                    ui.label(f"{h.rms_db:.0f} dB").classes("vi-hit-level")
                draggable_hit(tile, r, h)
                if tips:
                    tile.tooltip(", ".join(tips))

    def note_card(lane_takes: list[Take], results_by_key: dict[str, TakeResult]) -> None:
        first = lane_takes[0]
        note = first.note_base
        recs = note_results(note, first.pass_id)
        if state["problems_only"] and not is_problem(recs):
            return
        title = first.title.replace(f" · {first.lane}", "")
        own = note in inst.note_lanes
        lanes = note_lanes(inst, note)
        by_lane: dict[str | None, list[tuple[TakeResult, Hit]]] = {}
        for r in recs:
            for h in r.hits:
                by_lane.setdefault(lane_of(h, r, lanes), []).append((r, h))
        # The row with the most to play fills the width; the others are drawn to the same scale.
        span_s = max((sum(hit_seconds(r, h) for r, h in row) for row in by_lane.values()), default=0.0)
        loudest_db = max((hit_peak_db(h) for row in by_lane.values() for _, h in row), default=0.0)
        with ui.card().classes("w-full").props("flat bordered"):
            with ui.row().classes("items-center gap-2 w-full"):
                ui.label(title).classes("font-medium")
                if own:
                    ui.badge("custom velocities", color="primary").props("outline")
                ui.space()
                ui.button("+ velocity", on_click=lambda n=note, t=title: add_lane(n, t)).props("flat dense no-caps")
                if own:
                    ui.button("Use default velocities", on_click=lambda n=note: use_defaults(n)) \
                        .props("flat dense no-caps")
            # A row's own status ("not recorded yet") shows in the row: here what needs attention, in
            # orange, and what's good to know, in grey.
            recorded_recs = [r for r in recs if r.status != "missing"]
            for text, style in (("; ".join(filter(None, (r.message for r in recorded_recs))), "text-orange-600"),
                                ("; ".join(filter(None, (r.info for r in recorded_recs))), "opacity-60")):
                if text:
                    ui.label(text).classes(f"text-xs {style}")
            bounds = lane_bounds(inst, note)
            # As wide as the longest velocity name of the instrument, so short names leave no gap but
            # the buttons still line up across notes.
            name_w = max(len(n) for n in [*inst.dynamics.layers, *(nl for v in inst.note_lanes.values()
                                                                    for nl in v.layers)] or ["p"])
            with ui.row().classes("w-full no-wrap items-start gap-1"):
                if len(bounds) > 1:
                    velocity_slider(note, bounds)
                with ui.column().classes("grow gap-0 min-w-0"):
                    for (lane, lo, hi), take in zip(bounds, lane_takes):
                        result = results_by_key.get(take.key)
                        recorded = result is not None and result.status != "missing"
                        hits = by_lane.get(lane, [])
                        with ui.row().classes("vi-lane items-start gap-1 w-full no-wrap").style(f"min-height:{ROW_H}px") \
                                as row:
                            with controls_strip():
                                # Fixed widths, so the buttons line up across rows and notes; the
                                # range fits "100–127".
                                ui.label(lane).classes("shrink-0 font-medium whitespace-nowrap") \
                                    .style(f"width:{name_w + 1}ch")
                                ui.label(f"{lo}–{hi}").classes("shrink-0 text-xs opacity-70 whitespace-nowrap") \
                                    .style("width:7.5ch").tooltip(f"Velocity {lo}–{hi}")
                                record_button(take, recorded)
                                compare_button(hits)
                            with ui.column().classes("grow gap-1 min-w-0"):
                                if not recorded and not hits:
                                    with controls_strip():
                                        drop_hint(take)
                                hit_strip(hits, span_s, loudest_db,
                                          [r for r in recs if r.lane == lane and not r.hits and r.envelope])
                            if len(bounds) > 1 and not hits:
                                with controls_strip():
                                    ui.button(icon="close", on_click=lambda n=note, la=lane: remove_lane(n, la)) \
                                        .props("flat dense round size=xs") \
                                        .tooltip(f"Remove the {lane} velocity from this note")
                        drop_target(row, take, note, first.pass_id)
            orphans = by_lane.get(None, [])
            if orphans:
                with ui.row().classes("items-center gap-1 w-full"):
                    ui.label("No velocity:").classes("text-xs opacity-70") \
                        .tooltip("Their velocity was removed from this note; drag them into one, or they're left out")
                    for n, (r, h) in enumerate(orphans, 1):
                        chip(r, h, n)

    def import_card(take: TakeResult) -> None:
        label, color = STATUS[take.status]
        if state["problems_only"] and not is_problem([take]):
            return
        with ui.card().classes("w-full").props("flat bordered"):
            with ui.row().classes("items-center gap-3 w-full"):
                ui.label(take.title).classes("font-medium")
                ui.badge(label, color=color)
                if take.message:
                    ui.label(take.message).classes("text-sm text-orange-600")
            rows: dict[str, list[Hit]] = {}
            for h in take.hits:
                rows.setdefault(h.layer or "hits", []).append(h)
            for layer, hits in rows.items():
                with ui.row().classes("items-center gap-1"):
                    ui.label(layer).classes("w-10 text-xs opacity-70")
                    compare_button([(take, h) for h in hits])
                    for n, h in enumerate(hits, 1):
                        chip(take, h, n)

    @ui.refreshable
    def results() -> None:
        """The list: every note, then the extras."""
        manifest: Manifest | None = state["manifest"]
        overview.refresh()
        noise.refresh()
        counts.refresh()
        if importing:
            if manifest is None:
                ui.label("Nothing imported yet.").classes("opacity-70")
                return
            for take in manifest.takes:
                import_card(take)
            return
        results_by_key = {t.key: t for t in manifest.takes} if manifest else {}
        passes = recording_passes(inst)
        takes = plan_takes(inst)
        for p in passes:
            if len(passes) > 1:
                ui.label(f"Pass: {p.name}").classes("text-base font-medium mt-2")
            notes: dict[str, list[Take]] = {}
            for t in takes:
                if t.pass_id == p.id and t.kind == "note":
                    notes.setdefault(t.note_base, []).append(t)
            for lane_takes in notes.values():
                note_card(lane_takes, results_by_key)
        extras = [t for t in takes if t.kind == "extra"]
        if extras:
            ui.label("Extras").classes("text-sm font-medium mt-2")
            for t in extras:
                take_card(t, results_by_key.get(t.key))

    @ui.refreshable
    def noise() -> None:
        """The noise-floor recordings, before the notes: they're recorded first."""
        if importing:
            return
        results_by_key = {t.key: t for t in state["manifest"].takes} if state["manifest"] else {}
        for t in plan_takes(inst):
            if t.kind == "noise_floor":
                noise_card(t, results_by_key.get(t.key))

    @ui.refreshable
    def counts() -> None:
        manifest: Manifest | None = state["manifest"]
        if manifest is None:
            return
        c = manifest.counts()

        def n(count: int, word: str) -> str:
            return f"{count} {word}" + ("" if count == 1 else "s")

        ui.label(f"{n(c['hits'], 'hit')} · {n(c['errors'], 'error')} · {n(c['warnings'], 'warning')} · "
                 f"{c['rejected']} rejected").classes("text-sm opacity-70")

    def problems_only(value: bool) -> None:
        state["problems_only"] = value
        # After the switch has moved: redrawing at once would cut its animation short.
        with anchor:
            ui.timer(0.25, results.refresh, once=True)

    def pending(manifest: Manifest | None) -> str:
        """Why slicing is needed, or "" if everything on disk is sliced."""
        if importing:
            return ""
        if manifest is None:
            takes_dir = Path(rec.takes_dir).expanduser() if rec.takes_dir else None
            found = takes_dir is not None and takes_dir.is_dir() and any(
                all((takes_dir / f).exists() for f in t.files) for t in slicing.recordings(inst, takes_dir))
            return "Takes in the folder aren't sliced yet." if found else ""
        if manifest.setup != slicing_setup(inst):
            return STALE_MESSAGE
        if manifest.finder != slicing.HIT_FINDER and manifest.source == "takes":
            return FINDER_MESSAGE
        if slicing.sliced_cleanup(inst, manifest) != slicing.cleanup_setup(inst) and manifest.source == "takes":
            return CLEANUP_MESSAGE
        changed = slicing.changed_takes(inst, manifest)
        state["edited"] = []
        if not changed:
            eq_changed = slicing.eq_changed_takes(inst, manifest)
            return eq_message(eq_changed) if eq_changed else ""
        # Edited without moving a hit (e.g. an EQ): cut again where they were, no slicing needed.
        edited = slicing.edited_takes(inst, manifest)
        titles = sorted({t.title for t in manifest.takes if t.key in edited})
        if edited and set(changed) <= set(titles):
            state["edited"] = edited
            names = ", ".join(titles[:3]) + (f" and {len(titles) - 3} more" if len(titles) > 3 else "")
            return (f"Edited since slicing: {names}. Building cuts {'it' if len(titles) == 1 else 'them'} again "
                    "where it was cut and keeps your decisions; Update them does it now, to listen here.")
        return changed_message(changed)

    async def update_edited() -> None:
        """Cut the edited recordings again where they were cut (see slicing.recut)."""
        while state["busy"]:
            await asyncio.sleep(0.1)
        keys = list(state["edited"])
        if not keys or not still_there() or state["manifest"] is None:
            return
        state["busy"] = True
        button.disable()
        progress_box.set_open(True)

        def report(i: int, n: int, title: str) -> None:
            state["progress"], state["label"] = (i / n if n else 1), f"{i}/{n} · {title}"

        try:
            manifest = await run.io_bound(slicing.recut, inst.model_copy(deep=True), state["manifest"], keys, report)
        except Exception as e:  # unexpected I/O or audio errors: show them instead of failing silently
            ui.notify(f"Updating failed: {e}", type="negative", multi_line=True)
        else:
            review.save(manifest, path)
            state["manifest"] = manifest
            state["stale"] = None
            results.refresh()
            ui.notify(f"{len(keys)} edited recording{'' if len(keys) == 1 else 's'} cut again where "
                      f"{'it was' if len(keys) == 1 else 'they were'} cut.", type="positive")
        finally:
            state["busy"] = False
            button.enable()
            progress_box.set_open(False)
            state["label"] = ""

    def recheck() -> None:
        """Notices recordings changed on disk, e.g. edited in another application, while the page is open."""
        if state["busy"] or importing or not still_there():
            return
        now = pending(state["manifest"])
        if now != state["stale"]:
            state["stale"] = now
            overview.refresh()

    @ui.refreshable
    def overview() -> None:
        """What needs slicing again, and the counts; redrawn with the results."""
        manifest: Manifest | None = state["manifest"]
        if state["stale"] is None:  # computing it scans the takes or import folder, so only after changes
            state["stale"] = pending(manifest)
        # Only offered when something needs it: takes recorded here are sliced right away.
        button.set_visibility(importing or bool(state["stale"]))
        if not importing:
            button.set_text("Update them" if state["edited"] else "Slice them")
            button.props(f"icon={'sync' if state['edited'] else 'content_cut'}")
        if state["stale"]:
            ui.label("⚠ " + state["stale"]).classes("text-sm text-orange-600")

    # Buttons and the switch stay out of the redrawn parts: the switch keeps its animation.
    # The message gets a line of its own, however long; below it the button, and the progress bar
    # filling the rest with the take's name on one line of its own, so nothing moves as names change.
    overview_box = ui.column().classes("gap-0 w-full")
    with ui.row().classes("items-center gap-4 w-full no-wrap"):
        button = ui.button("Import & analyse" if importing else "Slice them",
                           icon="file_download" if importing else "content_cut",
                           on_click=lambda: update_edited() if state["edited"] else slice_all()) \
            .classes("shrink-0")
        with Collapse(False, axis="x", inner_classes="flex flex-col gap-1 min-w-0") as progress_box:
            progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
            # w-0 min-w-full: as wide as the bar, never wider, whatever the take is called.
            progress_label = ui.label().classes("text-xs opacity-70 truncate h-4 leading-4 w-0 min-w-full")
        progress_box.outer.classes("grow min-w-48")
    with overview_box:
        overview()
    # The list's own controls, right above it; the noise floor is the list's first recording.
    with ui.row().classes("items-center gap-4 w-full mt-2"):
        ui.label("Recordings").classes("text-base font-medium")
        counts()
        ui.space()
        ui.switch("Show only notes with problems", value=False, on_change=lambda e: problems_only(e.value))
    with ui.column().classes("w-full gap-2"):
        noise()

    def shown() -> str:
        """What the worksheet depends on in the spec; other edits (a name, a colour) don't redraw it."""
        return repr((rec.takes_dir, rec.samples_dir, rec.source, inst.note_lanes, inst.preset.velocity_splits,
                     slicing_setup(inst), slicing.cleanup_setup(inst),
                     [e.model_dump(exclude={"name"}) for e in inst.processing.cleanup_eqs],
                     [(t.key, t.files, t.hits, t.title) for t in plan_takes(inst)]))

    state["shown"] = shown()

    def spec_changed() -> None:
        now = shown()
        if now != state["shown"]:
            state["shown"] = now
            state["stale"] = None
            results.refresh()

    results()
    ed.on_change_hooks.append(spec_changed)
    ui.timer(3.0, recheck)
