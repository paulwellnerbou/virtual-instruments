"""Wizard steps 7 (sound & look) and 8 (build, install, package)."""

from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.responses import FileResponse
from nicegui import app, run, ui

from vinst import bundle, preset, store
from vinst.plan import layer_bounds, recording_passes
from vinst.preset import KNOB_Y, UI_H, UI_W, VISIBLE_H, knob_layout, mixable_signals, switched_passes
from vinst.spec import CONTROLS, Extra
from vinst.ui.anim import Collapse
from vinst.ui.finder import reveal

if TYPE_CHECKING:
    from vinst.ui.app import Editor

PREVIEW_SCALE = 0.75


# slug -> image path as currently typed in an editor; the preview refreshes before autosave
# has written it to the spec file.
_backgrounds: dict[str, Path] = {}


@app.get("/background/{slug}")
def background_image(slug: str) -> FileResponse:
    path = _backgrounds.get(slug) or Path(store.load(slug).preset.background).expanduser()
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


def _argb_to_css(argb: str) -> str:
    try:
        a, rgb = int(argb[:2], 16) / 255, argb[2:]
        return f"rgba({int(rgb[0:2], 16)},{int(rgb[2:4], 16)},{int(rgb[4:6], 16)},{a:.2f})"
    except ValueError:
        return "transparent"


def preview_html(ed: "Editor") -> str:
    """Approximation of the DecentSampler window: background, title and knob row."""
    inst = ed.inst
    p = inst.preset
    labels = []
    has_release = any(x.use == "release" for x in inst.extras)
    mixable = mixable_signals(inst)
    for c in p.controls:
        if c == "mix" and len(mixable) == 2:
            labels.append("Mix")
        elif c == "mix" and len(mixable) > 2:
            labels += [s.name for s in mixable]
        elif c == "noise" and has_release:
            labels.append("Noise")
        elif c not in ("mix", "noise"):
            labels.append(CONTROLS[c])
    s = PREVIEW_SCALE
    text = _argb_to_css(p.text_color)
    bg = f"background:{_argb_to_css(p.bg_color)};"
    if p.background and Path(p.background).expanduser().is_file():
        _backgrounds[inst.meta.slug] = Path(p.background).expanduser()
        mtime = int(Path(p.background).expanduser().stat().st_mtime)
        bg += f"background-image:url(/background/{inst.meta.slug}?v={mtime});background-size:{UI_W * s}px {UI_H * s}px;"
    switched = switched_passes(inst)
    menu = (f"<div style='position:absolute;left:{20 * s}px;top:{58 * s}px;width:{240 * s}px;"
            f"height:{26 * s}px;border:1px solid {text};color:{text};font-size:{12 * s + 2}px;"
            f"padding-left:6px;line-height:{26 * s}px'>{escape(switched[0].name)} ▾</div>") if switched else ""
    x0, step, size = knob_layout(len(labels))
    knobs = "".join(
        f"<div style='position:absolute;left:{(x0 + i * step) * s}px;top:{KNOB_Y * s}px;"
        f"width:{size * s}px;text-align:center;color:{text};font-size:{11 * s + 2}px'>"
        f"<div style='margin:0 auto 4px;width:{46 * s}px;height:{46 * s}px;border-radius:50%;"
        f"border:3px solid {text}'></div>{escape(label)}</div>"
        for i, label in enumerate(labels))
    return (f"<div style='position:relative;width:{UI_W * s}px;height:{UI_H * s}px;{bg}border-radius:6px;"
            f"overflow:hidden;font-family:sans-serif'>"
            f"<div style='position:absolute;left:{20 * s}px;top:{14 * s}px;color:{text};font-size:{26 * s}px'>"
            f"{escape(inst.meta.name)}</div>{menu}{knobs}"
            # The part DecentSampler covers with its on-screen keyboard.
            f"<div style='position:absolute;left:0;top:{VISIBLE_H * s}px;width:100%;bottom:0;"
            f"background:repeating-linear-gradient(90deg,#f4f4f4 0 {14 * s}px,#999 {14 * s}px {15 * s}px);"
            f"opacity:.9'></div></div>")


LAYER_COLOURS = ["#90caf9", "#42a5f5", "#1e88e5", "#1565c0", "#0d47a1"]


def velocity_layers(ed: "Editor") -> None:
    """Where each recorded dynamic takes over, e.g. when f kicks in, as velocity boundaries.

    Rebuilt when the layers change in step 4, so there is always one slider per boundary.
    """
    inst, p = ed.inst, ed.inst.preset
    shown = {"layers": list(inst.dynamics.layers)}

    @ui.refreshable
    def section() -> None:
        layers = list(inst.dynamics.layers)
        shown["layers"] = layers
        if len(layers) < 2:
            return

        def starts() -> list[int]:
            return [lo for lo, _ in layer_bounds(len(layers), p.velocity_splits)][1:]

        def moved(i: int, value: int) -> None:
            values = starts()
            values[i] = int(value)
            # Keep the layers in order and at least one velocity wide.
            for k in range(i + 1, len(values)):
                values[k] = max(values[k], values[k - 1] + 1)
            for k in range(i - 1, -1, -1):
                values[k] = min(values[k], values[k + 1] - 1)
            values = [min(max(v, 2 + k), 127 - (len(values) - 1 - k)) for k, v in enumerate(values)]
            ed.set(p, "velocity_splits", values)
            show()

        def even() -> None:
            ed.set(p, "velocity_splits", [])
            show()

        def show() -> None:
            for slider, value in zip(sliders, starts()):
                slider.set_value(value)
            bar.refresh()

        ui.label("Where each velocity starts").classes("text-sm font-medium mt-2")
        sliders: list[ui.slider] = []
        with ui.column().classes("gap-1 w-full max-w-3xl"):
            for i, layer in enumerate(layers[1:]):
                with ui.row().classes("items-center gap-3 w-full no-wrap"):
                    ui.label(f"{layer} starts at velocity").classes("whitespace-nowrap w-44")
                    slider = ui.slider(min=2, max=127, step=1, value=starts()[i]).props("label").classes("grow")
                    slider.on("change", lambda e, k=i: moved(k, e.args))
                    sliders.append(slider)
                    ui.label().bind_text_from(slider, "value", lambda v: f"{int(v)}").classes("w-8 text-right")

            @ui.refreshable
            def bar() -> None:
                bounds = layer_bounds(len(layers), p.velocity_splits)
                with ui.row().classes("w-full gap-0 no-wrap rounded overflow-hidden text-xs text-white"):
                    for k, (layer, (lo, hi)) in enumerate(zip(layers, bounds)):
                        ui.label(f"{layer} {lo}–{hi}").classes("text-center py-1 overflow-hidden whitespace-nowrap") \
                            .style(f"width:{(hi - lo + 1) / 127 * 100:.2f}%;"
                                   f"background:{LAYER_COLOURS[k % len(LAYER_COLOURS)]};transition:width .2s ease")

            bar()
            with ui.row().classes("items-center gap-2"):
                ui.button("Even split", on_click=even).props("flat dense")
                ui.label("Play your keyboard softly and firmly in DecentSampler to find where the louder "
                         "samples should take over. Changes need a new build, never new slicing.").classes("text-xs opacity-70")

    section()
    ed.on_change_hooks.append(lambda: shown["layers"] != list(inst.dynamics.layers) and section.refresh())


def step_sound(ed: "Editor") -> None:
    inst, p = ed.inst, ed.inst.preset

    ui.label("Tuning").classes("text-base font-medium")
    with ui.row().classes("items-center gap-4"):
        def tuning_changed(e: Any) -> None:
            ed.set(p, "tuning", e.value)
            partial.set_open(e.value == "partial")

        ui.toggle({"off": "Off", "partial": "Partial", "full": "Full"}, value=p.tuning, on_change=tuning_changed)
        with Collapse(p.tuning == "partial", axis="x", inner_classes="flex items-center gap-4 pr-2") as partial:
            ui.slider(min=0, max=1, step=0.05, value=p.tuning_amount,
                      on_change=lambda e: ed.set(p, "tuning_amount", e.value)).classes("w-48")
            ui.label().bind_text_from(p, "tuning_amount", lambda v: f"corrects {v:.0%}").classes("whitespace-nowrap")
    ui.label("Uses the cents measured in step 6. Partial keeps some natural detune.").classes("text-xs opacity-70")

    if len(recording_passes(inst)) > 1:
        ui.separator()
        ui.label("Recording passes").classes("text-base font-medium")
        ui.toggle({"switch": "Switch (menu in the instrument)", "layer": "Layer (play together)"},
                  value=p.pass_mode, on_change=lambda e: ed.set(p, "pass_mode", e.value))
        ui.label("Passes are separate performances. Switch plays one at a time; Layer plays them together, "
                 "which sounds like two players in unison (doubled, slightly chorused).").classes(
            "text-xs opacity-70 max-w-3xl")

    ui.separator()
    ui.label("Mapping").classes("text-base font-medium")
    with ui.row().classes("items-center gap-4"):
        ui.label("Use neighbours as extra round robins:")
        ui.toggle({0: "Off", 1: "±1 semitone", 2: "±2 semitones"}, value=p.neighbour_round_robins,
                  on_change=lambda e: ed.set(p, "neighbour_round_robins", e.value))
    ui.label("Between recorded notes, each sample is stretched to the midpoint (the upper one is pitched "
             "down). Neighbour round robins add the hits of nearby notes, pitched to fit, to each key's "
             "rotation: more variation, slightly less faithful timbre. Stretching past the lowest and "
             "highest notes is set in step 3.").classes("text-xs opacity-70 max-w-3xl")

    with ui.row().classes("items-center gap-4"):
        ui.switch("Even out loudness between notes", value=p.balance,
                  on_change=lambda e: (ed.set(p, "balance", e.value), max_db.set_open(e.value)))
        with Collapse(p.balance, axis="x") as max_db:
            ed.number("At most", p, "balance_max_db", cast=float, min=0.5, max=24, step=0.5,
                      suffix="dB").classes("w-28")
    ui.label("Compares each note with its neighbours per velocity layer and corrects only the ones that "
             "stick out, through the sample volume in the instrument. The audio files stay unchanged, and "
             "the difference between soft and loud layers is kept.").classes("text-xs opacity-70 max-w-3xl")

    ui.separator()
    ui.label("Extras").classes("text-base font-medium")
    for x in inst.extras:
        extra_row(ed, x)
    if not inst.extras:
        ui.label("No extras (step 4).").classes("text-sm opacity-70")

    ui.separator()
    ui.label("Envelope & controls").classes("text-base font-medium")
    with ui.row().classes("items-start gap-4"):
        ed.number("Attack", p, "attack", cast=float, min=0, max=10, step=0.01, suffix="s").classes("w-28")
        ed.number("Release", p, "release", cast=float, min=0, max=20, step=0.1, suffix="s").classes("w-28")

        def vel_changed(e: Any) -> None:
            ed.set(p, "vel_track", None if e.value in (None, "") else float(e.value))

        ui.number("Velocity sensitivity", value=p.vel_track, min=0, max=1, step=0.05, placeholder="auto",
                  on_change=vel_changed).classes("w-44").props('hint="empty: auto"')
    with ui.row().classes("items-center gap-2"):
        ui.label("Knobs:")
        for key, label in CONTROLS.items():
            def toggle(e: Any, key: str = key) -> None:
                chosen = [c for c in CONTROLS if (c == key and e.value) or (c != key and c in p.controls)]
                ed.set(p, "controls", chosen)

            ui.checkbox(label, value=key in p.controls, on_change=toggle)
    with ui.row().classes("items-start gap-4"):
        ed.number("Tone default", p, "tone", cast=float, min=0, max=1, step=0.05).classes("w-28")
        ed.number("Reverb default", p, "reverb", cast=float, min=0, max=1, step=0.05).classes("w-28")
        ed.number("Chorus default", p, "chorus", cast=float, min=0, max=1, step=0.05).classes("w-28")

    ui.separator()
    ui.label("Look").classes("text-base font-medium")
    with ui.row().classes("items-start gap-4 w-full"):
        ed.text("Background image (812 × 375 px, PNG or JPG)", p, "background",
                placeholder="/path/to/background.png").classes("grow")
        ed.text("Background colour", p, "bg_color").classes("w-40").props('hint="AARRGGBB"')
        ed.text("Text colour", p, "text_color").classes("w-40").props('hint="AARRGGBB"')

    @ui.refreshable
    def preview() -> None:
        ui.html(preview_html(ed), sanitize=False)

    preview()
    ed.on_change_hooks.append(preview.refresh)


def extra_row(ed: "Editor", x: Extra) -> None:
    with ui.row().classes("items-start gap-4"):
        ui.label(x.name).classes("w-48 mt-4")

        def use_changed(e: Any) -> None:
            ed.set(x, "use", e.value)
            key.set_open(e.value == "key")

        ui.select({"release": "Play on key release", "key": "Own key", "off": "Don't use"}, value=x.use,
                  label="Use", on_change=use_changed).classes("w-52")
        with Collapse(x.use == "key", axis="x") as key:
            ed.note("Key", x, "key").classes("w-28")


def step_build(ed: "Editor") -> None:
    inst = ed.inst
    state: dict[str, Any] = {"result": None, "busy": False}
    ui.label(f"Output: {bundle.build_root() / inst.meta.slug}").classes("text-xs opacity-60")
    ui.label(f"DecentSampler library folder: {bundle.library_dir()}").classes("text-xs opacity-60")

    with ui.row().classes("items-center gap-2"):
        build_btn = ui.button("Build & validate", icon="construction")
        install_btn = ui.button("Install into DecentSampler", icon="download").props("outline")
        package_btn = ui.button("Package .dslibrary", icon="inventory_2").props("outline")
        reveal_btn = ui.button("Show in Finder", icon="folder_open").props("flat")
    for b in (install_btn, package_btn, reveal_btn):
        b.disable()

    @ui.refreshable
    def results() -> None:
        r = state["result"]
        if r is None:
            ui.label("Not built yet.").classes("opacity-70")
            return
        ok = r.report.ok
        ui.label(("✓ Valid" if ok else "✗ Not valid") + f" · {r.samples} samples · {r.size_bytes / 1e6:,.0f} MB") \
            .classes("text-base font-medium " + ("text-green-600" if ok else "text-red-600"))
        ui.label(str(r.preset_file)).classes("text-xs font-mono opacity-70")
        for e in r.report.errors[:50]:
            ui.label(f"✗ {e}").classes("text-sm text-red-600")
        for w in r.warnings + r.report.warnings:
            ui.label(f"⚠ {w}").classes("text-sm text-orange-600")
        for line in r.info:
            ui.label(f"ℹ {line}").classes("text-sm opacity-80")

    async def do_build() -> None:
        if state["busy"]:
            return
        ed.autosave()
        state["busy"] = True
        build_btn.disable()
        try:
            state["result"] = await run.io_bound(bundle.build, inst.model_copy(deep=True))
        except ValueError as e:
            state["result"] = None
            ui.notify(str(e), type="warning", multi_line=True)
        except Exception as e:  # unexpected I/O errors: show them instead of failing silently
            state["result"] = None
            ui.notify(f"Build failed: {e}", type="negative", multi_line=True)
        finally:
            state["busy"] = False
            build_btn.enable()
        r = state["result"]
        for b in (install_btn, package_btn):
            b.set_enabled(bool(r and r.report.ok))
        reveal_btn.set_enabled(r is not None)
        results.refresh()

    async def do_install() -> None:
        try:
            target = await run.io_bound(bundle.install, state["result"].bundle)
        except ValueError as e:
            ui.notify(str(e), type="warning", multi_line=True)
            return
        ui.notify(f"Installed to {target}. In DecentSampler, open it from the library browser "
                  "(rescan the folder if it doesn't show up).", type="positive", multi_line=True)

    async def do_package() -> None:
        path = await run.io_bound(bundle.package, inst, state["result"].bundle)
        ui.notify(f"Wrote {path}", type="positive", multi_line=True)

    def show_bundle() -> None:
        reveal(state["result"].bundle)

    build_btn.on_click(do_build)
    install_btn.on_click(do_install)
    package_btn.on_click(do_package)
    reveal_btn.on_click(show_bundle)
    results()

