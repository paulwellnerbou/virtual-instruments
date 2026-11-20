"""Generate the .dspreset from the spec and the reviewed hits (hits.json)."""

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace

import numpy as np
from lxml import etree

from vinst import eq
from vinst.plan import Pass, Take, lane_bounds, note_lanes, plan_takes, recording_passes
from vinst.review import Hit, Manifest, lane_of
from vinst.spec import (CONTROLS, FX_TAG, LOW_TAG, NOTES_TAG, RELEASE_TAG, SUSTAINED_TYPES, Instrument, Signal,
                        file_token)

MIN_VERSION = "1.13.0"
UI_W, UI_H = 812, 375
# DecentSampler draws its keyboard over the lower part of the UI; only about the top 215 px
# stay visible, which is why the official presets keep their controls above y=200.
VISIBLE_H = 215
KNOB_W, KNOB_H, KNOB_GAP = 84, 84, 14
KNOB_Y = 104
FOOTER_Y, FOOTER_H = 194, 18  # under the knobs, still above the keyboard
MARGIN = 32
KNOB_STYLE = "vinst"
MENU_W = 240
EXTRA_KEY_RGB = "7FB3D5"  # keys of the extras, apart from the notes' accent
SAMPLES_DIR = "Samples"
# Tone knob: 0..1 -> lowpass cutoff in Hz, as in the official Decent Sampler presets.
TONE_TABLE = "0,33;0.3,150;0.4,450;0.5,1100;0.7,4100;0.9,11000;1.0001,22000"


@dataclass
class Entry:
    """One <sample> element."""

    signal: str
    file: str
    root: int
    lo: int
    hi: int
    lo_vel: int = 1
    hi_vel: int = 127
    tuning: float = 0.0
    seq_pos: int = 1
    seq_len: int = 1
    volume_db: float = 0.0
    loop: tuple[int, int, int] | None = None  # start, end (inclusive), crossfade frames
    sample_rate: int | None = None
    extra: str = ""  # an extra on its own key: its id, for a group of its own
    mutes: bool = False  # that extra stops the notes still ringing
    stroke: str = ""  # "down" or "up": its strokes alone, on a key of their own
    removed: str = ""  # the file with what the low cut took out of this one, for the Low cut knob


@dataclass
class Mapping:
    notes: list[Entry] = field(default_factory=list)
    release: list[Entry] = field(default_factory=list)
    keys: list[Entry] = field(default_factory=list)
    span: tuple[int, int] | None = None
    fx_keys: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)
    gain_db: float = 0.0  # for all samples: the recordings keep their level, the instrument is made louder

    @property
    def files(self) -> set[str]:
        return {e.file for e in self.notes + self.release + self.keys}


def velocity_slots(bounds: list[tuple[str, int, int]], available: set[str]) -> list[tuple[str, int, int]]:
    """(recorded lane to play, lo_vel, hi_vel) per velocity range of a note's lanes.

    A lane without usable hits gives its range to the nearest lower lane that has hits (or the
    nearest higher one if there is none below).
    """
    lanes = [lane for lane, _, _ in bounds]
    out = []
    for i, (_, lo, hi) in enumerate(bounds):
        lower = [lanes[j] for j in range(i, -1, -1) if lanes[j] in available]
        higher = [lanes[j] for j in range(i, len(lanes)) if lanes[j] in available]
        if not lower and not higher:
            continue
        src = (lower or higher)[0]
        if out and out[-1][0] == src:
            out[-1] = (src, out[-1][1], hi)
        else:
            out.append((src, lo, hi))
    return out


def key_zones(roots: list[int], extend_below: int, extend_above: int) -> dict[int, tuple[int, int]]:
    """Key range per sampled root: each root is the top of its zone, so the keys between two roots
    play the upper one pitched down. Pitched down, a sample sounds a little darker; pitched up it
    gets brighter and sounds "chipmunky" (DecentSampler's advice for samplists). Only above the
    highest root are samples pitched up, as far as extend_above asks."""
    zones = {}
    for i, r in enumerate(roots):
        lo = roots[i - 1] + 1 if i else max(0, r - extend_below)
        hi = r if i + 1 < len(roots) else min(127, r + extend_above)
        zones[r] = (lo, hi)
    return zones


def tuning_for(inst: Instrument, hit: Hit) -> float:
    p = inst.preset
    if hit.cents is None or p.tuning == "off":
        return 0.0
    factor = 1.0 if p.tuning == "full" else p.tuning_amount
    return round(-hit.cents / 100 * factor, 4)


def wanted_crossfade(inst: Instrument, sample_rate: int | None) -> int:
    return int(inst.processing.loop_crossfade_ms / 1000 * (sample_rate or 48000))


def loop_for(inst: Instrument, hit: Hit, sample_rate: int | None) -> tuple[int, int, int] | None:
    if not inst.processing.loops or hit.loop_off or hit.loop_start is None or hit.loop_end is None:
        return None
    # Decent Sampler crossfades with audio from before the loop start, so a loop that starts
    # early can only get a shorter crossfade; map_samples reports those.
    crossfade = max(0, min(wanted_crossfade(inst, sample_rate), hit.loop_start,
                           (hit.loop_end - hit.loop_start) // 4))
    return hit.loop_start, hit.loop_end, crossfade


def loudness(hit: Hit, sustained: bool) -> float:
    """What makes notes sound equally loud: the held part for sustained sounds, the attack otherwise."""
    return hit.body_db if sustained and hit.body_db is not None else hit.rms_db


# The instrument is made as loud as this: the median loudness (see loudness(): the attack, or the held
# part of sustained sounds) of its loudest velocity. Peaks are then held under level.CEILING_DB when
# the samples are written.
TARGET_LOUDNESS_DB = -16.0
MUTE_TAG = "mutes"  # on an extra that stops the notes still ringing (see render)
MUTE_FADE_S = 0.06
MAX_GAIN_DB = 24.0


# Evening out compares each note with this many recorded notes on either side, so a register that
# is naturally louder (bass, say) keeps its shape and only notes standing out from their neighbours move.
BALANCE_NEIGHBOURS = 3
# A hit this far off is most likely a mis-hit or a slicing error; raising it further would raise its noise.
BALANCE_MAX_DB = 18.0


def balance_corrections(pass_notes: dict[int, dict[str, list[Hit]]], layers: list[str],
                        tolerance_db: float, sustained: bool = False) -> dict[str, float]:
    """dB per hit (by id) that keeps it within tolerance_db of the median loudness of its velocity on
    the notes around it. Smaller differences stay as played; a hit beyond them is brought back to that
    difference (not to the median, so it doesn't jump past neighbours just inside it), by at most
    BALANCE_MAX_DB. Soft velocities stay softer than loud ones."""
    out: dict[str, float] = {}
    for layer in layers:
        roots = sorted(r for r, by_layer in pass_notes.items() if by_layer.get(layer))
        if sum(len(pass_notes[r][layer]) for r in roots) < 2:
            continue
        for i, root in enumerate(roots):
            # At the ends the window is cut off rather than shifted inwards: shifted, its median would sit
            # away from the end note and flatten a register trend there.
            window = roots[max(0, i - BALANCE_NEIGHBOURS):i + BALANCE_NEIGHBOURS + 1]
            median = float(np.median([loudness(h, sustained) for r in window for h in pass_notes[r][layer]]))
            for h in pass_notes[root][layer]:
                off = loudness(h, sustained) - median
                excess = off - float(np.clip(off, -tolerance_db, tolerance_db))
                out[h.id] = round(float(np.clip(-excess, -BALANCE_MAX_DB, BALANCE_MAX_DB)), 2)
    return out


def usable(hits: list[Hit]) -> list[Hit]:
    return [h for h in hits if not h.rejected]


def map_samples(inst: Instrument, manifest: Manifest) -> Mapping:
    m = Mapping()
    plan: dict[str, Take] = {t.key: t for t in plan_takes(inst)}
    extras_by_base = {f"x_{file_token(x.id)}": x for x in inst.extras}
    # pass -> midi -> layer -> hits. Each pass is mapped on its own: its hits, rejections and
    # gaps differ from the other passes'.
    notes: dict[str, dict[int, dict[str, list[Hit]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list)))
    pass_signals: dict[str, list[str]] = {}
    hit_rate: dict[str, int | None] = {}
    note_of: dict[tuple[str, int], str] = {}  # (pass, midi) -> note, for its own lanes
    laneless = 0
    recorded: set[tuple[str, str]] = set()
    # Per note and velocity that has hits: a name for it, to say when none of them is kept.
    played: dict[tuple[str, int, str], str] = {}

    imported = inst.recording.source == "samples"
    for t in manifest.takes:
        take = plan.get(t.key)
        if take is None and not imported and t.origin == "plan":
            if t.hits:
                m.warnings.append(f"“{t.title}” is in hits.json but not in the current plan; slice again.")
            continue
        # Notes and signals come from hits.json; plans from before they were stored there fill in.
        midi = t.midi if t.midi is not None else (take.note.midi if take and take.note else None)
        signals = t.signals or (take.signals if take else [])
        if signals:  # e.g. the import's "not imported" list has none and must not blank the pass
            pass_signals[t.pass_id] = signals
        if t.status == "missing":
            continue
        if t.status in ("count_mismatch", "error"):
            m.warnings.append(f"“{t.title}”: {t.message}. Its hits are left out.")
            continue
        for h in t.hits:
            hit_rate[h.id] = h.sample_rate or t.sample_rate
        if t.kind == "note" and midi is not None:
            if t.note:
                note_of[(t.pass_id, midi)] = t.note
                recorded.add((t.pass_id, t.note))
            lanes = note_lanes(inst, t.note) if t.note else None
            note_title = t.title.split(f" · {t.lane}")[0] if t.lane else t.title
            for h in t.hits:
                lane = h.layer if lanes is None or imported else lane_of(h, t, lanes)
                if lane is not None:
                    played.setdefault((t.pass_id, midi, lane), f"{note_title} · {lane}")
            for h in usable(t.hits):
                # Imported samples keep the importer's lanes; recorded hits follow the note's lanes.
                lane = h.layer if lanes is None or imported else lane_of(h, t, lanes)
                if lane is not None:
                    notes[t.pass_id][midi][lane].append(h)
                else:
                    laneless += 1
        elif t.kind == "extra" and not imported:
            extra = extras_by_base.get(t.base)
            if extra is None or extra.use == "off":
                continue
            hits = usable(t.hits)
            if extra.use == "release":
                m.release += [Entry(sig, h.files[sig], 60, 0, 0) for h in hits for sig in signals]
            else:
                # By its place in the recording, so a rejected hit doesn't swap down and up after it.
                strokes = [("", extra.key, hits)]
                if extra.stroke_keys:
                    strokes += [("down", extra.down_key, [h for h in hits if h.index % 2 == 0]),
                                ("up", extra.up_key, [h for h in hits if h.index % 2 == 1])]
                for stroke, key, stroke_hits in strokes:
                    if stroke_hits and key not in m.fx_keys:
                        m.fx_keys.append(key)
                    for pos, h in enumerate(stroke_hits, 1):
                        for sig in signals:
                            m.keys.append(Entry(sig, h.files[sig], key, key, key, seq_pos=pos,
                                                seq_len=len(stroke_hits), extra=extra.id, mutes=extra.mutes,
                                                stroke=stroke))
    # A recording with every hit rejected is fine while its velocity keeps others.
    for (pass_id, midi, lane), name in played.items():
        kept = notes.get(pass_id, {}).get(midi, {})
        if not kept.get(lane):
            instead = "other velocities of the note" if any(kept.values()) else "neighbouring notes"
            m.warnings.append(f"“{name}”: all its hits are rejected, so {instead} play there.")
    unrecorded = len({(t.pass_id, t.note_base) for t in plan.values() if t.kind == "note"} - recorded) \
        if not imported else 0
    if unrecorded:
        m.warnings.append(f"{unrecorded} planned notes aren't recorded yet; neighbouring samples cover them.")
    if laneless:
        m.warnings.append(f"{laneless} hits have no velocity (theirs was removed from the note) and are left out; "
                          "drag them into one in the worksheet.")
    if not notes:
        return m

    below, above = inst.preset.neighbours_below, inst.preset.neighbours_above
    spans = []
    # Per velocity and hit (each once, however many keys borrow it): its loudness as played.
    played: dict[str, dict[str, float]] = defaultdict(dict)
    sustained = inst.meta.type in SUSTAINED_TYPES
    balanced: list[float] = []
    capped: list[str] = []
    for pass_id, pass_notes in notes.items():
        roots = sorted(pass_notes)
        all_lanes = list(dict.fromkeys(lane for by_lane in pass_notes.values() for lane in by_lane))
        corrections = balance_corrections(pass_notes, all_lanes, inst.preset.balance_tolerance_db,
                                          sustained=inst.meta.type in SUSTAINED_TYPES) \
            if inst.preset.balance else {}
        balanced += [c for c in corrections.values() if abs(c) >= 0.5]
        capped += [f"{note_of.get((pass_id, r)) or r} ({lane})" for r, by_lane in pass_notes.items()
                   for lane, hs in by_lane.items() for h in hs
                   if abs(corrections.get(h.id, 0.0)) >= BALANCE_MAX_DB]
        zones = key_zones(roots, inst.preset.extend_below, inst.preset.extend_above)
        spans.append((zones[roots[0]][0], zones[roots[-1]][1]))
        for root in roots:
            lo, hi = zones[root]
            bounds = lane_bounds(inst, note_of.get((pass_id, root), ""))
            lanes = {lane for lane, _, _ in bounds}
            for lane in sorted(set(pass_notes[root]) - lanes):
                m.warnings.append(f"Note {root}: hits in velocity “{lane}”, which this note doesn't have; left out.")
            slots = velocity_slots(bounds, set(pass_notes[root]) & lanes)
            # Without neighbour borrowing one entry covers the whole zone; with it every key gets
            # its own entries, because each key has a different set of candidates.
            key_ranges = [(lo, hi)] if not (below or above) else [(k, k) for k in range(lo, hi + 1)]
            for k_lo, k_hi in key_ranges:
                for layer, v_lo, v_hi in slots:
                    candidates = [(root, h) for h in pass_notes[root][layer]]
                    if below or above:
                        for other in roots:
                            if other != root and -below <= other - k_lo <= above:
                                candidates += [(other, h) for h in pass_notes[other].get(layer, [])]
                    for pos, (h_root, h) in enumerate(candidates, 1):
                        played[layer][h.id] = loudness(h, sustained) + corrections.get(h.id, 0.0)
                        for sig in pass_signals[pass_id]:
                            m.notes.append(Entry(sig, h.files[sig], h_root, k_lo, k_hi, v_lo, v_hi,
                                                 tuning_for(inst, h), pos, len(candidates),
                                                 corrections.get(h.id, 0.0),
                                                 loop_for(inst, h, hit_rate.get(h.id)), hit_rate.get(h.id)))
    m.span = (min(lo for lo, _ in spans), max(hi for _, hi in spans))
    if played:
        # Recordings are kept at their level, often well below full scale; the instrument plays them
        # louder, as loud as its notes need rather than as its loudest click allows.
        loudest = max(float(np.median(list(v.values()))) for v in played.values())
        m.gain_db = round(min(MAX_GAIN_DB, TARGET_LOUDNESS_DB - loudest), 1)
    if inst.preset.balance:
        m.info.append(f"Loudness evened out: {len(balanced)} samples corrected"
                      + (f", largest {max(balanced, key=abs):+.1f} dB." if balanced else "."))
    if capped:
        m.warnings.append("These samples are far louder or softer than the notes around them and were evened out "
                          f"by only {BALANCE_MAX_DB:g} dB: " + ", ".join(dict.fromkeys(capped))
                          + ". Probably a mis-hit or a slicing error; check them in the worksheet.")
    if inst.processing.loops:
        looped = {e.file for e in m.notes if e.loop}
        m.info.append(f"Loops: {len(looped)} of {len({e.file for e in m.notes})} note samples loop.")
        shortened = {e.file for e in m.notes if e.loop and e.loop[2] < wanted_crossfade(inst, e.sample_rate)}
        if shortened:
            m.warnings.append(f"{len(shortened)} loops start too early for the {inst.processing.loop_crossfade_ms:g} ms "
                              "crossfade and got a shorter one; slice again to search loops that fit it.")
    for e in m.release:
        e.lo, e.hi = m.span
    by_signal: dict[str, int] = defaultdict(int)
    for e in m.release:
        by_signal[e.signal] += 1
        e.seq_pos = by_signal[e.signal]
    for e in m.release:
        e.seq_len = by_signal[e.signal]
    return m


# --- XML -------------------------------------------------------------------------------------

def _fmt(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def _el(parent: etree._Element, tag: str, **attrs: object) -> etree._Element:
    return etree.SubElement(parent, tag, {k: _fmt(v) if isinstance(v, float) else str(v)
                                          for k, v in attrs.items() if v is not None})


def table_value(table: str, x: float) -> float:
    """Linear interpolation in a DecentSampler translationTable ("in,out;in,out;...")."""
    pts = [tuple(float(v) for v in pair.split(",")) for pair in table.split(";")]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[0][1] if x < pts[0][0] else pts[-1][1]


def equal_power_table(rising: bool) -> str:
    pts = []
    for i in range(21):
        x = i / 20
        y = math.sin(x * math.pi / 2) if rising else math.cos(x * math.pi / 2)
        pts.append(f"{1.0001 if i == 20 else x:g},{y:.3f}")
    return ";".join(pts)


@dataclass
class Knob:
    key: str
    label: str
    lo: float
    hi: float
    value: float
    bindings: list[dict[str, object]]
    tooltip: str | None = None


def knob_layout(n: int) -> tuple[float, float, int]:
    """(x of the first knob, distance between knobs, knob size) for a row centred under the title.

    Knobs shrink when there are too many to fit next to each other: there is no second row,
    because DecentSampler's keyboard covers everything below the first one.
    """
    step = min(KNOB_W + KNOB_GAP, (UI_W - 2 * MARGIN) / max(n, 1))
    size = int(min(KNOB_W, step - KNOB_GAP))
    return (UI_W - n * step) / 2 + (step - size) / 2, step, size


def title_width(with_menu: bool) -> int:
    """The title's width: up to the passes menu, if the instrument has one, at the top right."""
    return UI_W - 2 * MARGIN - (MENU_W + 16 if with_menu else 0)


def subtitle(inst: Instrument) -> str:
    """The line under the title, e.g. "by Paul · v1.0"; empty when it shows neither."""
    meta, p = inst.meta, inst.preset
    version = meta.version.strip()
    parts = [f"by {meta.author.strip()}" if p.show_author and meta.author.strip() else "",
             (version if version[:1] in "vV" else f"v{version}") if p.show_version and version else ""]
    return " · ".join(x for x in parts if x)


def site_text(url: str) -> str:
    """A website as people read it: "https://www.example.com/" → "www.example.com"."""
    return re.sub(r"^https?://", "", url.strip()).rstrip("/")


def footer(inst: Instrument) -> str:
    """The line at the bottom of the panel, e.g. "© 2026 Paul · example.com"."""
    meta, p = inst.meta, inst.preset
    parts = [meta.copyright.strip() if p.show_copyright else "", site_text(meta.website) if p.show_website else ""]
    return " · ".join(x for x in parts if x)


def pale(argb: str, share: float) -> str:
    """`argb` mixed into white, `share` of it: a tint that is opaque."""
    rgb = [int(argb[i:i + 2], 16) for i in (2, 4, 6)]
    return "FF" + "".join(f"{round(255 + (c - 255) * share):02X}" for c in rgb)


def knob_style(accent: str, text: str) -> etree._Element:
    """A knob DecentSampler draws itself (1.43+): a dark cap with a pointer, on a faint track that
    lights up in the accent colour up to the value; the value shows on the cap while hovered."""
    rgb = accent[2:]
    style = etree.Element("knobStyle", id=KNOB_STYLE, startAngle="-135", endAngle="135")
    _el(style, "arc", r=46.0, width=6.0, color="22FFFFFF", cap="round")  # the whole sweep
    lit = _el(style, "arc", r=46.0, width=6.0, color=accent, to="value", cap="round")
    _el(lit, "glow", radius=3.0, color=f"55{rgb}")
    cap = _el(style, "circle", r=34.0, fill="FF353A42", fill2="FF1B1E23", gradient="linear")
    _el(cap, "shadow", dy=2.0, blur=4.0, color="AA000000")
    _el(cap, "bevel", depth=1.0)
    _el(style, "pointer", shape="rounded", r1=14.0, r2=30.0, width=5.0, color=text, show="notHover")
    _el(style, "text", showValue="true", size=15.0, color=text, show="hover")
    return style


def switched_passes(inst: Instrument) -> list[Pass]:
    """Passes the instrument switches between with a menu; empty if there is nothing to switch."""
    passes = recording_passes(inst)
    return passes if len(passes) > 1 and inst.preset.pass_mode == "switch" else []


def mixable_signals(inst: Instrument) -> list[Signal]:
    """Signals the mix control blends: all of them, or only the first pass's when switching."""
    switched = switched_passes(inst)
    return list(switched[0].signals) if switched else list(inst.signals)


def active_controls(inst: Instrument, mapping: Mapping) -> list[str]:
    """The knobs, in CONTROLS order; the low cut's comes with the low cut (see bundle.build)."""
    out = []
    for c in CONTROLS:
        if c not in inst.preset.controls and c != "lowcut":
            continue
        if c == "mix" and len(mixable_signals(inst)) < 2:
            continue
        if c == "noise" and not mapping.release:
            continue
        if c == "lowcut" and not any(e.removed for e in mapping.notes):
            continue
        if c == "tone" and "eq" in inst.preset.controls:
            continue  # the Equalizer takes the Tone knob's place
        if c == "eq" and not any(b.on for b in inst.preset.eq):
            continue
        out.append(c)
    return out


def eq_bands(inst: Instrument) -> list[int]:
    """Indices of the equalizer's bands that play."""
    return [i for i, b in enumerate(inst.preset.eq) if b.on]


def effects_for(inst: Instrument, controls: list[str]) -> list[str]:
    """The instrument's effects in order: "lowpass" (Tone), "eq<i>" per band, "chorus", "reverb"."""
    out = ["lowpass"] if "tone" in controls else []
    if "eq" in controls:
        out += [f"eq{i}" for i in eq_bands(inst)]
    return out + [fx for fx in ("chorus", "reverb") if fx in controls]


def knobs_for(inst: Instrument, controls: list[str], effects: list[str]) -> list[Knob]:
    p = inst.preset
    knobs: list[Knob] = []
    for c in controls:
        if c == "mix" and len(mixable_signals(inst)) == 2:
            a, b = mixable_signals(inst)
            knobs.append(Knob("mix", "Mix", 0, 1, 0.5, [
                dict(type="amp", level="tag", identifier=a.id, parameter="AMP_VOLUME", translation="table",
                     translationTable=equal_power_table(rising=False)),
                dict(type="amp", level="tag", identifier=b.id, parameter="AMP_VOLUME", translation="table",
                     translationTable=equal_power_table(rising=True)),
            ], tooltip=f"{a.name} ← → {b.name}"))
        elif c == "mix":
            for sig in mixable_signals(inst):
                knobs.append(Knob(f"mix-{sig.id}", sig.name, 0, 1, 0.7, [
                    dict(type="amp", level="tag", identifier=sig.id, parameter="AMP_VOLUME")]))
        elif c == "tone":
            knobs.append(Knob("tone", CONTROLS[c], 0, 1, p.tone, [
                dict(type="effect", level="instrument", position=effects.index("lowpass"),
                     parameter="FX_FILTER_FREQUENCY", translation="table", translationTable=TONE_TABLE)]))
        elif c == "eq":
            for i in eq_bands(inst):
                band = p.eq[i]
                if not eq.has_gain(band):
                    continue  # cuts and notches stay as set in the builder
                span = eq.knob_range(band)
                knobs.append(Knob(f"eq{i}", eq.label(band), -span, span, round(band.gain_db, 1), [
                    dict(type="effect", level="instrument", position=effects.index(f"eq{i}"),
                         parameter="FX_FILTER_GAIN", translation="table", translationTable=eq.gain_table(span))],
                    tooltip=f"{eq.TYPES[band.type]} at {eq.where(band)}: boost or cut, in dB"))
        elif c == "lowcut":
            # 0 %: as recorded, 100 %: the cut set when building; between, a blend of the two.
            knobs.append(Knob("lowcut", f"{CONTROLS[c]} %", 0, 100, 100, [  # a knob shows a bare number
                dict(type="amp", level="tag", identifier=LOW_TAG, parameter="AMP_VOLUME", translation="table",
                     translationTable="0,1;100,0;100.001,0")],
                tooltip=f"The low resonance below each note's pitch, e.g. of the body after a pluck: 0 % as "
                        f"recorded, 100 % quieter by the {inst.processing.low_cut_db:g} dB set when building"))
        elif c == "attack":
            knobs.append(Knob("attack", CONTROLS[c], 0, 2, p.attack, [
                dict(type="amp", level="instrument", position=0, parameter="ENV_ATTACK")]))
        elif c == "release":
            knobs.append(Knob("release", CONTROLS[c], 0, 8, p.release, [
                dict(type="amp", level="instrument", position=0, parameter="ENV_RELEASE")]))
        elif c == "reverb":
            knobs.append(Knob("reverb", CONTROLS[c], 0, 1, p.reverb, [
                dict(type="effect", level="instrument", position=effects.index("reverb"),
                     parameter="FX_REVERB_WET_LEVEL")]))
        elif c == "chorus":
            knobs.append(Knob("chorus", CONTROLS[c], 0, 1, p.chorus, [
                dict(type="effect", level="instrument", position=effects.index("chorus"), parameter="FX_MIX")]))
        elif c == "noise":
            knobs.append(Knob("noise", "Noise", 0, 1, 0.5, [
                dict(type="amp", level="tag", identifier=RELEASE_TAG, parameter="AMP_VOLUME")]))
    return knobs


def initial_tag_volumes(inst: Instrument, knobs: list[Knob]) -> dict[str, float]:
    vols = {sig.id: 1.0 for sig in inst.signals}
    for k in knobs:
        if k.key == "mix":
            a, b = mixable_signals(inst)
            vols[a.id] = vols[b.id] = round(math.cos(math.pi / 4), 3)
        elif k.key.startswith("mix-"):
            vols[k.key[4:]] = k.value
    return vols


def render(inst: Instrument, mapping: Mapping, background: str | None = None) -> bytes:
    """`background` is the image path inside the bundle, if any."""
    p = inst.preset
    meta = inst.meta
    root = etree.Element("DecentSampler", minVersion=MIN_VERSION)
    header = "".join(f"  {line}\n" for line in [
        meta.name, f"version {meta.version}", f"by {meta.author.strip()}" if meta.author.strip() else "",
        meta.copyright.strip(), meta.website.strip(), *meta.description.strip().splitlines(),
        "generated by the DecentSampler Instrument Builder (vinst)"] if line.strip())
    header = "\n" + header
    # XML comments may not contain "--".
    root.addprevious(etree.Comment(re.sub(r"-{2,}", "-", header)))

    controls = active_controls(inst, mapping)
    effects = effects_for(inst, controls)
    knobs = knobs_for(inst, controls, effects)

    ui_attrs = dict(width=UI_W, height=UI_H, bgColor=p.bg_color)
    if background:
        ui_attrs["bgImage"] = background
    ui = _el(root, "ui", **ui_attrs)
    tab = _el(ui, "tab", name="main")
    ui_index = 0
    _el(ui, "controlStyles").append(knob_style(p.accent_color, p.text_color))
    switched = switched_passes(inst)
    title_w = title_width(bool(switched))
    _el(tab, "label", x=MARGIN, y=20, width=title_w, height=36, text=meta.name, textSize=28,
        textColor=p.text_color, hAlign="left")
    ui_index += 1
    if sub := subtitle(inst):
        _el(tab, "label", x=MARGIN, y=54, width=title_w, height=20, text=sub,
            textSize=13, textColor=f"99{p.text_color[2:]}", hAlign="left")
        ui_index += 1
    # A hairline under the title, so the knobs sit in a panel of their own.
    _el(tab, "line", x1=MARGIN, y1=84, x2=UI_W - MARGIN, y2=84, lineColor="1FFFFFFF", lineThickness=1.0)
    ui_index += 1
    if switched:
        menu = _el(tab, "menu", x=UI_W - MARGIN - MENU_W, y=26, width=MENU_W, height=26, value=1, textColor=p.text_color,
                   tooltip="Which recording to play")
        for chosen in switched:
            option = _el(menu, "option", name=chosen.name)
            for q in switched:
                _el(option, "binding", type="amp", level="tag", identifier=f"pass-{q.id}",
                    parameter="TAG_ENABLED", translation="fixed_value",
                    translationValue="true" if q == chosen else "false")
        ui_index += 1
    knob_index: dict[str, int] = {}
    x0, step, size = knob_layout(len(knobs))
    for i, k in enumerate(knobs):
        el = _el(tab, "labeled-knob", x=round(x0 + i * step), y=KNOB_Y,
                 width=size, height=size, label=k.label, textSize=13, type="float", minValue=float(k.lo),
                 maxValue=float(k.hi), value=float(k.value), textColor=f"CC{p.text_color[2:]}",
                 trackForegroundColor=p.accent_color, trackBackgroundColor="22FFFFFF", tooltip=k.tooltip,
                 controlStyle=KNOB_STYLE, tags=f"knob-{k.key}")
        for b in k.bindings:
            _el(el, "binding", **b)
        knob_index[k.key] = ui_index
        ui_index += 1
    if foot := footer(inst):
        _el(tab, "label", x=MARGIN, y=FOOTER_Y, width=UI_W - 2 * MARGIN, height=FOOTER_H, text=foot,
            textSize=11, textColor=f"80{p.text_color[2:]}", hAlign="right")
        ui_index += 1
    if mapping.span or mapping.fx_keys:
        # A tint, not a fill: the keys stay keys, the playable ones warmer than the rest. (The keyboard
        # ignores a colour's alpha, so the tint is mixed in.)
        kb = _el(ui, "keyboard", centerNote=sum(mapping.span) // 2 if mapping.span else None)
        if mapping.span:
            _el(kb, "color", loNote=mapping.span[0], hiNote=mapping.span[1], color=pale(p.accent_color, 0.4))
        for key in mapping.fx_keys:
            _el(kb, "color", loNote=key, hiNote=key, color=pale(f"FF{EXTRA_KEY_RGB}", 0.6))

    vel_track = p.vel_track if p.vel_track is not None else (0.3 if len(inst.dynamics.layers) > 1 else 0.8)
    groups = _el(root, "groups", attack=float(p.attack), decay=25.0, sustain=1.0, release=float(p.release),
                 ampVelTrack=float(vel_track),
                 volume=f"{mapping.gain_db:.1f}dB" if abs(mapping.gain_db) >= 0.1 else None)

    def add_group(name: str, tags: str, entries: list[Entry], **attrs: object) -> None:
        if not entries:
            return
        g = _el(groups, "group", name=name, tags=tags, **attrs)
        for e in entries:
            loop = dict(loopEnabled="true", loopStart=e.loop[0], loopEnd=e.loop[1], loopCrossfade=e.loop[2],
                        loopCrossfadeMode="equal_power") if e.loop else {}
            _el(g, "sample", path=f"{SAMPLES_DIR}/{e.file}", rootNote=e.root, loNote=e.lo, hiNote=e.hi,
                loVel=e.lo_vel, hiVel=e.hi_vel, tuning=float(e.tuning) if e.tuning else None,
                volume=f"{e.volume_db:.2f}dB" if e.volume_db else None,
                seqPosition=e.seq_pos, seqLength=e.seq_len, **loop)

    pass_of = {s.id: q for q in recording_passes(inst) for s in q.signals}

    def tags(sig: Signal, *more: str) -> str:
        return ",".join([sig.id, f"pass-{pass_of[sig.id].id}", *more])

    def enabled(sig: Signal) -> str | None:
        # Matches the menu's default (first option): other passes start switched off.
        return "false" if switched and pass_of[sig.id] != switched[0] else None

    # A muting extra (a chuck) stops the notes still ringing, with a short fade as a hand on the strings.
    muting = {"silencedByTags": MUTE_TAG, "silencingDecay": MUTE_FADE_S} if any(e.mutes for e in mapping.keys) else {}
    for sig in inst.signals:
        add_group(sig.name, tags(sig, NOTES_TAG), [e for e in mapping.notes if e.signal == sig.id],
                  enabled=enabled(sig), seqMode="round_robin", **muting)
    # What the low cut took out, a layer of its own that the Low cut knob turns up: in step with the
    # notes, as their groups are, so the two add up to the uncut sample.
    for sig in inst.signals:
        add_group(f"{sig.name} low part", tags(sig, LOW_TAG),
                  [replace(e, file=e.removed) for e in mapping.notes if e.signal == sig.id and e.removed],
                  enabled=enabled(sig), seqMode="round_robin", **muting)
    for sig in inst.signals:
        add_group(f"{sig.name} release noise", tags(sig, RELEASE_TAG),
                  [e for e in mapping.release if e.signal == sig.id], enabled=enabled(sig),
                  trigger="release", seqMode="random", pitchKeyTrack=0.0, ampVelTrack=0.0)
    # Each extra its own group, its hits in turn: they were recorded as a sequence (e.g. chucks down
    # and up), and a group's rotation would mix the extras' hits.
    names = {x.id: x.name for x in inst.extras}
    for sig in inst.signals:
        for extra_id, stroke in dict.fromkeys((e.extra, e.stroke) for e in mapping.keys):
            entries = [e for e in mapping.keys if e.signal == sig.id and e.extra == extra_id and e.stroke == stroke]
            more = [MUTE_TAG] if entries and entries[0].mutes else []
            name = " ".join(filter(None, [sig.name, names.get(extra_id, "effects"), stroke]))
            add_group(name, tags(sig, FX_TAG, *more), entries,
                      enabled=enabled(sig), seqMode="round_robin", pitchKeyTrack=0.0)

    fx = _el(root, "effects")
    for name in effects:
        if name == "lowpass":
            # The same cutoff the Tone knob's default maps to, so knob and sound agree on load.
            _el(fx, "effect", type="lowpass", frequency=round(table_value(TONE_TABLE, p.tone), 1))
        elif name.startswith("eq"):
            band = p.eq[int(name[2:])]
            if eq.has_gain(band):
                _el(fx, "effect", type=band.type, frequency=round(band.freq, 1), q=round(band.q, 3),
                    gain=round(10 ** (band.gain_db / 20), 5))
            elif band.type == "notch":
                _el(fx, "effect", type="notch", frequency=round(band.freq, 1), q=round(band.q, 3))
            else:
                _el(fx, "effect", type=band.type, frequency=round(band.freq, 1), resonance=round(band.q, 3))
        elif name == "chorus":
            _el(fx, "effect", type="chorus", mix=float(p.chorus))
        elif name == "reverb":
            _el(fx, "effect", type="reverb", wetLevel=float(p.reverb), roomSize=float(p.reverb_room),
                damping=float(p.reverb_damping))

    # Not the low cut: at rest, the wheel would bring all the low end back; nor an EQ band, cut fully at rest.
    cc_target = "tone" if "tone" in knob_index else next(
        (k for k in knob_index if k != "lowcut" and not k.startswith("eq")), None)
    if cc_target is not None:
        target = next(k for k in knobs if k.key == cc_target)
        midi = _el(root, "midi")
        cc = _el(midi, "cc", number=1)
        # By its tag, not its position: what counts as a position (labels, lines) isn't spelled out.
        _el(cc, "binding", level="ui", type="control", controlTags=f"knob-{cc_target}", parameter="VALUE",
            translation="linear", translationOutputMin=float(target.lo), translationOutputMax=float(target.hi))

    tags_el = _el(root, "tags")
    for tag, vol in initial_tag_volumes(inst, knobs).items():
        _el(tags_el, "tag", name=tag, volume=float(vol))
    if "lowcut" in knob_index:
        _el(tags_el, "tag", name=LOW_TAG, volume=0.0)  # the knob starts at the full cut
    if mapping.release:
        _el(tags_el, "tag", name=RELEASE_TAG, volume=0.5 if "noise" in knob_index else 1.0)

    return etree.tostring(root.getroottree(), pretty_print=True, xml_declaration=True, encoding="UTF-8")


def library_info(inst: Instrument, cover_art: str | None = None) -> bytes:
    root = etree.Element("DecentSamplerLibraryInfo", name=inst.meta.name, version=inst.meta.version)
    if cover_art:
        root.set("coverArt", cover_art)
    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")
