"""Wizard step 6: slice the takes, then listen to and accept or reject every hit."""

import io
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import soundfile as sf
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from nicegui import app, run, ui

from vinst import loops, review, slicing, store
from vinst.bundle import STALE_MESSAGE
from vinst.plan import slicing_setup
from vinst.review import FLAGS, Hit, Manifest, TakeResult
from vinst.ui.anim import Collapse
from vinst.ui.finder import can_choose_folder, choose_folder

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


def hit_color(h: Hit) -> str:
    return COLORS["rejected"] if h.rejected else COLORS[h.severity()]


def waveform(take: TakeResult) -> str:
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
            c = hit_color(hit)
            marks.append(f"<rect x='{x0:.1f}' y='0' width='{max(1, x1 - x0):.1f}' height='{h}' fill='{c}' "
                         f"fill-opacity='0.12'><title>{escape(hit.id)}</title></rect>"
                         f"<line x1='{x0:.1f}' x2='{x0:.1f}' y1='0' y2='{h}' stroke='{c}' stroke-width='1.5'/>")
    return (f"<svg viewBox='0 0 {w} {h}' width='100%' height='{h}' preserveAspectRatio='none' "
            f"xmlns='http://www.w3.org/2000/svg'>{''.join(marks)}"
            f"<polygon points='{top} {bottom}' fill='currentColor' fill-opacity='0.55'/></svg>")


def step_review(ed: "Editor") -> None:
    inst = ed.inst
    slug = inst.meta.slug
    path = store.manifest_path(slug)
    state: dict[str, Any] = {"manifest": None, "problems_only": False, "busy": False,
                             "progress": 0.0, "label": "", "stale": None}
    ed.on_change_hooks.append(lambda: state.update(stale=None))  # re-check after any spec change
    try:
        state["manifest"] = review.load(path)
    except Exception as e:  # e.g. written by an older version; slicing again replaces it
        ui.label(f"{path.name} can't be read ({e.__class__.__name__}); slice again to replace it. "
                 "Rejections stored in it are lost.").classes("text-sm text-orange-600")

    importing = inst.recording.source == "samples"
    if not importing:
        ui.markdown("Cuts each take into single hits (all signals at the same positions), checks pitch "
                    "and loudness, and lets you listen to every hit. Rejected hits are left out of the "
                    "instrument. Re-slicing keeps your rejections, unless a take was re-recorded.") \
            .classes("max-w-3xl")
    if importing:
        ui.markdown("Reads a folder of already-cut samples, one note per file (subfolders included). The "
                    "note comes from the file name (“C3”, “F#4”, “055”) and is checked against the measured "
                    "pitch; without a name, the pitch decides. Files are copied under uniform names; the "
                    "folder itself isn't changed.").classes("max-w-3xl")
        with ui.row().classes("w-full items-start"):
            ed.text("Folder with the samples", inst.recording, "import_dir",
                    placeholder="/path/to/samples").classes("grow")
            ui.select({"c3": "Middle C is C3 (Decent Sampler, Cubase)", "c4": "Middle C is C4 (scientific, Logic)"},
                      label="Note names in file names", value=inst.recording.import_note_names,
                      on_change=lambda e: ed.set(inst.recording, "import_note_names", e.value)).classes("w-80") \
                .tooltip("Only used when a sample's pitch can't be measured; otherwise the pitch decides "
                         "which octave a name means.")
    else:
        with ui.row().classes("w-full items-start"):
            ui.input("Takes folder (set in step 5)").bind_value_from(inst.recording, "takes_dir") \
                .props("readonly").classes("grow")

    @ui.refreshable
    def samples_line() -> None:
        folder = review.samples_dir(inst)
        with ui.row().classes("items-center gap-2 text-sm"):
            ui.label("Imported copies go to" if importing else "Sliced samples go to").classes("opacity-70")
            ui.label(str(folder) if folder else "–").classes("font-mono text-xs")
            if can_choose_folder():
                ui.button("Change…", on_click=change_samples_dir).props("flat dense no-caps")
            if inst.recording.samples_dir:
                ui.button("Use default", on_click=lambda: set_samples_dir("")).props("flat dense no-caps")

    def set_samples_dir(value: str) -> None:
        ed.set(inst.recording, "samples_dir", value)
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
        button = ui.button("Import & analyse" if importing else "Slice & analyse",
                           icon="file_download" if importing else "content_cut")
        with Collapse(False, axis="x", inner_classes="flex items-center gap-4") as progress_box:
            progress = ui.linear_progress(value=0, show_value=False).classes("w-64")
            progress_label = ui.label().classes("text-sm opacity-70 whitespace-nowrap")

    def tick() -> None:
        progress.value = state["progress"]
        progress_label.text = state["label"]

    ui.timer(0.25, tick)

    async def slice_all() -> None:
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
            manifest = await run.io_bound(slicing.process, snapshot, state["manifest"], report)
        except ValueError as e:
            ui.notify(str(e), type="warning", multi_line=True)
        except Exception as e:  # unexpected I/O or audio errors: show them instead of failing silently
            ui.notify(f"Slicing failed: {e}", type="negative", multi_line=True)
        else:
            review.save(manifest, path)
            state["manifest"] = manifest
            state["stale"] = False
            results.refresh()
            ed.refresh()  # the summary shows what was imported
            c = manifest.counts()
            ui.notify(f"{c['hits']} hits from {c['takes_ok']} takes; {c['errors']} errors, "
                      f"{c['warnings']} warnings", type="positive")
        finally:
            state["busy"] = False
            button.enable()
            progress_box.set_open(False)
            state["label"] = ""

    button.on_click(slice_all)

    def toggle_reject(hit: Hit, value: bool) -> None:
        hit.rejected = value
        review.save(state["manifest"], path)
        results.refresh()

    def toggle_loop(hit: Hit, use: bool) -> None:
        hit.loop_off = not use
        review.save(state["manifest"], path)
        results.refresh()

    # One dialog for the whole step, created outside the refreshable results so that
    # refreshing them (e.g. after a reject) doesn't delete the open dialog.
    with ui.dialog() as dialog, ui.card().classes("min-w-[28rem]") as dialog_card:
        pass

    def open_hit(take: TakeResult, hit: Hit) -> None:
        dialog_card.clear()
        with dialog_card:
            ui.label(f"{take.title}").classes("text-lg font-medium")
            ui.label(hit.id).classes("text-xs font-mono opacity-70")
            facts = [("Layer / round robin", f"{hit.layer or '–'} / {hit.rr or '–'}"),
                     ("Length", f"{(hit.end - hit.start) / (hit.sample_rate or take.sample_rate or 1):.2f} s"),
                     ("Loudness (first 0.3 s)", f"{hit.rms_db:.1f} dBFS")]
            if hit.cents is not None:
                facts.append(("Tuning", f"{hit.cents:+.1f} cents"))
            facts += [(f"Peak {sig}", f"{p:.1f} dBFS") for sig, p in hit.peak_db.items()]
            with ui.grid(columns=2).classes("gap-x-6 gap-y-1 text-sm"):
                for k, v in facts:
                    ui.label(k).classes("opacity-70")
                    ui.label(v)
            for f in hit.flags:
                sev, text = FLAGS.get(f, ("info", f))
                ui.label(f"● {f.replace('_', ' ')}: {text}").style(f"color:{COLORS[sev]}").classes("text-sm")
            folder = Path(state["manifest"].samples_dir)
            for i, (sig, name) in enumerate(hit.files.items()):
                mtime = int((folder / name).stat().st_mtime) if (folder / name).exists() else 0
                ui.label(sig).classes("text-sm font-medium mt-2")
                ui.audio(f"/audio/{slug}/{quote(name)}?v={mtime}", autoplay=(i == 0)).classes("w-full")
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
                    ui.switch("Rejected", value=hit.rejected,
                              on_change=lambda e: toggle_reject(hit, e.value))
                    if hit.loop_start is not None:
                        ui.switch("Use loop", value=not hit.loop_off,
                                  on_change=lambda e: toggle_loop(hit, e.value))
                ui.button("Close", on_click=dialog.close).props("flat")
        dialog.open()

    @ui.refreshable
    def results() -> None:
        manifest: Manifest | None = state["manifest"]
        if manifest is None:
            ui.label("Nothing sliced yet.").classes("opacity-70")
            return
        if state["stale"] is None:  # computing it scans the import folder, so only after changes
            state["stale"] = manifest.setup != slicing_setup(inst)
        if state["stale"]:
            ui.label("⚠ " + STALE_MESSAGE).classes("text-sm text-orange-600")
        c = manifest.counts()
        with ui.row().classes("items-center gap-4 w-full"):
            ui.label(f"{c['takes_ok']}/{c['takes']} takes OK · {c['takes_missing']} not recorded · "
                     f"{c['takes_problem']} with problems · {c['hits']} hits · {c['errors']} errors · "
                     f"{c['warnings']} warnings · {c['rejected']} rejected").classes("text-sm")
            ui.switch("Only problems", value=state["problems_only"],
                      on_change=lambda e: (state.update(problems_only=e.value), results.refresh()))
        if manifest.noise_floor_db is not None:
            denoised = " · samples denoised" if manifest.denoised else ""
            ui.label(f"Noise floor: {manifest.noise_floor_db:.1f} dBFS{denoised}").classes("text-xs opacity-60")
        for take in manifest.takes:
            problem = take.status in ("count_mismatch", "error") or (
                bool(take.message) and take.status == "ok") or any(
                h.severity() in ("error", "warning") and not h.rejected for h in take.hits)
            if state["problems_only"] and not problem:
                continue
            take_card(take)

    def take_card(take: TakeResult) -> None:
        label, color = STATUS[take.status]
        with ui.card().classes("w-full").props("flat bordered"):
            with ui.row().classes("items-center gap-3 w-full"):
                ui.label(f"{take.number}").classes("opacity-50 w-6 text-right")
                ui.label(take.title).classes("font-medium")
                ui.badge(label, color=color)
                if take.message:
                    ui.label(take.message).classes(
                        "text-sm opacity-70" if take.status == "noise_floor" else "text-sm text-orange-600")
            if take.envelope:
                ui.html(waveform(take), sanitize=False).classes("w-full")
            rows: dict[str, list[Hit]] = {}
            for h in take.hits:
                rows.setdefault(h.layer or "hits", []).append(h)
            for layer, hits in rows.items():
                with ui.row().classes("items-center gap-1"):
                    ui.label(layer).classes("w-10 text-xs opacity-70")
                    for h in hits:
                        text = f"rr{h.rr}" if h.rr else f"{h.index + 1}"
                        if h.cents is not None:
                            text += f" {h.cents:+.0f}¢"
                        tips = [f.replace("_", " ") for f in h.flags]
                        if h.loop_start is not None:
                            # ⟳ loops, ⟳M uses a marker from the take, a crossed-out ⟳ was turned off.
                            text += " ⟳̸" if h.loop_off else (" ⟳M" if h.loop_source == "marker" else " ⟳")
                            tips.append("loop turned off" if h.loop_off else
                                        "loop from a marker" if h.loop_source == "marker" else
                                        f"automatic loop, match {h.loop_score:.2f}")
                        chip = ui.chip(text, color=None, on_click=lambda t=take, hit=h: open_hit(t, hit)) \
                            .props("dense square clickable").style(
                                f"background:{hit_color(h)};color:white"
                                + (";text-decoration:line-through" if h.rejected else ""))
                        if tips:
                            chip.tooltip(", ".join(tips))

    results()
