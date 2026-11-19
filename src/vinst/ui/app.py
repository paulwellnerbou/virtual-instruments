"""The instrument builder wizard (NiceGUI)."""

from collections.abc import Callable
from typing import Any

import nicegui
from nicegui import ui
from pydantic import ValidationError

from vinst import defaults, notes, review, store
from vinst.plan import (
    SampledNote,
    Summary,
    playable_range,
    recording_passes,
    sampled_notes,
    summarize,
)
from vinst.spec import (
    SUSTAINED_TYPES,
    TYPE_LABELS,
    Extra,
    Instrument,
    InstrumentString,
    InstrumentType,
    Signal,
    Sound,
    slugify,
)
from vinst.ui import anim, keyboard, recording, theme
from vinst.ui.build_steps import step_build, step_sound, velocity_layers
from vinst.ui.finder import can_choose_folder, choose_folder
from vinst.ui.worksheet import settings_cards, step_worksheet

APP_TITLE = "DecentSampler Instrument Builder"
DECENTSAMPLER_URL = "https://www.decentsamples.com/product/decent-sampler-plugin/"
SIGNAL_IDS = {"Pickup": "pickup", "Piezo": "piezo", "Internal mic": "mic", "External mic": "extmic", "DI": "di",
              "Room mic": "room", "Close mic": "close"}
LAYER_PRESETS = {1: ["normal"], 2: ["p", "f"], 3: ["p", "mf", "f"], 4: ["pp", "p", "mf", "f"]}
MODE_LABELS = {"strings": "Strings & frets", "notes": "Note range", "sounds": "Individual sounds"}
TYPE_OPTIONS = {t.value: label for t, label in TYPE_LABELS.items()}


def recording_status(s: Summary) -> str:
    if s.recorded_files == 0:
        return "Not recorded yet"
    if s.recorded_files >= s.files:
        return "All files recorded"
    return f"Recorded {s.recorded_files} of {s.files} files"


def imported_notes(inst: Instrument) -> dict[int, int] | None:
    """note -> number of samples, from the last import; None if nothing was imported yet."""
    try:
        manifest = review.load(store.manifest_path(inst.meta.slug))
    except Exception:  # unreadable hits.json is reported in step 6
        return None
    if manifest is None or manifest.source != "samples":  # e.g. sliced takes from before switching
        return None
    return {t.midi: len([h for h in t.hits if not h.rejected]) for t in manifest.takes if t.midi is not None}


def import_status(inst: Instrument) -> str:
    found = imported_notes(inst)
    if not found:
        return "Not imported yet"
    lo, hi = min(found), max(found)
    return f"Imported: {len(found)} notes, {sum(found.values())} samples, {notes.label(lo)} – {notes.label(hi)}"


def not_needed(text: str) -> None:
    ui.label(text).classes("opacity-70")


theme.apply()


WORKSHEET = "Worksheet"
STEPS_CSS = """
.vi-side { width: 24rem; transition: width .25s ease; }
.vi-side.vi-side-wide { width: 30rem; }
.vi-side.vi-side-sticky { position: sticky; top: 5rem; }
.vi-side .vi-collapse.vi-open > .vi-collapse-inner { overflow: visible; }
.vi-steps .q-stepper__header { flex-wrap: wrap; justify-content: flex-start; }
.vi-steps .q-stepper__tab { flex: 0 0 25%; max-width: 25%; }
/* Chrome anchors the scroll position to the focused element (a chip being dragged, the button just
   clicked); a redraw replaces it and the page jumps. The steps redraw in place, so no anchoring. */
.vi-steps .q-stepper__step-inner { overflow-anchor: none; }
"""  # seven steps: four on the first line, then the worksheet starts the second


def header() -> None:
    anim.add_css()
    ui.add_css(STEPS_CSS)
    theme.add_css()
    recording.add_assets()
    with ui.header().classes("items-center justify-between"):
        ui.link(APP_TITLE, "/").classes("text-white text-lg no-underline")
        with ui.row().classes("items-center gap-1 text-sm"):
            ui.label("Builds instruments for")
            ui.link("DecentSampler", DECENTSAMPLER_URL, new_tab=True).classes("text-white font-medium")
            ui.icon("open_in_new").classes("text-sm")


@ui.page("/")
def home() -> None:
    header()
    with ui.column().classes("w-full max-w-4xl mx-auto gap-4"):
        ui.label("Instruments").classes("text-2xl")
        instruments, broken = store.scan()
        for slug, error in broken.items():
            with ui.card().classes("w-full"):
                ui.label(f"{slug}: {store.SPEC_FILE} can't be read").classes("text-lg font-medium text-red-600")
                ui.label(str(store.instrument_dir(slug) / store.SPEC_FILE)).classes("text-xs opacity-60")
                ui.label(error).classes("text-sm font-mono whitespace-pre-wrap")
        for inst in instruments:
            with ui.card().classes("w-full"), ui.row().classes("w-full items-center"):
                with ui.column().classes("gap-0 grow"):
                    ui.label(inst.meta.name).classes("text-lg font-medium")
                    if inst.recording.source == "samples":
                        ui.label(f"{TYPE_LABELS[inst.meta.type]} · imported samples").classes("text-sm opacity-70")
                        ui.label(import_status(inst)).classes("text-sm opacity-70")
                    else:
                        s = summarize(inst)
                        ui.label(f"{TYPE_LABELS[inst.meta.type]} · {s.range_text}").classes("text-sm opacity-70")
                        ui.label(f"{recording_status(s)} · plan: {s.sampled} notes, {s.hits} hits, "
                                 f"~{s.sample_mb:,.0f} MB of samples").classes("text-sm opacity-70")
                ui.button("Duplicate", on_click=lambda i=inst: duplicate_dialog(i)).props("flat")
                ui.button("Open", on_click=lambda i=inst: ui.navigate.to(f"/i/{i.meta.slug}"))

        with ui.card().classes("w-full"):
            ui.label("New instrument").classes("text-lg font-medium")
            with ui.row().classes("w-full items-end"):
                name = ui.input("Name").classes("grow")
                kind = ui.select(TYPE_OPTIONS, label="Type", value=InstrumentType.PLUCKED.value).classes("w-72")

                def create() -> None:
                    if not name.value.strip():
                        ui.notify("Give it a name first.", type="warning")
                        return
                    inst = store.create(defaults.new_instrument(name.value.strip(), InstrumentType(kind.value)))
                    ui.navigate.to(f"/i/{inst.meta.slug}")

                ui.button("Create", on_click=create)

        with ui.card().classes("w-full"):
            ui.label("Settings").classes("text-lg font-medium")
            settings = store.load_settings()

            def set_recordings(value: str) -> None:
                settings.recordings_dir = value
                store.save_settings(settings)

            folder_field("Recordings folder", settings.recordings_dir, set_recordings,
                         "Folder where new instruments keep their takes", wide=True)
            ui.label("New instruments record into a folder named after the instrument inside it.") \
                .classes("text-xs opacity-60")


def duplicate_dialog(inst: Instrument) -> None:
    with ui.dialog() as dialog, ui.card():
        ui.label(f"Duplicate “{inst.meta.name}”").classes("text-lg")
        name = ui.input("New name", value=f"{inst.meta.name} copy").classes("w-80")
        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")

            def go() -> None:
                copy = store.duplicate(inst.meta.slug, name.value.strip() or f"{inst.meta.name} copy")
                ui.navigate.to(f"/i/{copy.meta.slug}")

            ui.button("Duplicate", on_click=go)
    dialog.open()


def folder_field(label: str, value: str, on_set: Callable[[str], None], prompt: str, wide: bool = False,
                 **kw: Any) -> ui.input:
    with ui.row().classes("items-center no-wrap grow gap-1" + (" w-full" if wide else "")):
        inp = ui.input(label, value=value, on_change=lambda e: on_set(e.value), **kw).classes("grow")
        if can_choose_folder():
            async def choose() -> None:
                # Not run(): this module's own run() starts the app.
                picked = await nicegui.run.io_bound(choose_folder, prompt, inp.value or "")
                if picked:
                    inp.set_value(picked)

            ui.button("Choose…", icon="folder", on_click=choose).props("flat dense")
    return inp


class Editor:
    """Per-page editing state: the instrument, autosave, and the refreshable summary."""

    def __init__(self, inst: Instrument) -> None:
        self.inst = inst
        self.dirty = False
        self.status: ui.label | None = None
        self.on_change_hooks: list[Callable[[], None]] = []

    def changed(self) -> None:
        self.dirty = True
        if self.status:
            self.status.set_text("Unsaved…")
        self.refresh()

    def refresh(self) -> None:
        """Redraw what depends on the instrument, e.g. after hits.json changed."""
        for hook in self.on_change_hooks:
            hook()

    def set(self, obj: Any, attr: str, value: Any) -> None:
        if getattr(obj, attr) != value:
            setattr(obj, attr, value)
            self.changed()

    def autosave(self) -> None:
        if not self.dirty:
            return
        try:
            Instrument.model_validate(self.inst.model_dump())
        except ValidationError as e:
            if self.status:
                self.status.set_text(f"Not saved: {e.errors()[0]['msg']}")
            return
        store.save(self.inst)
        self.dirty = False
        if self.status:
            self.status.set_text("Saved")

    # Field helpers bound to model attributes.

    def text(self, label: str, obj: Any, attr: str, **kw: Any) -> ui.input:
        return ui.input(label, value=getattr(obj, attr),
                        on_change=lambda e: self.set(obj, attr, e.value), **kw)

    def number(self, label: str, obj: Any, attr: str, *, cast: type = int, **kw: Any) -> ui.number:
        def handle(e: Any) -> None:
            if e.value is not None:
                self.set(obj, attr, cast(e.value))

        return ui.number(label, value=getattr(obj, attr), on_change=handle, **kw)

    def folder(self, label: str, obj: Any, attr: str, prompt: str, **kw: Any) -> ui.input:
        """A path field with a "Choose…" button that opens the system's folder dialog."""
        return folder_field(label, getattr(obj, attr), lambda v: self.set(obj, attr, v), prompt, **kw)

    def note(self, label: str, obj: Any, attr: str, **kw: Any) -> ui.input:
        def check(v: str) -> str | None:
            try:
                notes.parse(v)
            except ValueError:
                return "e.g. G2, C#3 or 60"
            return None

        inp = ui.input(label, value=notes.name(getattr(obj, attr)), validation=check, **kw)
        inp.props(f'hint="MIDI {getattr(obj, attr)}"')

        def handle(e: Any) -> None:
            try:
                midi = notes.parse(e.value)
            except ValueError:
                return
            inp.props(f'hint="MIDI {midi}"')
            self.set(obj, attr, midi)

        inp.on_value_change(handle)
        return inp


@ui.page("/i/{slug}")
def editor_page(slug: str) -> None:
    header()
    try:
        ed = Editor(store.load(slug))
        if not ed.inst.recording.takes_dir:
            # Instruments from before the recordings-folder setting: use its default, as new ones do.
            ed.inst.recording.takes_dir = store.default_takes_dir(ed.inst.meta.name)
            ed.dirty = True
    except Exception as e:  # missing or hand-edited spec that doesn't validate
        with ui.column().classes("w-full max-w-4xl mx-auto"):
            ui.label(f"Can't open “{slug}”").classes("text-2xl")
            ui.label(str(store.instrument_dir(slug) / store.SPEC_FILE)).classes("text-xs opacity-60")
            ui.label(str(e)).classes("text-sm font-mono whitespace-pre-wrap")
            ui.link("Back to the instruments", "/")
        return
    inst = ed.inst
    ui.timer(0.8, ed.autosave)

    with ui.row().classes("w-full max-w-7xl mx-auto no-wrap items-start gap-6"):
        with ui.column().classes("grow min-w-0"):
            with ui.row().classes("items-baseline gap-4"):
                title = ui.label(inst.meta.name).classes("text-2xl")
                ed.status = ui.label("Saved").classes("text-sm opacity-60")
            ed.on_change_hooks.append(lambda: title.set_text(inst.meta.name))

            with ui.stepper().props("header-nav animated flat").classes("w-full vi-steps") as stepper:
                with ui.step("Instrument", icon="music_note"):
                    step_instrument(ed)
                    nav(stepper, first=True)
                with ui.step("Signals", icon="settings_input_component"):
                    step_signals(ed)
                    nav(stepper)
                importing = inst.recording.source == "samples"
                with ui.step("Range", icon="piano"):
                    if importing:
                        not_needed("Not needed when importing: the notes come from the samples (step 5).")
                    else:
                        step_range(ed)
                    nav(stepper)
                with ui.step("Dynamics & extras", icon="graphic_eq"):
                    step_dynamics(ed)
                    nav(stepper)
                with ui.step(WORKSHEET, icon="view_list"):
                    step_worksheet(ed)
                    nav(stepper)
                with ui.step("Sound & look", icon="tune"):
                    step_sound(ed)
                    nav(stepper)
                with ui.step("Build", icon="inventory_2"):
                    step_build(ed)
                    nav(stepper, last=True)

        # The right column: the summary, and on the worksheet its settings, apart from the notes. It
        # stays in view on the other steps; on the worksheet it's wider for the settings and scrolls
        # with the page, so its cards are never cut off.
        with ui.column().classes("vi-side shrink-0 gap-4") as side:
            with ui.card().classes("w-full"):
                summary_panel(ed)
            with anim.Collapse(stepper.value == WORKSHEET, inner_classes="flex flex-col gap-4") as worksheet_side:
                settings_cards(ed)
            worksheet_side.outer.classes("w-full")  # as wide as the summary card above

        def side_for(step: str) -> None:
            worksheet_side.set_open(step == WORKSHEET)
            side.classes(add="vi-side-wide" if step == WORKSHEET else "vi-side-sticky",
                         remove="vi-side-sticky" if step == WORKSHEET else "vi-side-wide")

        side_for(stepper.value)
        stepper.on_value_change(lambda e: side_for(e.value))


def nav(stepper: ui.stepper, first: bool = False, last: bool = False) -> None:
    with ui.stepper_navigation():
        if not first:
            ui.button("Back", on_click=stepper.previous).props("flat")
        if not last:
            ui.button("Next", on_click=stepper.next)


def summary_panel(ed: Editor) -> None:
    @ui.refreshable
    def body() -> None:
        inst = ed.inst
        ui.label("Summary").classes("text-lg font-medium")
        if inst.recording.source == "samples":
            found = imported_notes(inst) or {}
            span = (min(found), max(found)) if found else None
            ui.html(keyboard.render([SampledNote(midi=m) for m in found], span, []), sanitize=False) \
                .classes("w-full")
            ui.label(import_status(inst)).classes("text-sm font-medium")
            ui.label(f"Signals: {', '.join(x.name for x in inst.signals) or '–'}").classes("text-sm opacity-70")
            for p in inst.problems():
                ui.label(f"⚠ {p}").classes("text-sm text-orange-600")
            return
        s = summarize(inst)
        strings = [st.name for st in inst.range.strings] if inst.range.mode == "strings" else []
        ui.html(keyboard.render(sampled_notes(inst), playable_range(inst), strings), sanitize=False) \
            .classes("w-full")
        ui.label(recording_status(s)).classes("text-sm font-medium")
        with ui.grid(columns=2).classes("gap-x-4 gap-y-1 text-sm"):
            for k, v in [("Range", s.range_text), ("Notes to sample", s.sampled), ("Takes to record", s.takes),
                         ("Hits to play", s.hits), ("Take files", s.files),
                         ("Sample data (est.)", f"~{s.sample_mb:,.0f} MB"),
                         ("Session length (est.)", f"~{s.minutes:.0f} min"),
                         ("Signals", ", ".join(x.name for x in inst.signals) or "–")]:
                ui.label(k).classes("opacity-70")
                ui.label(str(v))
        for p in inst.problems():
            ui.label(f"⚠ {p}").classes("text-sm text-orange-600")

    body()
    ed.on_change_hooks.append(body.refresh)


def step_instrument(ed: Editor) -> None:
    inst, meta = ed.inst, ed.inst.meta
    with ui.column().classes("w-full max-w-xl"):
        ed.text("Name", meta, "name").classes("w-full")
        with ui.row().classes("w-full"):
            ed.text("Author", meta, "author").classes("grow")
            ed.text("Version", meta, "version").classes("w-32")
        ed.text("Description", meta, "description").props("autogrow type=textarea").classes("w-full")

        def source_changed(e: Any) -> None:
            # Each mode has its own default output folder; a folder chosen for sliced takes could
            # be the very folder an import reads from.
            inst.recording.samples_dir = ""
            ed.set(inst.recording, "source", e.value)
            ed.autosave()
            # Steps 3–6 show different things for imports; rebuild them.
            ui.navigate.reload()

        ui.label("Samples").classes("text-sm opacity-70 mt-2")
        ui.toggle({"takes": "Record new takes", "samples": "Import existing samples"},
                  value=inst.recording.source, on_change=source_changed)

        async def type_changed(e: Any) -> None:
            new = InstrumentType(e.value)
            if new == meta.type:
                return
            meta.type = new
            ed.changed()
            with ui.dialog() as dialog, ui.card():
                ui.label(f"Use the defaults for “{TYPE_LABELS[new]}”?")
                ui.label("This replaces range, dynamics, extras and recording settings.").classes("text-sm opacity-70")
                with ui.row():
                    ui.button("Keep mine", on_click=lambda: dialog.submit(False)).props("flat")
                    ui.button("Use defaults", on_click=lambda: dialog.submit(True))
            if await dialog:
                takes_dir = inst.recording.takes_dir
                inst.range, inst.dynamics, inst.extras, inst.recording = defaults.type_defaults(new)
                inst.recording.takes_dir = takes_dir
                inst.processing.loops = new in SUSTAINED_TYPES
                ed.changed()
                ed.autosave()
                # The other steps hold references to the replaced sub-models; rebuild them.
                ui.navigate.reload()

        ui.select(TYPE_OPTIONS, label="Type", value=meta.type.value, on_change=type_changed).classes("w-full")
        ui.label(f"Folder: {store.instrument_dir(meta.slug)}").classes("text-xs opacity-60")


def step_signals(ed: Editor) -> None:
    inst = ed.inst
    ui.markdown(
        "The sources you record, e.g. a pickup, an external mic or a DI. The id is the track name and file "
        "suffix.\n\n"
        "- **Together**: recorded at the same time as the other “together” signals, so it's the same "
        "performance. The instrument can blend them with a mix knob.\n"
        "- **Own pass**: recorded separately, e.g. piezo and internal mic that share one output and are "
        "switched by a knob inside the instrument. Every take is recorded once more for this signal. "
        "Because it's a different performance, the instrument switches between passes (or layers them, "
        "step 7) instead of blending."
    ).classes("max-w-3xl")

    @ui.refreshable
    def rows() -> None:
        for sig in inst.signals:
            with ui.row().classes("items-center"):
                ed.text("Name", sig, "name").classes("w-56")
                ed.text("Id (track name)", sig, "id").classes("w-40")
                ui.select({"together": "Together", "separate": "Own pass"}, label="Recorded",
                          value=sig.recorded,
                          on_change=lambda e, s=sig: (ed.set(s, "recorded", e.value), passes_label.refresh())
                          ).classes("w-36")

                def remove(s: Signal = sig) -> None:
                    inst.signals.remove(s)
                    ed.changed()
                    rows.refresh()
                    passes_label.refresh()

                ui.button(icon="delete", on_click=remove).props("flat round")
        with ui.row().classes("items-center gap-2"):
            present = {s.name for s in inst.signals}
            for name in defaults.SIGNAL_SUGGESTIONS:
                if name not in present:
                    ui.button(f"+ {name}", on_click=lambda n=name: add(n, SIGNAL_IDS.get(n))).props("outline dense")
            ui.button("+ Other", on_click=lambda: add("New signal", None)).props("outline dense")

    @ui.refreshable
    def passes_label() -> None:
        passes = recording_passes(inst)
        if len(passes) > 1:
            text = " → ".join(f"{i}. {p.name}" for i, p in enumerate(passes, 1))
            ui.label(f"Recording passes: {text}").classes("text-sm font-medium")

    def add(name: str, sid: str | None) -> None:
        inst.signals.append(Signal(id=sid or slugify(name).replace("-", ""), name=name))
        ed.changed()
        rows.refresh()
        passes_label.refresh()

    rows()
    passes_label()


def step_range(ed: Editor) -> None:
    inst = ed.inst
    rng = inst.range

    def mode_changed(e: Any) -> None:
        rng.mode = e.value
        if rng.mode == "strings" and not rng.strings:
            rng.strings = defaults.tuning_strings("Ukulele, low G (G C E A)")
        if rng.mode == "sounds" and not rng.sounds:
            rng.sounds = [Sound(name="Hit", key=60)]
        ed.changed()
        body.refresh()

    ui.toggle(MODE_LABELS, value=rng.mode, on_change=mode_changed)

    @ui.refreshable
    def body() -> None:
        if rng.mode == "strings":
            strings_editor()
        elif rng.mode == "notes":
            with ui.row().classes("items-start"):
                ed.note("Lowest note", rng, "low").classes("w-36")
                ed.note("Highest note", rng, "high").classes("w-36")
            density_select()
        else:
            sounds_editor()
        ui.label("Note names follow DecentSampler: middle C = C3 = MIDI 60.").classes("text-xs opacity-60")
        if rng.mode != "sounds":
            stretch()

    def stretch() -> None:
        """Keys beyond the recorded notes: playback only, so no recording or slicing again."""
        p = inst.preset
        ui.label("Beyond the recorded notes").classes("text-sm font-medium mt-4")
        with ui.row().classes("items-start gap-4"):
            ed.number("Stretch lowest note down", p, "extend_below", min=0, max=24, precision=0,
                      suffix="semitones").classes("w-56")
            ed.number("Stretch highest note up", p, "extend_above", min=0, max=24, precision=0,
                      suffix="semitones").classes("w-56")

        playable()

    @ui.refreshable
    def playable() -> None:
        span = rng.span()
        if span is None or rng.mode == "sounds":
            return
        p = inst.preset
        lo, hi = max(0, span[0] - p.extend_below), min(127, span[1] + p.extend_above)
        ui.label(f"Plays {notes.label(lo)} – {notes.label(hi)}: the lowest and highest recorded samples are "
                 "pitched further. Only the built instrument changes; nothing needs recording or slicing "
                 "again. More than 2–3 semitones starts to sound unnatural.").classes("text-xs opacity-70 max-w-3xl")

    ed.on_change_hooks.append(playable.refresh)  # once; a no-op while it isn't shown

    def density_select() -> None:
        ui.select({1: "Every note (chromatic)", 2: "Every 2nd semitone", 3: "Every 3rd semitone (minor thirds)",
                   4: "Every 4th semitone (major thirds)", 6: "Every tritone", 12: "Every octave"},
                  label="Sampling density", value=rng.density,
                  on_change=lambda e: ed.set(rng, "density", e.value)).classes("w-72")

    def strings_editor() -> None:
        ui.markdown("List the strings **as they sit on the instrument** (e.g. G C E A). Each note is recorded "
                    "once, on the string where it's played at the lowest fret.").classes("max-w-2xl")
        with ui.row().classes("items-end"):
            preset = ui.select(list(defaults.TUNINGS), label="Tuning preset",
                               value=None).classes("w-80")
            frets = ui.number("Frets", value=rng.strings[0].frets if rng.strings else 12, min=0, max=36,
                              precision=0).classes("w-24")

            def apply() -> None:
                if not preset.value:
                    ui.notify("Pick a tuning preset first.", type="warning")
                    return
                rng.strings = defaults.tuning_strings(preset.value, int(frets.value or 0))
                ed.changed()
                strings_rows.refresh()

            ui.button("Apply", on_click=apply).props("outline")

        @ui.refreshable
        def strings_rows() -> None:
            for st in rng.strings:
                with ui.row().classes("items-start"):
                    ed.text("String", st, "name").classes("w-28")
                    ed.note("Open note", st, "open").classes("w-32")
                    ed.number("Frets", st, "frets", min=0, max=36, precision=0).classes("w-24")

                    def remove(s: InstrumentString = st) -> None:
                        rng.strings.remove(s)
                        ed.changed()
                        strings_rows.refresh()

                    ui.button(icon="delete", on_click=remove).props("flat round").classes("mt-2")

            def add() -> None:
                top = max((s.open for s in rng.strings), default=55)
                rng.strings.append(InstrumentString(name=f"S{len(rng.strings) + 1}", open=min(127, top + 5),
                                                    frets=rng.strings[-1].frets if rng.strings else 12))
                ed.changed()
                strings_rows.refresh()

            ui.button("+ String", on_click=add).props("outline dense")

        strings_rows()
        density_select()
        mapping_table()

    @ui.refreshable
    def mapping_table() -> None:
        groups: dict[str, list[str]] = {}
        for n in sampled_notes(inst):
            groups.setdefault(n.string or "", []).append(f"{notes.name(n.midi)}·{n.fret}")
        with ui.column().classes("gap-1 text-sm"):
            for string, items in groups.items():
                with ui.row().classes("gap-2 items-baseline"):
                    ui.label(f"{string} string:").classes("w-24 opacity-70")
                    ui.label("  ".join(items)).classes("font-mono text-xs")

    # Registered once; refreshing is a no-op while the strings editor isn't shown.
    ed.on_change_hooks.append(mapping_table.refresh)

    def sounds_editor() -> None:
        ui.markdown("One key per sound (e.g. body knock, chuck, slap).")

        @ui.refreshable
        def sound_rows() -> None:
            for snd in rng.sounds:
                with ui.row().classes("items-start"):
                    ed.text("Sound", snd, "name").classes("w-56")
                    ed.note("Key", snd, "key").classes("w-32")

                    def remove(s: Sound = snd) -> None:
                        rng.sounds.remove(s)
                        ed.changed()
                        sound_rows.refresh()

                    ui.button(icon="delete", on_click=remove).props("flat round").classes("mt-2")

            def add() -> None:
                used = {s.key for s in rng.sounds}
                key = next(k for k in range(60, 128) if k not in used)
                rng.sounds.append(Sound(name=f"Sound {len(rng.sounds) + 1}", key=key))
                ed.changed()
                sound_rows.refresh()

            ui.button("+ Sound", on_click=add).props("outline dense")

        sound_rows()

    body()


def step_dynamics(ed: Editor) -> None:
    inst, dyn, rec = ed.inst, ed.inst.dynamics, ed.inst.recording
    ui.label("Velocities").classes("text-base font-medium")
    ui.label("The dynamics every note is recorded at, quietest first, each in its own take. These are the "
             "defaults: in the worksheet, single notes can get their own velocities and ranges.") \
        .classes("text-sm opacity-70 max-w-3xl")

    @ui.refreshable
    def layers() -> None:
        with ui.row().classes("items-center"):
            ui.toggle({n: str(n) for n in LAYER_PRESETS}, value=len(dyn.layers) if len(dyn.layers) in LAYER_PRESETS
                      else None, on_change=lambda e: set_layers(LAYER_PRESETS[e.value]) if e.value else None)
            for i in range(len(dyn.layers)):
                def rename(e: Any, i: int = i) -> None:
                    dyn.layers[i] = e.value
                    ed.changed()

                ui.input(value=dyn.layers[i], on_change=rename).classes("w-20").props("dense")

    def set_layers(names: list[str]) -> None:
        if names != dyn.layers:
            dyn.layers = list(names)
            ed.changed()
            layers.refresh()

    layers()
    if inst.recording.source == "samples":
        velocity_layers(ed)
        ui.label("When importing: a sample whose file name contains a velocity name (e.g. “_mf_”) goes to that "
                 "velocity; a velocity number like “v2” is ranked; otherwise the samples of each note are "
                 "spread over the velocities by loudness, and several samples in one velocity become "
                 "variations.").classes("text-sm opacity-70 max-w-3xl")
        return
    velocity_layers(ed)
    with ui.row():
        ed.number("Variations per velocity", dyn, "round_robins", min=1, max=32, precision=0).classes("w-52") \
            .tooltip("How many times each note is played per velocity; DecentSampler alternates between them")
        ed.number("Ring / hold time per hit", rec, "ring_seconds", cast=float, min=0.5, max=60, step=0.5,
                  suffix="s").classes("w-56")
        ed.number("Noise floor", rec, "noise_floor_seconds", min=0, max=300, precision=0, suffix="s").classes("w-36")

    ui.separator()
    ui.label("Extras").classes("text-base font-medium")
    ui.label("Non-note recordings: release noises, chucks, body knocks, … Each becomes its own take."
             ).classes("text-sm opacity-70")

    @ui.refreshable
    def extras() -> None:
        for x in inst.extras:
            with ui.row().classes("items-start w-full no-wrap"):
                ed.text("Name", x, "name").classes("w-48")
                ed.text("Id", x, "id").classes("w-28")
                ed.number("Hits", x, "hits", min=1, max=200, precision=0).classes("w-20")
                ed.text("Instructions", x, "instructions").classes("grow")

                def remove(item: Extra = x) -> None:
                    inst.extras.remove(item)
                    ed.changed()
                    extras.refresh()

                ui.button(icon="delete", on_click=remove).props("flat round").classes("mt-2")

        def add() -> None:
            n = len(inst.extras) + 1
            inst.extras.append(Extra(id=f"extra{n}", name=f"Extra {n}"))
            ed.changed()
            extras.refresh()

        ui.button("+ Extra", on_click=add).props("outline dense")

    extras()


def run(port: int = 8080, show: bool = True, idle_exit: float = 0) -> None:
    if idle_exit > 0:
        from vinst.ui import idle

        idle.enable(idle_exit)
    ui.run(title=APP_TITLE, port=port, show=show, reload=False, dark=None, favicon="🎸", host="127.0.0.1",
           show_welcome_message=idle_exit <= 0)
