"""Wizard steps 6 (sound & look) and 7 (build, install, package), and the extras' keys of step 4."""

import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Callable

from fastapi import HTTPException
from fastapi.responses import FileResponse
from nicegui import app, run, ui

from vinst import bundle, lowcut, notes, preset, reverb_send, review, sf2, store
from vinst.plan import layer_bounds, recording_passes, valid_splits
from vinst.preset import mixable_signals
from vinst.spec import CONTROLS, Extra, ExtraKey
from vinst.ui.anim import Collapse
from vinst.ui.controls import NumberField, Segmented, VelocitySplits
from vinst.ui import preview
from vinst.ui.equalizer import equalizer
from vinst.ui.finder import reveal

if TYPE_CHECKING:
    from vinst.spec import Instrument
    from vinst.ui.app import Editor

# Drawn at the instrument's own size and scaled to fit the column: an SVG's viewBox scales what's in it.
PREVIEW_MAX_W = 640


ARTWORK_ATTRS = ("background", "cover_art")

# (slug, preset attribute) -> image path as currently typed in an editor; previews refresh before
# autosave has written it to the spec file.
_artwork: dict[tuple[str, str], Path] = {}


@app.get("/artwork/{slug}/{attr}")
def artwork_image(slug: str, attr: str) -> FileResponse:
    if attr not in ARTWORK_ATTRS:
        raise HTTPException(404)
    path = _artwork.get((slug, attr))
    if path is None:
        try:
            value = getattr(store.load(slug).preset, attr)
        except Exception:  # deleted, renamed or hand-broken instrument: a stale page asks for it
            raise HTTPException(404) from None
        path = Path(value).expanduser() if value else None
    if path is None or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


def artwork_url(inst: "Instrument", attr: str) -> str | None:
    """The image's URL for the browser, or None without an image file."""
    value = getattr(inst.preset, attr)
    path = Path(value).expanduser() if value else None
    if path is None or not path.is_file():
        _artwork.pop((inst.meta.slug, attr), None)
        return None
    _artwork[(inst.meta.slug, attr)] = path
    return f"/artwork/{inst.meta.slug}/{attr}?v={path.stat().st_mtime_ns}"


# slug -> (modification time of its hits.json, what it holds), so the preview needn't read it on every change.
_manifests: dict[str, tuple[int, "review.Manifest | None"]] = {}


def _sliced(slug: str) -> "review.Manifest | None":
    path = store.manifest_path(slug)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    if slug not in _manifests or _manifests[slug][0] != stamp:
        _manifests[slug] = (stamp, review.load(path))
    return _manifests[slug][1]


def preview_html(ed: "Editor") -> str:
    """The instrument as DecentSampler will show it (see preview)."""
    inst, p = ed.inst, ed.inst.preset
    url = artwork_url(inst, "background")
    manifest = _sliced(inst.meta.slug)
    try:
        mapping = preset.map_samples(inst, manifest) if manifest else None
    except Exception:  # half-edited settings: the preview falls back to what they ask for
        mapping = None
    if mapping is not None and not mapping.span:
        mapping = None
    return preview.render(inst, bundle.low_cut_now(inst, manifest), url, PREVIEW_MAX_W, mapping)


LAYER_COLOURS = ["#90caf9", "#42a5f5", "#1e88e5", "#1565c0", "#0d47a1"]


def velocity_layers(ed: "Editor") -> None:
    """Where each recorded dynamic takes over, e.g. when f kicks in, as velocity boundaries.

    Rebuilt when the layers change in step 4, so there is always one knob per boundary.
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

        def moved(e: Any) -> None:
            try:
                values = [int(v) for v in e.value]
            except (TypeError, ValueError):
                values = []
            if not valid_splits(len(layers), values):
                splits.set_value(starts())  # e.g. sent for another number of velocities
            elif values != starts():  # also called for "Even split", which only shows the even starts
                ed.set(p, "velocity_splits", values)

        def even() -> None:
            ed.set(p, "velocity_splits", [])
            splits.set_value(starts())

        with ui.column().classes("gap-2 w-full max-w-3xl"):
            with ui.row().classes("items-center w-full mt-2"):
                ui.label("Where each velocity starts").classes("text-sm font-medium")
                ui.space()
                ui.button("Even split", on_click=even).props("flat dense")
            splits = VelocitySplits(layers, LAYER_COLOURS, value=starts(), on_change=moved).classes("w-full")
            ui.label("Drag a knob to where the next velocity takes over. Play your keyboard softly and firmly in "
                     "DecentSampler to find the place. Changes need a new build, never new slicing.") \
                .classes("text-xs opacity-70")

    section()
    ed.on_change_hooks.append(lambda: shown["layers"] != list(inst.dynamics.layers) and section.refresh())


def step_sound(ed: "Editor") -> None:
    inst, p = ed.inst, ed.inst.preset

    ui.label("Tuning").classes("text-base font-medium")
    with ui.row().classes("items-center gap-4"):
        def tuning_changed(e: Any) -> None:
            ed.set(p, "tuning", e.value)
            partial.set_open(e.value == "partial")

        Segmented({"off": "Off", "partial": "Partial", "full": "Full"}, value=p.tuning, on_change=tuning_changed)
        with Collapse(p.tuning == "partial", axis="x", inner_classes="flex items-center gap-4 pr-2") as partial:
            ui.slider(min=0, max=1, step=0.05, value=p.tuning_amount,
                      on_change=lambda e: ed.set(p, "tuning_amount", e.value)).classes("w-48")
            ui.label().bind_text_from(p, "tuning_amount", lambda v: f"corrects {v:.0%}").classes("whitespace-nowrap")
    ui.label("Uses the cents measured in the worksheet (step 5). Partial keeps some natural detune.") \
        .classes("text-xs opacity-70")

    if len(recording_passes(inst)) > 1:
        ui.separator()
        ui.label("Recording passes").classes("text-base font-medium")
        Segmented({"switch": "Switch (menu in the instrument)", "layer": "Layer (play together)"},
                  value=p.pass_mode, on_change=lambda e: ed.set(p, "pass_mode", e.value))
        ui.label("Passes are separate performances. Switch plays one at a time; Layer plays them together, "
                 "which sounds like two players in unison (doubled, slightly chorused).").classes(
            "text-xs opacity-70 max-w-3xl")

    ui.separator()
    ui.label("Mapping").classes("text-base font-medium")
    ui.label("Use neighbours as extra round robins:")
    with ui.grid(columns="auto auto").classes("items-center gap-x-4 gap-y-2"):
        for field, label in (("neighbours_below", "Notes below (pitched up)"),
                             ("neighbours_above", "Notes above (pitched down)")):
            ui.label(label)
            Segmented({0: "Off", 1: "1 semitone", 2: "2 semitones"}, value=getattr(p, field),
                      on_change=lambda e, f=field: ed.set(p, f, e.value))
    ui.label("Keys between recorded notes play the recorded note above them, pitched down (pitched up, a "
             "sample soon sounds thin; only above the highest note is it pitched up). Neighbour round robins "
             "add the hits of nearby notes, pitched to fit, to each key's rotation: more variation, slightly "
             "less faithful timbre. Stretching past the lowest and highest notes is set in step 3."
             ).classes("text-xs opacity-70 max-w-3xl")

    ui.separator()
    ui.label("Loudness").classes("text-base font-medium")
    with ui.row().classes("items-center gap-4"):
        ui.switch("Even out loudness within each velocity", value=p.balance,
                  on_change=lambda e: (ed.set(p, "balance", e.value), tolerance.set_open(e.value)))
        with Collapse(p.balance, axis="x") as tolerance:
            ed.number("Allowed difference", p, "balance_tolerance_db", cast=float, min=0, max=12, step=0.5,
                      prefix="±", suffix="dB").classes("w-36")
    ui.label("Each velocity's loudness over the keyboard is drawn as a straight line, so a trend from low to "
             "high notes stays, but a note or a whole string that is louder or softer doesn't tilt it. A "
             "sample further from the line than the allowed difference is brought back to it; smaller "
             "differences stay as played. At 0 every sample is brought onto the line. Soft velocities stay "
             "softer than loud ones. Only the instrument's copies of the samples change.") \
        .classes("text-xs opacity-70 max-w-3xl")
    remembered = {"range": p.loudness_range_db or 42.0}

    def follow(on: bool) -> None:
        if p.loudness_range_db is not None:
            remembered["range"] = p.loudness_range_db
        ed.set(p, "loudness_range_db", remembered["range"] if on else None)
        if on:
            range_field.set_value(remembered["range"])
        loudness_range.set_open(on)

    with ui.row().classes("items-center gap-4"):
        ui.switch("Loudness follows velocity", value=p.loudness_range_db is not None,
                  on_change=lambda e: follow(e.value))
        with Collapse(p.loudness_range_db is not None, axis="x") as loudness_range:
            range_field = ed.number("Velocity 1 to 127", p, "loudness_range_db", cast=float, min=0, max=60,
                                    step=1, suffix="dB").classes("w-40")
    ui.label("From the softest to the hardest key press, the loudness rises by this much: quickly at the soft "
             "end, gently at the top, so the usual playing range stays even (at 42 dB the volume is in "
             "proportion to velocity: velocity 64 about 6 dB softer than 127, velocity 20 about 16 dB). Each "
             "velocity's samples are lifted or lowered to it: moving from one velocity to the next changes "
             "the sound, not the loudness. It can reach further than you played, e.g. pianissimo and "
             "fortissimo from p and mf. A note's loudest velocity sounds as recorded in the middle of its "
             "range; its variations keep up to half the allowed difference above between them. Off: the "
             "samples play as recorded, and velocity changes their volume only a little (Velocity "
             "sensitivity, under Envelope).").classes("text-xs opacity-70 max-w-3xl")

    ui.separator()
    ui.label("Envelope").classes("text-base font-medium")
    with ui.row().classes("items-start gap-4"):
        ed.number("Attack", p, "attack", cast=float, min=0, max=10, step=0.01, suffix="s").classes("w-28")
        ed.number("Release", p, "release", cast=float, min=0, max=20, step=0.1, suffix="s").classes("w-28")

        def vel_changed(e: Any) -> None:
            ed.set(p, "vel_track", None if e.value in (None, "") else float(e.value))

        NumberField("Velocity sensitivity", value=p.vel_track, min=0, max=1, step=0.05, placeholder="auto",
                  on_change=vel_changed).classes("w-44").props('hint="empty: auto"')

    ui.separator()
    ui.label("Controls").classes("text-base font-medium")
    ui.label("The knobs in the instrument.").classes("text-xs opacity-70")
    switches: dict[str, ui.switch] = {}
    with ui.column().classes("gap-1 w-full"):
        for key in CONTROLS:
            if key in shown_controls(inst):
                control_row(ed, key, switches)

    ui.separator()
    ui.label("Look").classes("text-base font-medium")
    ui.add_css(ARTWORK_CSS)
    artwork_field(ed, "background", "background", "Background image", f"{preset.UI_W} / {preset.UI_H}",
                  "812 × 375 px. DecentSampler's keyboard covers the lower part.",
                  "The background colour fills the panel when no image is selected.")
    artwork_field(ed, "cover_art", "cover", "Library cover", "1",
                  "Square. DecentSampler's File Browser shows it when you select the instrument.",
                  "No library cover selected.")
    with ui.row().classes("items-start gap-4 w-full"):
        colour_field(ed, "Background colour", "bg_color")
        colour_field(ed, "Text colour", "text_color")
        colour_field(ed, "Accent colour", "accent_color", "The knobs' lit arcs and the playable keys")
    with ui.row().classes("items-center gap-4"):
        ui.switch("Show the author", value=p.show_author, on_change=lambda e: ed.set(p, "show_author", e.value))
        ui.switch("Show the version", value=p.show_version, on_change=lambda e: ed.set(p, "show_version", e.value))
        ui.switch("Show the copyright", value=p.show_copyright,
                  on_change=lambda e: ed.set(p, "show_copyright", e.value))
        ui.switch("Show the website", value=p.show_website, on_change=lambda e: ed.set(p, "show_website", e.value))

    @ui.refreshable
    def look_preview() -> None:
        ui.html(preview_html(ed), sanitize=False).classes("w-full")

    look_preview()
    ed.on_change_hooks.append(look_preview.refresh)


# The upload box is the image's frame, at the shape DecentSampler wants. "contain" shows an image
# of another shape as such instead of cropping it.
ARTWORK_CSS = """
.vi-artwork { max-height: none; }
.vi-artwork .q-uploader__subtitle { display: none; }
.vi-artwork .q-uploader__list { flex: none; min-height: 0; aspect-ratio: var(--vi-ratio);
                                background: var(--vi-art) center / contain no-repeat; }
"""


def artwork_field(ed: "Editor", attr: str, stem: str, label: str, ratio: str, hint: str, empty: str) -> None:
    """Upload artwork into the instrument's folder and edit its preset path; the upload box shows it."""
    inst, p = ed.inst, ed.inst.preset
    folder = store.instrument_dir(inst.meta.slug)
    format_message = f"Choose a PNG or JPG image for {label.lower()}."

    def selected_path() -> Path | None:
        value = getattr(p, attr)
        return Path(value).expanduser() if value else None

    def remove_copy(path: Path | None) -> None:
        if (path and path.stem == stem and path.suffix.lower() in bundle.IMAGE_SIGNATURES
                and path.parent.resolve() == folder.resolve()):
            path.unlink(missing_ok=True)

    @ui.refreshable
    def current() -> None:
        path = selected_path()
        url = artwork_url(inst, attr)  # also forgets a removed image, so the route stops serving it
        upload.style(f"--vi-art: url('{url}')" if url else "--vi-art: none")
        if path is None:
            ui.label(empty).classes("text-sm opacity-70")
            return
        with ui.row().classes("items-center gap-3"):
            if path.is_file():
                ui.label(path.name).classes("text-sm")
            else:
                ui.label(f"{path.name} is missing.").classes("text-sm text-orange-600")
            ui.button(f"Remove {stem}", icon="close", on_click=remove).props("flat dense")

    def remove() -> None:
        remove_copy(selected_path())
        ed.set(p, attr, "")
        current.refresh()

    async def uploaded(e: Any) -> None:
        suffix = Path(e.file.name).suffix.lower()
        try:
            if suffix not in bundle.IMAGE_SIGNATURES:
                ui.notify(format_message, type="warning")
                return
            dest = folder / f"{stem}{suffix}"
            with TemporaryDirectory(dir=folder) as staging:
                pending = Path(staging) / dest.name
                await e.file.save(pending)
                if not bundle.is_artwork_image(pending):
                    ui.notify(format_message, type="warning")
                    return
                pending.replace(dest)
        except OSError as err:
            ui.notify(f"Could not save {label.lower()}: {err}", type="negative")
            return
        finally:
            upload.reset()
        old = selected_path()
        if getattr(p, attr) == str(dest):
            ed.refresh()  # The file changed at the same path.
        else:
            ed.set(p, attr, str(dest))
        if old and old.name.casefold() != dest.name.casefold():
            remove_copy(old)
        current.refresh()

    ui.label(label).classes("text-sm font-medium")
    with ui.row().classes("items-start gap-4 w-full"):
        upload = ui.upload(on_upload=uploaded, auto_upload=True, max_files=1,
                           on_rejected=lambda: ui.notify(format_message, type="warning"),
                           label="Drop a PNG or JPG") \
            .props(f'accept="{",".join(bundle.IMAGE_SIGNATURES)}" flat bordered color=grey-8') \
            .classes("vi-artwork w-48 max-w-full").style(f"--vi-ratio: {ratio}")
        with ui.column().classes("gap-1 pt-2"):
            current()
            ui.label(hint).classes("text-xs opacity-70")


def reverb_ir_field(ed: "Editor") -> None:
    """An impulse response of your own for the reverb: dropped or chosen, it's copied into the instrument's folder."""
    inst, p = ed.inst, ed.inst.preset

    @ui.refreshable
    def current() -> None:
        path = Path(p.reverb_ir).expanduser() if p.reverb_ir else None
        if path is None:
            ui.label("None: DecentSampler's reverb, with the room size and damping above.").classes("text-sm opacity-70")
            return
        with ui.row().classes("items-center gap-3"):
            # The file stays while DecentSampler's reverb plays, so the two can be compared.
            Segmented({False: "DecentSampler's reverb", True: path.name}, value=p.reverb_own,
                      on_change=lambda e: ed.set(p, "reverb_own", e.value))
            ui.button("Remove", icon="close", on_click=remove).props("flat dense")
        if not path.is_file():
            ui.label(f"{path.name} is missing.").classes("text-sm text-orange-600")

    def remove() -> None:
        old = Path(p.reverb_ir).expanduser() if p.reverb_ir else None
        ed.set(p, "reverb_ir", "")
        # Only our own copy goes; a file picked elsewhere before stays where it is.
        if old and old.parent == store.instrument_dir(inst.meta.slug) and old.stem.startswith(reverb_send.IR_COPY):
            old.unlink(missing_ok=True)
        current.refresh()

    async def uploaded(e: Any) -> None:
        suffix = Path(e.file.name).suffix.lower()
        if suffix not in reverb_send.IR_TYPES:
            ui.notify("An impulse response must be a WAV, AIFF or FLAC file.", type="warning")
            return
        folder = store.instrument_dir(inst.meta.slug)
        # Its own name kept in the copy, so the field and the build say which room or reverb it is.
        name = re.sub(r"[^\w.-]+", "-", Path(e.file.name).stem).strip("-.")
        dest = folder / (f"{reverb_send.IR_COPY}-{name}{suffix}" if name else f"{reverb_send.IR_COPY}{suffix}")
        await e.file.save(dest)
        # Only once the new one is saved: a failed upload leaves the one in use.
        for old in folder.glob(f"{reverb_send.IR_COPY}*"):
            if old != dest:
                old.unlink()
        ed.set(p, "reverb_ir", str(dest))
        ed.set(p, "reverb_own", True)
        upload.reset()
        current.refresh()

    ui.label("Impulse response of your own (optional)").classes("text-sm font-medium")
    with ui.row().classes("items-start gap-4 w-full"):
        upload = ui.upload(on_upload=uploaded, auto_upload=True, max_files=1,
                           label="Drop a WAV, AIFF or FLAC here, or click +") \
            .props(f'accept="{",".join(reverb_send.IR_TYPES)}" flat bordered color=grey-8').classes("w-80")
        with ui.column().classes("gap-1 pt-2 max-w-xl"):
            current()
            ui.label("A room or a reverb you captured, instead of DecentSampler's reverb, on a bus of its own "
                     "so the dry sound stays as recorded. With Without the attack's hiss (below), it's filtered "
                     "per pitch as DecentSampler's reverb is there. Remove its direct sound first, or the reverb "
                     "plays every note a second time. It's made about as loud as DecentSampler's reverb with "
                     "this room size and damping, which otherwise don't change it; set the knob by ear. Longer "
                     f"than {reverb_send.IR_MAX_S:g} s, it's faded out and cut there.") \
                .classes("text-xs opacity-70")
            ui.link("How to record one", reverb_send.DOCS_URL, new_tab=True).classes("text-xs")


def cut_curve_svg(below: float, reduction_db: float, accent: str) -> str:
    """The low cut over the frequencies around a note: how much quieter, against the share of its pitch."""
    w, h, left, top, right, bottom = 240, 90, 40, 8, 8, 18
    lo, hi = 0.25, 1.25
    shares = [lo + (hi - lo) * i / 120 for i in range(121)]
    depth = max(reduction_db, 1.0) + 2
    db = lowcut.curve(below, reduction_db, shares)

    def x(share: float) -> float:
        return left + (share - lo) / (hi - lo) * (w - left - right)

    def y(v: float) -> float:
        return top - v / depth * (h - top - bottom)

    line = " ".join(f"{x(sh):.1f},{y(v):.1f}" for sh, v in zip(shares, db))
    colour = preview.css(accent)
    grid = "".join(f"<line x1='{left}' x2='{w - right}' y1='{y(v):.1f}' y2='{y(v):.1f}' stroke='currentColor' "
                   f"stroke-opacity='.15'/><text x='{left - 4}' y='{y(v) + 3:.1f}' text-anchor='end' font-size='9' "
                   f"fill='currentColor' opacity='.6'>{v:g} dB</text>" for v in (0, -round(reduction_db)))
    # The share's label goes above the curve when it would sit on the pitch's.
    near = abs(below - 1.0) < 0.12
    marks = "".join(f"<line x1='{x(sh):.1f}' x2='{x(sh):.1f}' y1='{top}' y2='{h - bottom}' stroke='currentColor' "
                    f"stroke-opacity='{op}' stroke-dasharray='3 3'/><text x='{x(sh):.1f}' y='{ty}' "
                    f"text-anchor='{anchor}' font-size='9' fill='currentColor' opacity='.7'>{label}</text>"
                    for sh, label, op, ty, anchor in (
                        (below, f"{below:.0%}", .35, top + 8 if near else h - 4, "end" if near else "middle"),
                        (1.0, "pitch", .5, h - 4, "middle")))
    return (f"<svg viewBox='0 0 {w} {h}' width='{w}' height='{h}' style='display:block'>{grid}{marks}"
            f"<polyline points='{line}' fill='none' stroke='{colour}' stroke-width='2'/></svg>")


def colour_field(ed: "Editor", label: str, attr: str, tooltip: str | None = None) -> None:
    """A colour with a picker, and a reset to the default while it differs; the preset keeps it as
    AARRGGBB, its alpha left as it was."""
    from vinst.spec import Preset

    p = ed.inst.preset
    default = Preset.model_fields[attr].default

    def changed(e: Any) -> None:
        value = (e.value or "").strip().lstrip("#")
        if len(value) == 6 and all(c in "0123456789abcdefABCDEF" for c in value):
            ed.set(p, attr, getattr(p, attr)[:2] + value.upper())
        reset.set_visibility(getattr(p, attr).upper() != default.upper())

    def to_default() -> None:
        ed.set(p, attr, default)
        field.value = f"#{default[2:]}"

    with ui.row().classes("items-center gap-0 no-wrap"):
        field = ui.color_input(label, value=f"#{getattr(p, attr)[2:]}", on_change=changed, preview=True) \
            .classes("w-44")
        if tooltip:
            field.tooltip(tooltip)
        reset = ui.button(icon="restart_alt", on_click=to_default).props("flat round dense size=sm") \
            .tooltip("Back to the default colour")
        reset.set_visibility(getattr(p, attr).upper() != default.upper())


def shown_controls(inst: "Instrument") -> list[str]:
    """The controls that can do something in this instrument."""
    out = []
    for key in CONTROLS:
        if key == "mix" and len(mixable_signals(inst)) < 2:
            continue
        if key == "noise" and not any(x.use == "release" for x in inst.extras):
            continue
        if key == "lowcut" and not lowcut.applies(inst):
            continue
        out.append(key)
    return out


# What each control does, in a line under its switch.
CONTROL_TEXT = {
    "mix": "Blends the signals, e.g. pickup and microphone.",
    "tone": "Darkens the sound, as a lowpass filter does; the mod wheel moves it. Instead of the Equalizer.",
    "eq": "Shapes the sound with up to 8 bands; each peak or shelf gets a knob to boost or cut it. "
          "Instead of the Tone knob.",
    "lowcut": "Makes the frequencies below each note's pitch quieter (a key-tracked low cut).",
    "attack": "How long each note takes to fade in; starts at the envelope's attack. Not for plucked or "
              "struck strings: it only softens the pluck.",
    "release": "How long a note rings on after the key is let go; starts at the envelope's release.",
    "reverb": "How much room is added.",
    "chorus": "Thickens and widens the sound with slightly detuned copies of it.",
    "noise": "How loud the release noises play, e.g. a finger leaving the string.",
}


# Both shape the sound: one or the other.
RIVALS = {"tone": "eq", "eq": "tone"}


def control_row(ed: "Editor", key: str, switches: dict[str, ui.switch]) -> None:
    """A control's switch; its settings, if it has any, slide open below it while it's on: below, not
    beside, so they keep their width in a narrow column and take no room while closed."""
    inst, p, proc = ed.inst, ed.inst.preset, ed.inst.processing
    # The low cut is one thing: on, its samples are cut and the knob blends back to the uncut ones.
    on = proc.low_cut if key == "lowcut" else key in p.controls

    def switched(e: Any) -> None:
        if key == "lowcut":
            ed.set(proc, "low_cut", e.value)
        else:
            gone = RIVALS.get(key) if e.value else None
            ed.set(p, "controls", [c for c in CONTROLS
                                   if (c == key and e.value) or (c not in (key, gone) and c in p.controls)])
            if gone in switches:
                switches[gone].set_value(False)
        if settings is not None:
            settings.set_open(e.value)

    name = CONTROLS[key]
    settings = None
    with ui.column().classes("gap-0 py-1 w-full"):
        switches[key] = ui.switch(name, value=on, on_change=switched)
        ui.label(CONTROL_TEXT[key]).classes("text-xs opacity-70 pl-12")
        if key == "eq":
            with Collapse(on, inner_classes="flex flex-col gap-1 pl-12 pt-2") as settings:
                ui.label("Drag a band's point on the curve, or type its values. In the instrument, each peak "
                         "or shelf gets a knob starting where you set it here; cuts and notches stay as set. "
                         "The grey area is the average spectrum of your samples; Listen plays a few notes "
                         "through the EQ. MuseScore's soundfont gets the EQ built into its samples.") \
                    .classes("text-xs opacity-70 max-w-3xl")
                equalizer(ed)
        elif key == "mix" and len(mixable_signals(inst)) == 2:
            a, b = mixable_signals(inst)
            with Collapse(on, inner_classes="flex flex-col gap-1 pl-12 pt-2") as settings:
                ed.number("Starts at", p, "mix", cast=float, min=0, max=100, step=5, precision=0,
                          suffix="%").classes("w-28")
                ui.label(f"Where the {b.name} knob starts: 0 % plays only {a.name}, 100 % only {b.name}, "
                         f"between both blended.").classes("text-xs opacity-70 max-w-3xl")
        elif key in ("lowcut", "tone", "reverb", "chorus"):
            with Collapse(on, inner_classes="flex flex-col gap-1 pl-12 pt-2") as settings:
                with ui.row().classes("items-center gap-2"):
                    if key == "lowcut":
                        NumberField("Below", value=round(proc.low_cut_below * 100), min=30, max=100, step=5,
                                  precision=0, suffix="% of the pitch",
                                  on_change=lambda e: e.value and ed.set(proc, "low_cut_below",
                                                                         float(e.value) / 100)).classes("w-44")
                        ed.number("By", proc, "low_cut_db", cast=float, min=1, max=40, step=1,
                                  suffix="dB").classes("w-24")

                        @ui.refreshable
                        def drawn() -> None:
                            ui.html(cut_curve_svg(proc.low_cut_below, proc.low_cut_db,
                                                  inst.preset.accent_color), sanitize=False)

                        drawn()
                        ed.on_change_hooks.append(drawn.refresh)
                    else:
                        ed.number("Starts at", p, key, cast=float, min=0, max=1, step=0.05).classes("w-28")
                    if key == "reverb":
                        ed.number("Room size", p, "reverb_room", cast=float, min=0, max=1, step=0.05) \
                            .classes("w-28").tooltip("Bigger rooms ring longer")
                        ed.number("Damping", p, "reverb_damping", cast=float, min=0, max=1, step=0.05) \
                            .classes("w-28").tooltip("How fast the reverb loses its highs: more is warmer, and softens "
                                                     "a pluck's click ringing on as a hiss. To take the click out of "
                                                     "the reverb altogether: Without the attack's hiss, below")
                if key == "reverb":
                    reverb_ir_field(ed)
                    ui.switch("Without the attack's hiss", value=p.reverb_send,
                              on_change=lambda e: ed.set(p, "reverb_send", e.value)) \
                        .classes("pt-2") \
                        .tooltip("Each sample also feeds a reverb for its few semitones: a convolution reverb on a "
                                 "bus of its own, made when building from DecentSampler's reverb with this room and "
                                 "damping (or your own impulse response), turned down from about three times the "
                                 "pitch up")
                    ui.label("For plucked and struck strings: the click at the start of each note no longer rings "
                             "on in the reverb as a hiss, and the bright first moment of a note doesn't linger in "
                             "it, so the reverb sounds warmer. The dry sound stays exactly as recorded. Notes "
                             "without a click (bowed, blown, sung) gain little. At the same knob setting the "
                             "reverb is a little quieter, its highs gone, so you may turn it up a bit. MuseScore's "
                             "soundfont has no reverb; nothing changes there.").classes("text-xs opacity-70 max-w-3xl")
                if key == "lowcut":
                    ui.label("E.g. the low resonance of a ukulele's body that rings on after the pluck; the note itself, from its pitch up, "
                             "stays as it is. A curve, not a step, as drawn: the full amount well below the share "
                             "set, less towards it, almost nothing at the pitch. Applied when building, so no "
                             "slicing is needed; the instrument then also carries what was cut out. "
                             "The instrument's Low cut knob starts at 0 %, as recorded, and reaches this cut at "
                             "100 %; most of the cut comes in its upper half. Two things are set for the knob at 50 %, "
                             "not where it starts: loudness is evened out there, so notes with much low resonance "
                             "play a little louder at 0 % and a little quieter at 100 %, and MuseScore's "
                             "soundfont, which has no knob, has the cut as at 50 %.").classes("text-xs opacity-70 max-w-3xl")


def extra_use(ed: "Editor", x: Extra) -> None:
    """How the instrument plays an extra: on note release, on keys of its own, or not at all."""
    with ui.column().classes("vi-extra-map gap-0 w-full pb-2") as box:
        def use_changed(e: Any) -> None:
            # Its keys were left as they were while it played on release: off the notes and the
            # other extras' keys now.
            if e.value == "key" and ed.inst.place_extra(x):
                key_field.set_value(notes.name(x.key))
                more_keys.refresh()
            ed.set(x, "use", e.value)
            key.set_open(e.value == "key")

        ui.select({"release": "Play on key release", "key": "Own key", "off": "Don't use"}, value=x.use,
                  label="Use", on_change=use_changed).classes("w-52")
        # Below the select, not beside it: closed, it takes no room even where the row would wrap.
        with Collapse(x.use == "key", inner_classes="flex flex-col gap-2 pt-2") as key:
            turns: list[tuple[ui.label, Callable[[], int]]] = []

            def show_turns() -> None:
                if box.is_deleted:  # the extras were redrawn
                    ed.on_change_hooks.remove(show_turns)
                    return
                for i, (label, start) in enumerate(turns):
                    label.set_text(hit_turn(start(), x.hits, own=i > 0))

            with ui.row().classes("items-start gap-4"):
                key_field = ed.note("Key", x, "key", problem=ed.inst.extra_key_problem).classes("w-28")
                ui.switch("Stops ringing notes", value=x.mutes, on_change=lambda e: ed.set(x, "mutes", e.value)) \
                    .classes("mt-2").tooltip("As a chuck mutes the strings: playing it fades out the notes still ringing")
            turns.append((ui.label().classes("text-xs opacity-70 -mt-1"), lambda: 1))

            @ui.refreshable
            def more_keys() -> None:
                del turns[1:]
                for k in x.more_keys:
                    with ui.row().classes("items-start gap-4"):
                        ed.note("Key", k, "key", problem=ed.inst.extra_key_problem).classes("w-28")
                        ed.number("Starts with hit", k, "start", min=1, max=x.hits, precision=0).classes("w-32")
                        ui.button(icon="delete", on_click=lambda k=k: remove(k)).props("flat round") \
                            .tooltip("Remove this key")
                    turns.append((ui.label().classes("text-xs opacity-70 -mt-1"), lambda k=k: k.start))
                show_turns()

            def add() -> None:
                x.more_keys.append(ExtraKey(key=ed.inst.free_extra_key(near=max(x.keys()) + 1),
                                            start=min(x.hits, len(x.keys()) + 1)))
                ed.changed()
                more_keys.refresh()

            def remove(k: ExtraKey) -> None:
                # By identity: two keys set alike are equal, and the one clicked must go.
                x.more_keys = [o for o in x.more_keys if o is not k]
                ed.changed()
                more_keys.refresh()

            more_keys()
            ed.on_change_hooks.append(show_turns)
            ui.button("+ Key starting with another hit", on_click=add).props("outline dense").classes("self-start") \
                .tooltip("The same hits in turn on one more key, each key keeping its own place. E.g. chucks "
                         "recorded down, up, down…: a key starting with hit 2 starts with an up stroke. "
                         "Rejected hits are skipped. In the soundfont, each key plays only the hit it starts with.")


def hit_turn(start: int, hits: int, own: bool = False) -> str:
    """What each press of a key plays, e.g. "Each press plays the next hit: 2, 3, 4, … 10, 1, then 2 again"."""
    if hits == 1:
        return "Each press plays its one hit."
    order = [str((start - 1 + i) % hits + 1) for i in range(hits)]
    if hits > 6:
        order = [*order[:3], f"… {order[-2]}", order[-1]]
    turn = "next hit of its own turn" if own else "next hit"
    return f"Each press plays the {turn}: {', '.join(order)}, then {order[0]} again."


def step_build(ed: "Editor") -> None:
    """Three parts: building (and its report), then what's done with it: the DecentSampler bundle,
    and the soundfont for MuseScore and other SF2 players."""
    inst = ed.inst
    state: dict[str, Any] = {"result": None, "busy": False, "progress": 0.0, "label": ""}

    with ui.card().classes("w-full").props("flat bordered"):
        ui.label("Build").classes("text-base font-medium")
        ui.label(f"Into {bundle.build_root() / inst.meta.slug}").classes("text-xs opacity-60")
        # As in the worksheet: the bar opens beside the button, with what's being done on a line below it.
        with ui.row().classes("items-center gap-4 w-full no-wrap"):
            build_btn = ui.button("Build & validate", icon="construction").classes("shrink-0")
            with Collapse(False, axis="x", inner_classes="flex flex-col gap-1 min-w-0") as progress_box:
                progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
                progress_label = ui.label().classes("text-xs opacity-70 truncate h-4 leading-4 w-0 min-w-full")
            progress_box.outer.classes("vi-build-progress grow min-w-48 max-w-xl")

        def tick() -> None:
            if state["busy"]:
                progress.value = state["progress"]
                progress_label.text = state["label"]

        ui.timer(0.25, tick)

        @ui.refreshable
        def report() -> None:
            r = state["result"]
            if r is None:
                ui.label("Not built yet.").classes("opacity-70")
                return
            ok = r.report.ok
            ui.label(("✓ Valid" if ok else "✗ Not valid") + f" · {r.samples} samples · {r.size_bytes / 1e6:,.0f} MB") \
                .classes("text-base font-medium " + ("text-green-600" if ok else "text-red-600"))
            for e in r.report.errors[:50]:
                ui.label(f"✗ {e}").classes("text-sm text-red-600")
            for w in r.warnings + r.report.warnings:
                ui.label(f"⚠ {w}").classes("text-sm text-orange-600")
            for line in r.info:
                ui.label(f"ℹ {line}").classes("text-sm opacity-80")

        report()

    with ui.card().classes("w-full").props("flat bordered"):
        ui.label("DecentSampler").classes("text-base font-medium")
        bundle_path = ui.label("Build first.").classes("text-xs font-mono opacity-70")
        ui.label(f"Its library folder: {bundle.library_dir()}").classes("text-xs opacity-60")
        with ui.row().classes("items-center gap-2"):
            install_btn = ui.button("Install into DecentSampler", icon="download").props("outline")
            package_btn = ui.button("Package .dslibrary", icon="inventory_2").props("outline")
            bundle_finder = ui.button("Show in Finder", icon="folder_open").props("flat")

    with ui.card().classes("w-full").props("flat bordered"):
        ui.label("Soundfont").classes("text-base font-medium")
        ui.label("The same instrument as an SF2 soundfont: MuseScore and other SF2 players play it without "
                 "DecentSampler. SF2 has no round robins, so each key and velocity always plays the same sample "
                 "(its note's own, the best in tune), and release noises are left out.") \
            .classes("text-xs opacity-70 max-w-3xl")
        font_path = ui.label("Build first.").classes("text-xs font-mono opacity-70")
        with ui.row().classes("items-center gap-2"):
            download_btn = ui.button("Download", icon="file_download").props("outline")
            musescore_btn = ui.button("Copy to MuseScore", icon="library_music").props("outline") \
                .tooltip(f"Into MuseScore's SoundFonts folder, {sf2.musescore_folder()}")
            font_finder = ui.button("Show in Finder", icon="folder_open").props("flat")

    def built() -> None:
        """After a build: what can be done with it."""
        r = state["result"]
        report.refresh()
        bundle_path.set_text(str(r.preset_file) if r else "Build first.")
        for b in (install_btn, package_btn):
            b.set_enabled(bool(r and r.report.ok))
        bundle_finder.set_enabled(r is not None)
        font = r.soundfont if r else None
        font_path.set_text(f"{font} · {r.soundfont_samples} samples · {font.stat().st_size / 1e6:,.1f} MB"
                           if font else "Build first.")
        for b in (download_btn, musescore_btn, font_finder):
            b.set_enabled(font is not None)

    async def do_build() -> None:
        if state["busy"]:
            return
        ed.autosave()
        state["busy"] = True
        state["progress"], state["label"] = 0.0, "Starting"
        progress.value, progress_label.text = 0.0, "Starting"
        build_btn.disable()
        progress_box.set_open(True)

        def report_progress(fraction: float, what: str) -> None:  # from the worker thread
            state["progress"], state["label"] = fraction, what

        try:
            state["result"] = await run.io_bound(bundle.build, inst.model_copy(deep=True), report_progress)
        except ValueError as e:
            state["result"] = None
            ui.notify(str(e), type="warning", multi_line=True)
        except Exception as e:  # unexpected I/O errors: show them instead of failing silently
            state["result"] = None
            ui.notify(f"Build failed: {e}", type="negative", multi_line=True)
        finally:
            state["busy"] = False
            build_btn.enable()
            progress_box.set_open(False)
        built()

    async def do_install() -> None:
        try:
            target = await run.io_bound(bundle.install, state["result"].bundle)
        except ValueError as e:
            ui.notify(str(e), type="warning", multi_line=True)
            return
        # DecentSampler keeps a loaded instrument's samples in memory: an open one plays the old ones.
        ui.notify(f"Installed to {target}. In DecentSampler, open it from the library browser "
                  "(rescan the folder if it doesn't show up). If it's open already, restart DecentSampler "
                  "to hear the new samples.", type="positive", multi_line=True)

    async def do_package() -> None:
        path = await run.io_bound(bundle.package, inst, state["result"].bundle)
        ui.notify(f"Wrote {path}", type="positive", multi_line=True)

    def do_download() -> None:
        font = state["result"].soundfont
        ui.download.file(font, font.name)

    async def do_musescore() -> None:
        try:
            target = await run.io_bound(sf2.install, state["result"].soundfont, inst.meta.slug)
        except ValueError as e:
            ui.notify(str(e), type="warning", multi_line=True)
            return
        ui.notify(f"Copied to {target}. Restart MuseScore, then choose “{inst.meta.name}” as the sound of a "
                  "staff in the Mixer.", type="positive", multi_line=True)

    build_btn.on_click(do_build)
    install_btn.on_click(do_install)
    package_btn.on_click(do_package)
    bundle_finder.on_click(lambda: reveal(state["result"].bundle))
    download_btn.on_click(do_download)
    musescore_btn.on_click(do_musescore)
    font_finder.on_click(lambda: reveal(state["result"].soundfont))
    built()

