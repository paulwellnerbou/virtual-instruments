"""Generate the .dspreset from the spec and the reviewed hits (hits.json)."""

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from lxml import etree

from vinst.plan import Pass, Take, plan_takes, recording_passes
from vinst.review import Hit, Manifest
from vinst.spec import CONTROLS, SUSTAINED_TYPES, FX_TAG, NOTES_TAG, RELEASE_TAG, Instrument, Signal, file_token

MIN_VERSION = "1.13.0"
UI_W, UI_H = 812, 375
# DecentSampler draws its keyboard over the lower part of the UI; only about the top 215 px
# stay visible, which is why the official presets keep their controls above y=200.
VISIBLE_H = 215
KNOB_W, KNOB_H, KNOB_GAP = 90, 90, 6
KNOB_Y = 100
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


@dataclass
class Mapping:
    notes: list[Entry] = field(default_factory=list)
    release: list[Entry] = field(default_factory=list)
    keys: list[Entry] = field(default_factory=list)
    span: tuple[int, int] | None = None
    fx_keys: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def files(self) -> set[str]:
        return {e.file for e in self.notes + self.release + self.keys}


def layer_bounds(n: int) -> list[tuple[int, int]]:
    """Split velocities 1..127 evenly into n layers."""
    edges = [1 + round(i * 127 / n) for i in range(n + 1)]
    return [(edges[i], edges[i + 1] - 1 if i < n - 1 else 127) for i in range(n)]


def velocity_slots(layers: list[str], available: set[str]) -> list[tuple[str, int, int]]:
    """(recorded layer to play, lo_vel, hi_vel) per velocity range.

    A layer without usable hits gives its range to the nearest lower layer that has hits
    (or the nearest higher one if there is none below).
    """
    out = []
    for i, (lo, hi) in enumerate(layer_bounds(len(layers))):
        lower = [layers[j] for j in range(i, -1, -1) if layers[j] in available]
        higher = [layers[j] for j in range(i, len(layers)) if layers[j] in available]
        src = (lower or higher)[0]
        if out and out[-1][0] == src:
            out[-1] = (src, out[-1][1], hi)
        else:
            out.append((src, lo, hi))
    return out


def key_zones(roots: list[int], extend_below: int, extend_above: int) -> dict[int, tuple[int, int]]:
    """Key range per sampled root. Between two roots the upper one gets the extra key, because
    pitching a sample down usually sounds more natural than pitching it up."""
    zones = {}
    for i, r in enumerate(roots):
        lo = roots[i - 1] + (r - roots[i - 1] - 1) // 2 + 1 if i else max(0, r - extend_below)
        hi = r + (roots[i + 1] - r - 1) // 2 if i + 1 < len(roots) else min(127, r + extend_above)
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


def balance_corrections(pass_notes: dict[int, dict[str, list[Hit]]], layers: list[str],
                        max_db: float, sustained: bool = False) -> dict[tuple[int, str], float]:
    """dB per (note, layer) that pulls each note towards its neighbours' loudness.

    Per layer, a running median over five neighbouring notes gives the expected level, so the
    overall shape across the range and the differences between layers stay as recorded; only
    notes that stick out are corrected, by at most max_db.
    """
    out: dict[tuple[int, str], float] = {}
    for layer in layers:
        points = sorted((root, float(np.median([loudness(h, sustained) for h in by_layer[layer]])))
                        for root, by_layer in pass_notes.items() if by_layer.get(layer))
        if len(points) < 3:
            continue
        values = np.array([v for _, v in points])
        expected = np.array([np.median(values[max(0, i - 2): i + 3]) for i in range(len(values))])
        for (root, _), corr in zip(points, np.clip(expected - values, -max_db, max_db)):
            out[(root, layer)] = round(float(corr), 2)
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
    unrecorded = 0

    for t in manifest.takes:
        take = plan.get(t.key)
        if take is None:
            if t.hits:
                m.warnings.append(f"“{t.title}” is in hits.json but not in the current plan; slice again.")
            continue
        pass_signals[take.pass_id] = take.signals
        if t.status == "missing":
            unrecorded += take.kind == "note"
            continue
        if t.status in ("count_mismatch", "error"):
            m.warnings.append(f"“{t.title}”: {t.message}. Its hits are left out.")
            continue
        for h in t.hits:
            hit_rate[h.id] = t.sample_rate
        if take.kind == "note" and take.note is not None:
            for h in usable(t.hits):
                if h.layer is not None:
                    notes[take.pass_id][take.note.midi][h.layer].append(h)
            if t.hits and not usable(t.hits):
                m.warnings.append(f"“{t.title}”: all hits rejected.")
        elif take.kind == "extra":
            extra = extras_by_base.get(t.base)
            if extra is None or extra.use == "off":
                continue
            hits = usable(t.hits)
            if extra.use == "release":
                m.release += [Entry(sig, h.files[sig], 60, 0, 0) for h in hits for sig in take.signals]
            else:
                if extra.key not in m.fx_keys:
                    m.fx_keys.append(extra.key)
                for pos, h in enumerate(hits, 1):
                    for sig in take.signals:
                        m.keys.append(Entry(sig, h.files[sig], extra.key, extra.key, extra.key,
                                            seq_pos=pos, seq_len=len(hits)))
    if unrecorded:
        m.warnings.append(f"{unrecorded} planned note takes aren't recorded yet; neighbouring samples cover them.")
    if not notes:
        return m

    n = inst.preset.neighbour_round_robins
    spans = []
    balanced: list[float] = []
    for pass_id, pass_notes in notes.items():
        roots = sorted(pass_notes)
        corrections = balance_corrections(pass_notes, inst.dynamics.layers, inst.preset.balance_max_db,
                                          sustained=inst.meta.type in SUSTAINED_TYPES) \
            if inst.preset.balance else {}
        balanced += [c for c in corrections.values() if abs(c) >= 0.5]
        zones = key_zones(roots, inst.preset.extend_below, inst.preset.extend_above)
        spans.append((zones[roots[0]][0], zones[roots[-1]][1]))
        for root in roots:
            lo, hi = zones[root]
            slots = velocity_slots(inst.dynamics.layers, set(pass_notes[root]))
            # Without neighbour borrowing one entry covers the whole zone; with it every key gets
            # its own entries, because each key has a different set of candidates.
            key_ranges = [(lo, hi)] if n == 0 else [(k, k) for k in range(lo, hi + 1)]
            for k_lo, k_hi in key_ranges:
                for layer, v_lo, v_hi in slots:
                    candidates = [(root, h) for h in pass_notes[root][layer]]
                    if n:
                        for other in roots:
                            if other != root and abs(other - k_lo) <= n:
                                candidates += [(other, h) for h in pass_notes[other].get(layer, [])]
                    for pos, (h_root, h) in enumerate(candidates, 1):
                        for sig in pass_signals[pass_id]:
                            m.notes.append(Entry(sig, h.files[sig], h_root, k_lo, k_hi, v_lo, v_hi,
                                                 tuning_for(inst, h), pos, len(candidates),
                                                 corrections.get((h_root, layer), 0.0),
                                                 loop_for(inst, h, hit_rate.get(h.id)), hit_rate.get(h.id)))
    m.span = (min(lo for lo, _ in spans), max(hi for _, hi in spans))
    if inst.preset.balance:
        m.info.append(f"Loudness balanced: {len(balanced)} note layers corrected"
                      + (f", largest {max(balanced, key=abs):+.1f} dB." if balanced else "."))
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
    """(x of the first knob, distance between knobs, knob size) for a right-aligned row.

    Knobs shrink when there are too many to fit next to each other: there is no second row,
    because DecentSampler's keyboard covers everything below the first one.
    """
    step = min(KNOB_W + KNOB_GAP, (UI_W - 32) / max(n, 1))
    size = int(min(KNOB_W, step - KNOB_GAP))
    return UI_W - 16 - n * step, step, size


def switched_passes(inst: Instrument) -> list[Pass]:
    """Passes the instrument switches between with a menu; empty if there is nothing to switch."""
    passes = recording_passes(inst)
    return passes if len(passes) > 1 and inst.preset.pass_mode == "switch" else []


def mixable_signals(inst: Instrument) -> list[Signal]:
    """Signals the mix control blends: all of them, or only the first pass's when switching."""
    switched = switched_passes(inst)
    return list(switched[0].signals) if switched else list(inst.signals)


def active_controls(inst: Instrument, mapping: Mapping) -> list[str]:
    out = []
    for c in inst.preset.controls:
        if c == "mix" and len(mixable_signals(inst)) < 2:
            continue
        if c == "noise" and not mapping.release:
            continue
        out.append(c)
    return out


def effects_for(controls: list[str]) -> list[str]:
    return [fx for c, fx in (("tone", "lowpass"), ("chorus", "chorus"), ("reverb", "reverb")) if c in controls]


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
    header = (f"\n  {meta.name}\n  version {meta.version}\n  by {meta.author}\n"
              "  generated by the DecentSampler Instrument Builder (vinst)\n")
    # XML comments may not contain "--".
    root.addprevious(etree.Comment(re.sub(r"-{2,}", "-", header)))

    controls = active_controls(inst, mapping)
    effects = effects_for(controls)
    knobs = knobs_for(inst, controls, effects)

    ui_attrs = dict(width=UI_W, height=UI_H, bgColor=p.bg_color)
    if background:
        ui_attrs["bgImage"] = background
    ui = _el(root, "ui", **ui_attrs)
    tab = _el(ui, "tab", name="main")
    ui_index = 0
    _el(tab, "label", x=20, y=14, width=520, height=36, text=meta.name, textSize=26, textColor=p.text_color,
        hAlign="left")
    ui_index += 1
    switched = switched_passes(inst)
    if switched:
        menu = _el(tab, "menu", x=20, y=58, width=240, height=26, value=1, textColor=p.text_color,
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
                 width=size, height=size, label=k.label, textSize=14, type="float", minValue=float(k.lo),
                 maxValue=float(k.hi), value=float(k.value), textColor=p.text_color,
                 trackForegroundColor=p.text_color, trackBackgroundColor="44FFFFFF", tooltip=k.tooltip)
        for b in k.bindings:
            _el(el, "binding", **b)
        knob_index[k.key] = ui_index
        ui_index += 1
    if mapping.span or mapping.fx_keys:
        kb = _el(ui, "keyboard")
        if mapping.span:
            _el(kb, "color", loNote=mapping.span[0], hiNote=mapping.span[1], color="FF2A9D8F")
        for key in mapping.fx_keys:
            _el(kb, "color", loNote=key, hiNote=key, color="FFE9A23B")

    vel_track = p.vel_track if p.vel_track is not None else (0.3 if len(inst.dynamics.layers) > 1 else 0.8)
    groups = _el(root, "groups", attack=float(p.attack), decay=25.0, sustain=1.0, release=float(p.release),
                 ampVelTrack=float(vel_track))

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

    for sig in inst.signals:
        add_group(sig.name, tags(sig, NOTES_TAG), [e for e in mapping.notes if e.signal == sig.id],
                  enabled=enabled(sig), seqMode="round_robin")
    for sig in inst.signals:
        add_group(f"{sig.name} release noise", tags(sig, RELEASE_TAG),
                  [e for e in mapping.release if e.signal == sig.id], enabled=enabled(sig),
                  trigger="release", seqMode="random", pitchKeyTrack=0.0, ampVelTrack=0.0)
    for sig in inst.signals:
        add_group(f"{sig.name} effects", tags(sig, FX_TAG), [e for e in mapping.keys if e.signal == sig.id],
                  enabled=enabled(sig), seqMode="random", pitchKeyTrack=0.0)

    fx = _el(root, "effects")
    for name in effects:
        if name == "lowpass":
            # The same cutoff the Tone knob's default maps to, so knob and sound agree on load.
            _el(fx, "effect", type="lowpass", frequency=round(table_value(TONE_TABLE, p.tone), 1))
        elif name == "chorus":
            _el(fx, "effect", type="chorus", mix=float(p.chorus))
        elif name == "reverb":
            _el(fx, "effect", type="reverb", wetLevel=float(p.reverb))

    cc_target = "tone" if "tone" in knob_index else next(iter(knob_index), None)
    if cc_target is not None:
        target = next(k for k in knobs if k.key == cc_target)
        midi = _el(root, "midi")
        cc = _el(midi, "cc", number=1)
        _el(cc, "binding", level="ui", type="control", position=knob_index[cc_target], parameter="VALUE",
            translation="linear", translationOutputMin=float(target.lo), translationOutputMax=float(target.hi))

    tags_el = _el(root, "tags")
    for tag, vol in initial_tag_volumes(inst, knobs).items():
        _el(tags_el, "tag", name=tag, volume=float(vol))
    if mapping.release:
        _el(tags_el, "tag", name=RELEASE_TAG, volume=0.5 if "noise" in knob_index else 1.0)

    return etree.tostring(root.getroottree(), pretty_print=True, xml_declaration=True, encoding="UTF-8")


def library_info(inst: Instrument) -> bytes:
    root = etree.Element("DecentSamplerLibraryInfo", name=inst.meta.name, version=inst.meta.version)
    return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")
