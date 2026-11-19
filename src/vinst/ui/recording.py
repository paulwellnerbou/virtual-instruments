"""Recording in the app: input settings, and a dialog that records a take or a whole pass.

The dialog shows the input levels from the moment it opens, so the gain can be set before
starting. While recording, a visual metronome says what to play: a count-in, then each hit on
beat 1 of its bar, how to play it, how long to let it ring and what comes next. The browser
animates it on its own clock; the server only decides when recording starts and stops.
"""

import asyncio
import json
import math
import time
from html import escape
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import ui

from vinst import guide as guides
from vinst import recorder, store, takes_meta
from vinst.plan import more_base, more_file, plan_takes, recording_passes
from vinst.plan import take_key as take_key_of
from vinst.spec import Signal
from vinst.ui import form

if TYPE_CHECKING:
    from vinst.ui.app import Editor

SILENCE_WARNING_S = 2.0
MAX_HITS = 12

CSS = """
.vi-rec { display: flex; flex-direction: column; align-items: center; gap: 6px; min-height: 330px; }
.vi-rec-stage { position: relative; width: 150px; height: 150px; display: grid; place-items: center; }
.vi-rec-pulse { position: absolute; inset: 0; border-radius: 50%; border: 6px solid #9e9e9e;
                transition: background-color .08s, border-color .08s; }
.vi-rec[data-kind=count] .vi-rec-pulse { border-color: #f9a825; }
.vi-rec[data-play=true] .vi-rec-pulse { background: #e53935; border-color: #e53935; }
.vi-rec[data-kind=silence] .vi-rec-pulse { border-color: #546e7a; border-style: dashed; }
.vi-rec-big { position: relative; font-size: 2.6rem; font-weight: 600; }
.vi-rec[data-play=true] .vi-rec-big { color: white; }
.vi-rec-title { font-size: 1.6rem; font-weight: 600; }
.vi-rec-detail { font-size: 1.15rem; min-height: 1.5em; }
.vi-rec-beats { display: flex; gap: 8px; min-height: 16px; }
.vi-rec-beats span { width: 14px; height: 14px; border-radius: 50%; background: #e0e0e0;
                     transition: background-color .08s, transform .08s; }
.vi-rec-beats span.on { background: #1976d2; transform: scale(1.3); }
.vi-rec-beats span.one { outline: 2px solid #1976d2; outline-offset: 1px; }
.vi-rec-bar, .vi-rec-total { width: 100%; height: 8px; background: #eeeeee; border-radius: 4px; overflow: hidden; }
.vi-rec-bar > div { height: 100%; background: #1976d2; }
.vi-rec-total { height: 4px; } .vi-rec-total > div { height: 100%; background: #9e9e9e; }
.vi-rec-time { opacity: .7; font-size: .9rem; min-height: 1.3em; }
.vi-meter { display: grid; grid-template-columns: 7rem 1fr 4.5rem; align-items: center; gap: 8px; width: 100%; }
.vi-meter-bar { height: 10px; background: #eeeeee; border-radius: 5px; overflow: hidden; }
.vi-meter-bar > div { height: 100%; width: 0; background: #43a047; transition: width .08s linear; }
.vi-meter-bar > div.hot { background: #fdd835; } .vi-meter-bar > div.over { background: #e53935; }
.vi-meter-db { text-align: right; font-variant-numeric: tabular-nums; font-size: .85rem; }
.vi-meter-db.clip { color: #e53935; font-weight: 600; }
"""

JS = r"""
window.viRec = (() => {
  let raf = null, audio = null, last = null;
  const part = (root, name) => root.querySelector('.vi-rec-' + name);
  const clock = s => { s = Math.max(0, Math.ceil(s)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
  function blip(accent) {
    if (!audio) return;
    const o = audio.createOscillator(), g = audio.createGain(), t = audio.currentTime;
    o.frequency.value = accent ? 1760 : 1100;
    g.gain.setValueAtTime(accent ? 0.35 : 0.2, t);
    g.gain.exponentialRampToValueAtTime(0.001, t + 0.06);
    o.connect(g).connect(audio.destination); o.start(t); o.stop(t + 0.07);
  }
  function show(root, g, t) {
    const i = g.steps.findIndex(s => t < s.at + s.length);
    const s = g.steps[i >= 0 ? i : g.steps.length - 1];
    const local = Math.min(t - s.at, s.length);
    const beat = s.beats ? Math.min(Math.floor(local / g.beat), s.beats - 1) : -1;
    const phase = s.beats ? (local / g.beat) % 1 : 0;
    const key = i + ':' + beat;
    if (i >= 0 && s.beats && key !== last) { last = key; blip(s.kind === 'hit' && beat === 0); }
    const play = s.kind === 'hit' && beat === 0;
    const stage = root.querySelector('.vi-rec'); stage.dataset.kind = s.kind; stage.dataset.play = play;
    part(root, 'title').textContent = i >= 0 ? s.title : 'Done';
    // Always what the current step asks for; "let it ring" only adds to it.
    part(root, 'detail').textContent = i < 0 ? '' : s.kind === 'hit' && beat > 0
      ? [s.detail, 'let it ring'].filter(Boolean).join(' · ') : s.detail;
    const left = s.length - local;
    part(root, 'big').textContent = i < 0 ? '✓' : s.kind === 'count' ? String(s.beats - beat)
      : play ? 'Play' : s.kind === 'silence' ? clock(left) : s.kind === 'hit' ? String(Math.ceil(left)) : '·';
    const pulse = part(root, 'pulse');
    pulse.style.transform = 'scale(' + (s.beats && i >= 0 ? 1 + 0.12 * Math.pow(1 - phase, 3) : 1) + ')';
    const dots = part(root, 'beats');
    if (dots.childElementCount !== s.beats) {
      dots.innerHTML = '';
      for (let b = 0; b < s.beats; b++) { const d = document.createElement('span'); if (b === 0) d.className = 'one'; dots.append(d); }
    }
    [...dots.children].forEach((d, b) => d.classList.toggle('on', b === beat && i >= 0));
    part(root, 'bar').firstElementChild.style.width = (100 * local / s.length) + '%';
    part(root, 'total').firstElementChild.style.width = Math.min(100, 100 * t / g.total) + '%';
    part(root, 'time').textContent = clock(t) + ' / ' + clock(g.total);
    return i >= 0;
  }
  function stop() {
    if (raf) cancelAnimationFrame(raf);
    raf = null; last = null;
    if (audio) { audio.close(); audio = null; }
  }
  function start(id, g, click) {
    stop();
    const root = document.getElementById(id);
    if (!root) return;
    if (click) { audio = new (window.AudioContext || window.webkitAudioContext)(); audio.resume(); }
    const t0 = performance.now();
    const frame = () => { if (show(root, g, (performance.now() - t0) / 1000)) raf = requestAnimationFrame(frame); };
    raf = requestAnimationFrame(frame);
  }
  function preview(id, g, t = 0) { const root = document.getElementById(id); if (root) show(root, g, t); }
  function meters(id, levels) {
    const root = document.getElementById(id);
    if (!root) return;
    for (const m of levels) {
      const row = root.querySelector('[data-signal="' + m.id + '"]');
      if (!row) continue;
      const bar = row.querySelector('.vi-meter-bar > div'), text = row.querySelector('.vi-meter-db');
      bar.style.width = Math.max(0, Math.min(100, (m.db + 60) / 60 * 100)) + '%';
      bar.className = m.db >= -3 ? 'over' : m.db >= -12 ? 'hot' : '';
      text.textContent = m.clip ? 'CLIP' : m.db > -100 ? m.db.toFixed(0) + ' dB' : '–';
      text.classList.toggle('clip', m.clip);
    }
  }
  return { start, stop, preview, meters };
})();
"""


def add_assets() -> None:
    """Once per page."""
    ui.add_css(CSS)
    ui.add_body_html(f"<script>{JS}</script>")


def signals_of(ed: "Editor", ids: list[str]) -> list[Signal]:
    by_id = {s.id: s for s in ed.inst.signals}
    return [by_id[i] for i in ids]


def takes_folder(ed: "Editor") -> Path:
    rec = ed.inst.recording
    if not rec.takes_dir:
        ed.set(rec, "takes_dir", store.default_takes_dir(ed.inst.meta.name))
    return Path(rec.takes_dir).expanduser()


Saved = Callable[[str], None]  # called with the key of the recording that was just saved


def record_take(ed: "Editor", number: int, on_saved: Saved | None = None) -> None:
    """Record a planned take; "Next" goes on to the following one. A velocity that's recorded
    already gets more hits beside its own instead: its hits and the decisions about them stay."""
    take = guides.take_by_number(ed.inst, number)
    if take is None:
        return
    folder = takes_folder(ed)
    paths = {sid: folder / name for sid, name in zip(take.signals, take.files)}
    if take.kind == "note" and all(p.exists() for p in paths.values()):
        record_more(ed, take.key, ed.inst.dynamics.round_robins, on_saved)
        return
    following = next((t for t in plan_takes(ed.inst) if t.number == number + 1), None)
    count = take.hits if take.kind == "note" else None  # extras and the noise floor are as planned
    open_dialog(ed, take.title, signals_of(ed, take.signals), paths,
                lambda n: guides.take_guide(ed.inst, take, n), count,
                (lambda: record_take(ed, following.number, on_saved)) if following else None,
                f"Next: {following.title}" if following else "",
                (lambda: on_saved(take.key)) if on_saved else None)


def record_more(ed: "Editor", take_key: str, count: int, on_saved: Saved | None = None) -> None:
    """Record more variations of a lane take, to compare with its own and keep the best of all."""
    from vinst.slicing import more_numbers

    take = next((t for t in plan_takes(ed.inst) if t.key == take_key), None)
    if take is None:
        return
    folder = takes_folder(ed)
    number = 1 + max(more_numbers(take, folder), default=0)
    paths = {sid: folder / more_file(take, number, sid) for sid in take.signals}
    key = take_key_of(more_base(take, number), take.pass_id)
    open_dialog(ed, f"{take.title} · more hits", signals_of(ed, take.signals), paths,
                lambda n: guides.more_guide(ed.inst, take, n or count), count, None, "",
                (lambda: on_saved(key)) if on_saved else None)


def current_device(rescan: bool = False) -> tuple[recorder.Device | None, list[recorder.Device], str]:
    """The chosen interface (None if it isn't connected), all inputs, and the chosen name."""
    devices = recorder.input_devices(rescan)
    name = store.load_settings().input_device
    return recorder.find_device(name, devices), devices, name


def open_dialog(ed: "Editor", title: str, signals: list[Signal], paths: dict[str, Path],
                guide_for: Callable[[int | None], guides.Guide], count: int | None,
                on_next: Callable[[], None] | None, next_label: str,
                on_saved: Callable[[], None] | None = None) -> None:
    """`count`: how many hits to ask for, which the player can change before recording; None for
    takes recorded as planned."""
    inst = ed.inst
    # Re-read the devices: the interface's sample rate may have changed since the app started.
    device, _, name = current_device(rescan=True)
    why = recorder.problem(inst, device, [s.id for s in signals])
    if device is None and name:
        why = f"{name} isn't connected. Connect it, or choose another interface under Recording."
    if why:
        ui.notify(why, type="warning", multi_line=True, timeout=8000)
        return
    assert device is not None
    channels = recorder.channels_for(inst)
    try:
        rec = recorder.Recorder(device, {s.id: channels[s.id] for s in signals}, inst.recording.sample_rate)
    except Exception as e:  # PortAudio refuses: device busy, unplugged, no permission
        ui.notify(f"Couldn't open {device.name}: {e}", type="negative", multi_line=True)
        return
    opened = time.monotonic()
    state: dict = {"timer": None, "guide": guide_for(count)}
    client = ui.context.client

    # At page level: the take table redraws after each saved take and would take the dialog with it.
    with client.layout, ui.dialog().props("persistent") as dialog, ui.card().classes("w-[36rem] max-w-full"):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            ui.label(title).classes("text-lg font-medium")
            with ui.row().classes("items-center gap-2 no-wrap"):
                ui.label(f"{device.name} · {device.sample_rate / 1000:g} kHz").classes("text-sm opacity-60")
                # Always here: while recording it cancels the take, like Cancel.
                with ui.button(icon="close", on_click=lambda: dialog.close()).props("flat dense round"):
                    close_tip = ui.tooltip("Close")
        root_id = f"vi-rec-{dialog.id}"
        with ui.element("div").classes("w-full").props(f"id={root_id}"):
            ui.html(
                '<div class="vi-rec">'
                '<div class="vi-rec-stage"><div class="vi-rec-pulse"></div><div class="vi-rec-big"></div></div>'
                '<div class="vi-rec-title"></div><div class="vi-rec-detail"></div><div class="vi-rec-beats"></div>'
                '<div class="vi-rec-bar"><div></div></div>'
                '<div class="vi-rec-total"><div></div></div><div class="vi-rec-time"></div></div>'
            ).classes("w-full")
            with ui.column().classes("w-full gap-1 mt-2"):
                for s in signals:
                    ui.html(f'<div class="vi-meter" data-signal="{escape(s.id)}">'
                            f'<span>{escape(s.name)} · input {channels[s.id]}</span>'
                            '<div class="vi-meter-bar"><div></div></div><span class="vi-meter-db">–</span></div>') \
                        .classes("w-full")
        silent = ui.label("No signal at all. Check the cable and input, and that macOS allows the builder to use "
                          "the microphone (System Settings → Privacy & Security → Microphone).") \
            .classes("text-sm text-negative")
        silent.set_visibility(False)
        if any(path.exists() for path in paths.values()):
            ui.label("Already recorded: a finished recording replaces it, a cancelled one keeps it.") \
                .classes("text-sm opacity-70")
        outcome = ui.column().classes("w-full gap-0 text-sm")
        click = ui.checkbox("Click sound (use headphones, so the click isn't recorded)", value=False)
        with ui.row().classes("w-full items-center justify-end"):
            hits_input = None
            if count is not None:
                hits_input = ui.number("Hits", value=count, min=1, max=MAX_HITS, step=1, precision=0,
                                       on_change=lambda e: set_count(e.value)) \
                    .props("dense outlined").classes("w-24")
                ui.space()
            cancel_btn = ui.button("Cancel", on_click=lambda: finish(False)).props("flat")
            keep_btn = ui.button("Stop and keep", on_click=lambda: finish(True)).props("outline")
            next_btn = ui.button(next_label or "Next", icon="skip_next", on_click=lambda: go_next()) \
                .props("outline")
            start_btn = ui.button("Record", icon="fiber_manual_record", on_click=lambda: start()).props("color=red")

    def set_count(value: float | None) -> None:
        if value is None or rec.recording:
            return
        state["guide"] = guide_for(max(1, min(MAX_HITS, int(value))))
        preview()

    def buttons(recording: bool, done: bool = False) -> None:
        start_btn.set_text("Record again" if done else "Record")
        start_btn.set_visibility(not recording)
        close_tip.set_text("Cancel the take and close" if recording else "Close")
        next_btn.set_visibility(done and on_next is not None)
        cancel_btn.set_visibility(recording)
        keep_btn.set_visibility(recording)
        click.set_enabled(not recording)
        if hits_input is not None:
            hits_input.set_enabled(not recording)

    def start() -> None:
        outcome.clear()
        guide = state["guide"]
        try:
            rec.start(paths, inst.recording.bit_depth, skip_seconds=guide.skip)
        except recorder.RecorderError as e:
            with outcome:
                ui.label(str(e)).classes("text-negative")
            return
        ui.run_javascript(f"viRec.start({json.dumps(root_id)}, {json.dumps(guide.to_dict())}, {json.dumps(click.value)})")
        state["timer"] = ui.timer(guide.total, lambda: finish(True, complete=True), once=True)
        buttons(True)

    def finish(keep: bool, complete: bool = False) -> None:
        if not rec.recording:
            return
        if state["timer"]:
            state["timer"].cancel()
            state["timer"] = None
        guide = state["guide"]
        # A take that ran its full time must have about that much audio; less means the interface stopped.
        result = rec.stop(keep, min_seconds=guide.total - guide.skip - 1 if complete else 0)
        for f in result.files:
            takes_meta.save(inst.meta.slug, f, guide.hits_at())
        ui.run_javascript(f"viRec.stop(); viRec.preview({json.dumps(root_id)}, {json.dumps(guide.to_dict())})")
        with outcome:
            if result.error:
                ui.label(f"Nothing was saved. {result.error} Record this again.").classes("text-negative")
            elif keep:
                ui.label(f"Saved {', '.join(p.name for p in result.files)} ({result.seconds:.0f} s).") \
                    .classes("text-positive")
                if result.overflows:
                    ui.label(f"{result.overflows} dropouts: the interface lost audio. Record this again; closing "
                             "other audio apps helps.").classes("text-negative")
                if result.clipped:
                    names = ", ".join(s.name for s in signals if s.id in result.clipped)
                    ui.label(f"Clipped: {names}. Turn the gain down and record again.").classes("text-negative")
            else:
                ui.label("Cancelled; nothing was changed.").classes("opacity-70")
        buttons(False, done=bool(result.files))
        if result.files:
            ed.refresh()
            if on_saved:
                on_saved()

    def levels() -> None:
        data = [{"id": s, "db": max(db, -120.0) if math.isfinite(db) else -120.0, "clip": s in rec.clipped}
                for s, db in rec.levels().items()]
        ui.run_javascript(f"viRec.meters({json.dumps(root_id)}, {json.dumps(data)})")
        silent.set_visibility(not rec.heard and time.monotonic() - opened > SILENCE_WARNING_S)

    def preview() -> None:
        ui.run_javascript(f"viRec.preview({json.dumps(root_id)}, {json.dumps(state['guide'].to_dict())})")

    with dialog:
        meter = ui.timer(0.1, levels)
        ui.timer(0.2, preview, once=True)

    def release() -> None:
        """Stop everything and free the interface; also when the page goes away mid-take."""
        if rec.closed:
            return
        meter.cancel()
        if state["timer"]:
            state["timer"].cancel()
        rec.close()  # an unfinished take is discarded

    def go_next() -> None:
        release()  # before the next take opens the interface again
        dialog.close()
        if on_next:
            on_next()

    def closed() -> None:
        release()
        ui.run_javascript("viRec.stop()")
        dialog.delete()

    dialog.on("hide", closed)
    client.on_disconnect(release)
    buttons(False)
    dialog.open()


def input_settings(ed: "Editor") -> None:
    """Which interface to record from, and which of its inputs each signal is plugged into."""
    inst, rec = ed.inst, ed.inst.recording
    state = {"rescan": True}  # the interface's rate may have changed since the app started

    @ui.refreshable
    def body() -> None:
        device, devices, name = current_device(rescan=state["rescan"])
        state["rescan"] = False
        with form.setting("Interface", "The audio interface the app records from. ⟳ looks again, e.g. after "
                                       "plugging one in or changing its sample rate in Audio MIDI Setup."):
            if devices:
                options = [d.name for d in devices] + ([name] if name and device is None else [])

                def choose(e) -> None:
                    s = store.load_settings()
                    s.input_device = e.value
                    store.save_settings(s)
                    body.refresh()

                ui.select(options, value=device.name if device else name or None, on_change=choose) \
                    .props("dense outlined").classes("grow min-w-0")
                if device:
                    same = device.sample_rate == rec.sample_rate
                    with ui.row().classes("items-center gap-1 no-wrap shrink-0"):
                        ui.icon("check_circle" if same else "warning", color="positive" if same else "warning",
                                size="18px")
                        ui.label(f"{device.sample_rate / 1000:g} kHz").classes("text-sm whitespace-nowrap")
                        ui.tooltip("The interface's current sample rate"
                                   + ("" if same else f"; this instrument records at {rec.sample_rate / 1000:g} kHz"))
            else:
                ui.label("no audio input found").classes("text-sm text-negative")
            ui.button(icon="refresh", on_click=rescan).props("flat dense round").tooltip("Look for interfaces again")
        if device is None:
            if name and devices:
                form.note(f"{name} isn't connected.")
            return
        channels = recorder.channels_for(inst)
        for p in recording_passes(inst):
            for s in p.signals:
                with form.setting(s.name, f"Which input of {device.name} the {s.name} is plugged into. "
                                          "Detect: play the instrument, and the input where it arrives is chosen."):
                    ui.select({n: f"Input {n}" for n in range(1, device.channels + 1)},
                              value=channels[s.id] if channels[s.id] <= device.channels else None,
                              on_change=lambda e, sid=s.id: e.value and ed.set(
                                  rec, "input_channels", {**rec.input_channels, sid: e.value})) \
                        .props("dense outlined").classes("grow min-w-0")
                    ui.button("Detect", icon="hearing", on_click=lambda _, sig=s: detect(device, sig)) \
                        .props("flat dense no-caps")
        why = recorder.problem(inst, device, [s.id for s in inst.signals])
        if why:
            form.note(why)

    def rescan() -> None:
        recorder.input_devices(rescan=True)
        body.refresh()

    async def detect(device: recorder.Device, sig: Signal) -> None:
        inputs = {f"in{n}": n for n in range(1, device.channels + 1)}
        try:
            probe = recorder.Recorder(device, inputs, device.sample_rate)
        except Exception as e:
            ui.notify(f"Couldn't open {device.name}: {e}", type="negative")
            return
        note = ui.notification(f"Play the {sig.name} now…", spinner=True, timeout=None)
        peaks = dict.fromkeys(inputs, -math.inf)
        found = None
        try:
            for tick in range(DETECT_S * 10):
                await asyncio.sleep(0.1)
                for k, level in probe.levels().items():
                    peaks[k] = max(peaks[k], level)
                found = loudest(peaks)
                if found and tick >= 10:
                    break
        finally:
            probe.close()
            note.dismiss()
        if found is None:
            ui.notify("No clear signal on one input. Play louder, or choose the input yourself.", type="warning")
            return
        ed.set(rec, "input_channels", {**rec.input_channels, sig.id: inputs[found]})
        ui.notify(f"{sig.name}: input {inputs[found]}", type="positive")
        body.refresh()

    body()
    ed.on_change_hooks.append(body.refresh)


DETECT_S = 8
DETECT_MIN_DB = -45.0  # quieter than this is noise, not a plucked string
DETECT_MARGIN_DB = 10.0  # the signal's input must stand out this much from all the others


def loudest(peaks: dict[str, float]) -> str | None:
    """The one input clearly louder than all others, if there is one."""
    ranked = sorted(peaks, key=peaks.get, reverse=True)
    if not ranked or peaks[ranked[0]] < DETECT_MIN_DB:
        return None
    if len(ranked) > 1 and peaks[ranked[0]] - peaks[ranked[1]] < DETECT_MARGIN_DB:
        return None
    return ranked[0]
