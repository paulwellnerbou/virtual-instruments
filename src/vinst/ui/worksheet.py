"""Wizard step 5, the worksheet: every note with its velocity lanes, recorded, sliced and reviewed.

Each lane is its own take. Recording one in the app slices it right away; takes exported from a DAW
are picked up with "Slice all". Hits can be dragged between a note's lanes, and a note can get its
own lanes and velocity ranges instead of the defaults from Dynamics.
"""

import io
import json
from html import escape
from pathlib import Path
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import numpy as np
import soundfile as sf
from fastapi import HTTPException, Query
from fastapi.responses import FileResponse, Response
from nicegui import app, run, ui

from vinst import loops, review, slicing, store
from vinst.bundle import STALE_MESSAGE, changed_message
from vinst.plan import SAFE_LANE, Take, lane_bounds, note_lanes, plan_takes, recording_passes, slicing_setup
from vinst.review import FLAGS, Hit, Manifest, TakeResult, check_lanes, lane_of, settle
from vinst.spec import NoteLanes
from vinst.ui import recording
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


# One player for the whole page: a second click on the same button stops it.
PLAY_JS = """(url) => {
  const a = window.viPlayer || (window.viPlayer = new Audio());
  if (!a.paused && a.dataset.url === url) { a.pause(); return; }
  a.src = url; a.dataset.url = url; a.play();
}"""


def hit_color(h: Hit) -> str:
    return COLORS["rejected"] if h.rejected else COLORS[h.severity()]


def waveform(take: TakeResult, highlight: str | None = None) -> str:
    env = take.envelope
    if not env:
        return ""
    w, h = 800, 60
    n = len(env)
    top = " ".join(f"{i * w / (n - 1):.1f},{h / 2 - v * h / 2:.1f}" for i, v in enumerate(env))
    bottom = " ".join(f"{i * w / (n - 1):.1f},{h / 2 + v * h / 2:.1f}" for i, v in reversed(list(enumerate(env))))
    marks = []
    if take.length:
        for hit in take.hits:
            x0, x1 = hit.start / take.length * w, hit.end / take.length * w
            c = hit_color(hit) if highlight in (None, hit.id) else COLORS["rejected"]
            marks.append(f"<rect x='{x0:.1f}' y='0' width='{max(1, x1 - x0):.1f}' height='{h}' fill='{c}' "
                         f"fill-opacity='0.12'><title>{escape(hit.id)}</title></rect>"
                         f"<line x1='{x0:.1f}' x2='{x0:.1f}' y1='0' y2='{h}' stroke='{c}' stroke-width='1.5'/>")
    return (f"<svg viewBox='0 0 {w} {h}' width='100%' height='{h}' preserveAspectRatio='none' "
            f"xmlns='http://www.w3.org/2000/svg'>{''.join(marks)}"
            f"<polygon points='{top} {bottom}' fill='currentColor' fill-opacity='0.55'/></svg>")


CSS = """
.vi-lane { border-radius: 6px; padding: 2px 4px; transition: background-color .15s; }
.vi-lane.vi-drop { background: rgba(25, 118, 210, .12); outline: 2px dashed #1976d2; }
.vi-chip-drag { cursor: grab; }
"""
DRAG_JS = "(e) => e.dataTransfer.setData('text/plain', {ref})"
DRAG_OVER_JS = "(e) => { e.preventDefault(); e.currentTarget.classList.add('vi-drop'); }"
DRAG_LEAVE_JS = "(e) => e.currentTarget.classList.remove('vi-drop')"
DROP_JS = ("(e) => { e.preventDefault(); e.currentTarget.classList.remove('vi-drop'); "
           "emit(e.dataTransfer.getData('text/plain')); }")
SUGGESTED_LANES = ["pp", "p", "mp", "mf", "f", "ff"]


def step_worksheet(ed: "Editor") -> None:
    inst = ed.inst
    slug = inst.meta.slug
    path = store.manifest_path(slug)
    state: dict[str, Any] = {"manifest": None, "problems_only": False, "busy": False,
                             "progress": 0.0, "label": "", "stale": None}
    ui.add_css(CSS)
    try:
        state["manifest"] = review.load(path)
    except Exception as e:  # e.g. written by an older version; slicing again replaces it
        ui.label(f"{path.name} can't be read ({e.__class__.__name__}); slice again to replace it. "
                 "Rejections stored in it are lost.").classes("text-sm text-orange-600")

    importing = inst.recording.source == "samples"
    rec = inst.recording
    if importing:
        ui.markdown("Reads a folder of already-cut samples, one note per file (subfolders included). The "
                    "note comes from the file name (“C3”, “F#4”, “055”) and is checked against the measured "
                    "pitch; without a name, the pitch decides. Files are copied under uniform names; the "
                    "folder itself isn't changed.").classes("max-w-3xl")
        with ui.row().classes("w-full items-start"):
            ed.text("Folder with the samples", rec, "import_dir", placeholder="/path/to/samples").classes("grow")
            ui.select({"c3": "Middle C is C3 (Decent Sampler, Cubase)", "c4": "Middle C is C4 (scientific, Logic)"},
                      label="Note names in file names", value=rec.import_note_names,
                      on_change=lambda e: ed.set(rec, "import_note_names", e.value)).classes("w-80") \
                .tooltip("Only used when a sample's pitch can't be measured; otherwise the pitch decides "
                         "which octave a name means.")
    else:
        ui.markdown("Every note is recorded once per **velocity lane**. Record a lane with ● (or export it "
                    "from a DAW under the file name shown) and its hits appear right away. Listen to them, "
                    "reject the ones you don't want, drag hits to another lane, and give single notes their "
                    "own lanes or velocity ranges.").classes("max-w-3xl")
        with ui.row().classes("w-full items-center no-wrap gap-1"):
            takes = ed.folder("Folder for the takes", rec, "takes_dir", "Folder for the takes",
                              placeholder=store.default_takes_dir(inst.meta.name))

            def show_takes() -> None:
                folder = Path(rec.takes_dir or store.default_takes_dir(inst.meta.name)).expanduser()
                try:
                    ensure_folder(folder)
                except OSError as e:
                    ui.notify(str(e), type="warning")
                    return
                if not rec.takes_dir:
                    takes.set_value(str(folder))
                reveal(folder)

            ui.button("Show in Finder", icon="folder_open", on_click=show_takes).props("flat dense")
        with ui.row().classes("items-center"):
            ui.select([44100, 48000, 88200, 96000], label="Sample rate", value=rec.sample_rate,
                      on_change=lambda e: ed.set(rec, "sample_rate", e.value)).classes("w-32")
            ui.select([16, 24, 32], label="Bit depth", value=rec.bit_depth,
                      on_change=lambda e: ed.set(rec, "bit_depth", e.value)).classes("w-28")
        recording.input_settings(ed)

    @ui.refreshable
    def samples_line() -> None:
        folder = review.samples_dir(inst)
        with ui.row().classes("items-center gap-2 text-sm"):
            ui.label("Imported copies go to" if importing else "Sliced samples go to").classes("opacity-70")
            ui.label(str(folder) if folder else "–").classes("font-mono text-xs")
            if can_choose_folder():
                ui.button("Change…", on_click=change_samples_dir).props("flat dense no-caps")
            if rec.samples_dir:
                ui.button("Use default", on_click=lambda: set_samples_dir("")).props("flat dense no-caps")

    def set_samples_dir(value: str) -> None:
        ed.set(rec, "samples_dir", value)
        samples_line.refresh()

    async def change_samples_dir() -> None:
        current = review.samples_dir(inst)
        picked = await run.io_bound(choose_folder, "Folder for the sliced samples", str(current or ""))
        if picked:
            set_samples_dir(picked)

    samples_line()

    proc = inst.processing
    if not importing:  # imports have no noise-floor recording to learn from
        with ui.row().classes("items-center w-full gap-4"):
            ui.switch("Denoise the samples", value=proc.denoise,
                      on_change=lambda e: (ed.set(proc, "denoise", e.value), reduction.set_open(e.value)))
            with Collapse(proc.denoise, axis="x") as reduction:
                ed.number("Max. reduction", proc, "denoise_reduction_db", cast=float, min=1, max=40,
                          step=1, suffix="dB").classes("w-36")
            ui.label("Uses the noise-floor recording as the noise profile and only changes the sliced "
                     "samples, never your takes. Leave it off if you already cleaned the takes, e.g. in "
                     "WaveLab or Cubase.").classes("text-xs opacity-70 max-w-xl")
    with ui.row().classes("items-center w-full gap-4"):
        ui.switch("Find loops", value=proc.loops,
                  on_change=lambda e: (ed.set(proc, "loops", e.value), crossfade.set_open(e.value)))
        with Collapse(proc.loops, axis="x") as crossfade:
            ed.number("Loop crossfade", proc, "loop_crossfade_ms", cast=float, min=0, max=500,
                      step=5, suffix="ms").classes("w-36")
        ui.label("For sustained sounds: notes keep playing while the key is held. Loop markers in the "
                 "takes (e.g. set in WaveLab) are used where they exist; otherwise a loop is searched "
                 "in the steady part of each note.").classes("text-xs opacity-70 max-w-xl")

    with ui.row().classes("items-center w-full"):
        button = ui.button("Import & analyse" if importing else "Slice all",
                           icon="file_download" if importing else "content_cut")
        if not importing:
            ui.label("Only needed for takes exported from a DAW; takes recorded here are sliced right away.") \
                .classes("text-xs opacity-70")
        with Collapse(False, axis="x", inner_classes="flex items-center gap-4") as progress_box:
            progress = ui.linear_progress(value=0, show_value=False).classes("w-64")
            progress_label = ui.label().classes("text-sm opacity-70 whitespace-nowrap")

    def tick() -> None:
        progress.value = state["progress"]
        progress_label.text = state["label"]

    ui.timer(0.25, tick)

    async def slice_all(only: str | None = None) -> None:
        """Slice everything, or with `only` just that recording (right after recording it)."""
        if state["busy"]:
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

    button.on_click(lambda: slice_all())

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

    def saved(take: TakeResult) -> None:
        """After a decision: lanes and loudness checks again, save, redraw."""
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
            ui.notify("Hits can only move between the lanes of their own note.", type="warning")
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
            ui.label(f"{take.title}").classes("text-lg font-medium")
            ui.label(hit.id).classes("text-xs font-mono opacity-70")
            lanes = note_lanes(inst, take.note) if take.note else []
            lane = lane_of(hit, take, lanes) if take.note and not importing else hit.layer
            facts = [("Lane", lane or "–"),
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
                ui.label(f"Where it was cut in {take.title}").classes("text-xs opacity-60")
                ui.html(waveform(take, hit.id), sanitize=False).classes("w-full")
            if take.kind == "note" and not importing and len(lanes) > 1:
                ui.select({la: la for la in lanes}, value=lane, label="Move to lane",
                          on_change=lambda e: choose_lane(take, hit, e.value)).props("dense").classes("w-40")
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
                            .tooltip("Records one more hit for this lane; this one is rejected once it's saved")
                    ui.button("Close", on_click=dialog.close).props("flat")
        dialog.open()

    # Per-note lanes: changing them gives the note its own lanes instead of the defaults.
    def set_lanes(note: str, lanes: list[str], starts: list[int]) -> None:
        inst.note_lanes[note] = NoteLanes(layers=lanes, splits=starts)
        ed.changed()

    def set_start(note: str, index: int, value: Any) -> None:
        bounds = lane_bounds(inst, note)
        starts = [lo for _, lo, _ in bounds][1:]
        try:
            starts[index - 1] = int(value)
        except (TypeError, ValueError):
            return
        for k in range(index, len(starts)):  # keep the lanes in order, each at least one velocity wide
            starts[k] = max(starts[k], starts[k - 1] + 1) if k else starts[k]
        for k in range(index - 2, -1, -1):
            starts[k] = min(starts[k], starts[k + 1] - 1)
        starts = [min(max(v, 2 + k), 127 - (len(starts) - 1 - k)) for k, v in enumerate(starts)]
        set_lanes(note, [lane for lane, _, _ in bounds], starts)

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
            ui.label(f"A new lane for {title}").classes("text-lg font-medium")
            suggestion = next((s for s in SUGGESTED_LANES[::-1] if s not in existing), "")
            name = ui.input("Name", value=suggestion).props("dense autofocus")
            start = ui.number("Starts at velocity", value=min(127, bounds[-1][1] - 10 if bounds else 64),
                              min=2, max=127, precision=0).props("dense")
            ui.label("Only this note gets it; the defaults in Dynamics stay as they are. The lanes stay "
                     "ordered by where they start.").classes("text-xs opacity-70")

            def add() -> None:
                lane, at = (name.value or "").strip(), int(start.value or 0)
                if not SAFE_LANE.fullmatch(lane) or lane.lower() in {e.lower() for e in existing}:
                    ui.notify("Use a new name with letters, digits and “-” only.", type="warning")
                    return
                if at <= 1 or at in {lo for _, lo, _ in bounds}:
                    ui.notify(f"Another lane already starts at velocity {at}; choose another.", type="warning")
                    return
                entries = sorted([(lo, n) for n, lo, _ in bounds if n] + [(at, lane)])
                entries = [(max(1, lo), n) for lo, n in entries]
                lanes = [n for _, n in entries]
                starts = sorted({lo for lo, _ in entries[1:]} - {1})
                lane_dialog.close()
                set_lanes(note, lanes, starts if len(starts) == len(lanes) - 1 else [])

            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=lane_dialog.close).props("flat")
                ui.button("Add lane", on_click=add)
        lane_dialog.open()

    def chip(take: TakeResult, h: Hit, number: int) -> None:
        text = f"{number}"
        if h.cents is not None:
            text += f" {h.cents:+.0f}¢"
        tips = [f.replace("_", " ") for f in h.flags]
        if take.origin == "more":
            tips.append("recorded later")
        elif take.origin == "combined":
            tips.append("from the take with all lanes")
        if h.layer_set:
            tips.append("moved here")
        if h.loop_start is not None:
            # ⟳ loops, ⟳M uses a marker from the take, a crossed-out ⟳ was turned off.
            text += " ⟳̸" if h.loop_off else (" ⟳M" if h.loop_source == "marker" else " ⟳")
            tips.append("loop turned off" if h.loop_off else "loop from a marker" if h.loop_source == "marker"
                        else f"automatic loop, match {h.loop_score:.2f}")
        c = ui.chip(text, color=None, on_click=lambda t=take, hit=h: open_hit(t, hit)) \
            .props("dense square clickable draggable").classes("vi-chip-drag").style(
                f"background:{hit_color(h)};color:white" + (";text-decoration:line-through" if h.rejected else ""))
        c.on("dragstart", js_handler=DRAG_JS.format(ref=json.dumps(f"{take.key}|{h.id}")))
        if tips:
            c.tooltip(", ".join(tips))

    def compare_button(hits: list[tuple[TakeResult, Hit]]) -> None:
        kept = [h for _, h in hits if not h.rejected]  # rejected ones are decided; compare the rest
        if len(kept) < 2:
            return
        names = "&".join(f"names={quote(next(iter(h.files.values())))}" for h in kept)
        url = f"/compare/{slug}?{names}"
        ui.button(icon="play_arrow").props("flat dense round size=sm") \
            .on("click", js_handler=f"() => ({PLAY_JS})({json.dumps(url)})") \
            .tooltip(f"Play the {len(kept)} kept ones with their attacks {COMPARE_EVERY_S:g} s apart "
                     "(click again to stop)")

    def is_problem(recs: list[TakeResult]) -> bool:
        return any(t.status == "error" or t.message or any(h.severity() in ("error", "warning") and not h.rejected
                                                             for h in t.hits) for t in recs)

    def take_row(take: Take, result: TakeResult | None) -> None:
        """The noise floor or an extra: one take, recorded as a whole."""
        recorded = result is not None and result.status not in ("missing",)
        with ui.row().classes("items-center gap-2 w-full"):
            ui.label(take.title).classes("font-medium w-56")
            record_button(take, recorded)
            if result and result.status == "noise_floor":
                ui.label(f"recorded · {result.message}").classes("text-sm opacity-70")
            elif result:
                for i, h in enumerate(result.hits, 1):
                    chip(result, h, i)
                compare_button([(result, h) for h in result.hits])
            if not recorded:
                ui.label(f"or export {', '.join(take.files)}").classes("text-xs font-mono opacity-50")
            if result and (result.message or result.info) and result.status != "noise_floor":
                ui.label("; ".join(filter(None, [result.message, result.info]))).classes("text-xs opacity-70")

    def record_button(take: Take, recorded: bool) -> None:
        ui.button(icon="check_circle" if recorded else "fiber_manual_record", color=None,
                  on_click=lambda t=take: recording.record_take(ed, t.number, resliced)) \
            .props(f"flat dense round size=sm text-color={'positive' if recorded else 'red'}") \
            .tooltip("Recorded; click to record it again (replaces it)" if recorded else "Record this lane")

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
        with ui.card().classes("w-full").props("flat bordered"):
            with ui.row().classes("items-center gap-2 w-full"):
                ui.label(title).classes("font-medium")
                if own:
                    ui.badge("own lanes", color="primary").props("outline")
                ui.space()
                ui.button("+ lane", on_click=lambda n=note, t=title: add_lane(n, t)).props("flat dense no-caps")
                if own:
                    ui.button("Use default lanes", on_click=lambda n=note: use_defaults(n)).props("flat dense no-caps")
            notes_text = "; ".join(filter(None, [m for r in recs for m in (r.message, r.info)]))
            if notes_text:
                ui.label(notes_text).classes("text-xs text-orange-600")
            bounds = lane_bounds(inst, note)
            for i, ((lane, lo, hi), take) in enumerate(zip(bounds, lane_takes)):
                result = results_by_key.get(take.key)
                recorded = result is not None and result.status != "missing"
                hits = by_lane.get(lane, [])
                with ui.row().classes("vi-lane items-center gap-1 w-full no-wrap min-h-[44px]") as row:
                    ui.label(lane).classes("w-10 font-medium")
                    if i == 0:
                        ui.label(f"1 – {hi}").classes("w-28 text-xs opacity-70 whitespace-nowrap")
                    else:
                        with ui.row().classes("w-28 items-center gap-1 no-wrap"):
                            # Applied on Enter or leaving the field: redrawing while typing would lose focus.
                            start = ui.number(value=lo, min=2, max=127, precision=0) \
                                .props("dense input-class=text-xs").classes("w-16") \
                                .tooltip(f"Velocity where {lane} starts for this note")
                            # The typed text comes with the event: the field's value may not have reached
                            # the server yet when it loses focus.
                            for event in ("blur", "keydown.enter"):
                                start.on(event, js_handler="(e) => emit((e.target || {}).value)",
                                         handler=lambda e, n=note, k=i, old=lo:
                                         e.args not in (None, "", str(old)) and set_start(n, k, e.args))
                            ui.label(f"– {hi}").classes("text-xs opacity-70 whitespace-nowrap")
                    record_button(take, recorded)
                    ui.button(icon="add", color=None,
                              on_click=lambda t=take: recording.record_more(ed, t.key, inst.dynamics.round_robins,
                                                                             resliced)) \
                        .props("flat dense round size=sm text-color=primary") \
                        .tooltip(f"Record {inst.dynamics.round_robins} more {lane} hits to compare with these")
                    compare_button(hits)
                    with ui.row().classes("gap-1 items-center"):
                        for n, (r, h) in enumerate(hits, 1):
                            chip(r, h, n)
                    if not recorded and not hits:
                        ui.label(f"or export {', '.join(take.files)}").classes("text-xs font-mono opacity-50")
                    ui.space()
                    if len(bounds) > 1 and not hits:
                        ui.button(icon="close", on_click=lambda n=note, la=lane: remove_lane(n, la)) \
                            .props("flat dense round size=xs").tooltip(f"Remove the {lane} lane from this note")
                row.on("dragover", js_handler=DRAG_OVER_JS)
                row.on("dragleave", js_handler=DRAG_LEAVE_JS)
                row.on("drop", js_handler=DROP_JS,
                       handler=lambda e, la=lane, n=note, ps=first.pass_id: move(e.args, la, n, ps))
            orphans = by_lane.get(None, [])
            if orphans:
                with ui.row().classes("items-center gap-1 w-full"):
                    ui.label("No lane:").classes("text-xs opacity-70")
                    for n, (r, h) in enumerate(orphans, 1):
                        chip(r, h, n)
                    ui.label("their lane was removed from this note; drag them into one, or they're left out") \
                        .classes("text-xs opacity-60")

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
        manifest: Manifest | None = state["manifest"]
        if manifest is not None:
            if state["stale"] is None:  # computing it scans the takes or import folder, so only after changes
                changed = slicing.changed_takes(inst, manifest)
                state["stale"] = STALE_MESSAGE if manifest.setup != slicing_setup(inst) else \
                    changed_message(changed) if changed else ""
            if state["stale"]:
                ui.label("⚠ " + state["stale"]).classes("text-sm text-orange-600")
            c = manifest.counts()
            with ui.row().classes("items-center gap-4 w-full"):
                ui.label(f"{c['hits']} hits · {c['errors']} errors · {c['warnings']} warnings · "
                         f"{c['rejected']} rejected").classes("text-sm")
                ui.switch("Only problems", value=state["problems_only"],
                          on_change=lambda e: (state.update(problems_only=e.value), results.refresh()))
            if manifest.noise_floor_db is not None:
                denoised = " · samples denoised" if manifest.denoised else ""
                ui.label(f"Noise floor: {manifest.noise_floor_db:.1f} dBFS{denoised}").classes("text-xs opacity-60")
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
            pass_takes = [t for t in takes if t.pass_id == p.id]
            for t in pass_takes:
                if t.kind == "noise_floor":
                    take_row(t, results_by_key.get(t.key))
            notes: dict[str, list[Take]] = {}
            for t in pass_takes:
                if t.kind == "note":
                    notes.setdefault(t.note_base, []).append(t)
            for lane_takes in notes.values():
                note_card(lane_takes, results_by_key)
            extras = [t for t in pass_takes if t.kind == "extra"]
            if extras:
                ui.label("Extras").classes("text-sm font-medium mt-2")
                for t in extras:
                    take_row(t, results_by_key.get(t.key))

    def shown() -> str:
        """What the worksheet depends on in the spec; other edits (a name, a colour) don't redraw it."""
        return repr((rec.takes_dir, rec.samples_dir, rec.source, inst.note_lanes, inst.preset.velocity_splits,
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
