"""The Equalizer in step 6: a curve to drag, its bands, and listening to the samples through it.

The browser keeps the bands while they're dragged and sends them when a change is done ("eq-change").
The curve and what's heard use the same biquads as vinst.eq, so the builder shows and plays what
DecentSampler and the soundfont get.
"""

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import soundfile as sf
from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
from nicegui import app, ui
from pydantic import ValidationError

from vinst import eq, review, store
from vinst.spec import EqBand, default_eq

if TYPE_CHECKING:
    from vinst.ui.app import Editor

DEMO_NOTES = 6
DEMO_EVERY_S = 1.1
DEMO_FADE_S = 0.03


def _note_hits(slug: str) -> list[tuple[int, list[Path]]]:
    """(note, the files of its loudest kept hit, one per signal) for every sliced note, low to high."""
    inst = store.load(slug)
    manifest = review.load(store.manifest_path(slug))
    folder = review.samples_dir(inst)
    if manifest is None or folder is None:
        return []
    best: dict[int, tuple[float, list[Path]]] = {}
    for t in manifest.takes:
        if t.kind != "note" or t.midi is None:
            continue
        for h in t.hits:
            files = [folder / h.files[s] for s in t.signals if s in h.files]
            if h.rejected or not files or not all(f.is_file() for f in files):
                continue
            if t.midi not in best or h.rms_db > best[t.midi][0]:
                best[t.midi] = (h.rms_db, files)
    return [(m, best[m][1]) for m in sorted(best)]


# slug -> (modification time of its hits.json, spectrum), as the spectrum takes a moment.
_spectra: dict[str, tuple[int, list[tuple[float, float]]]] = {}


@app.get("/eq-spectrum/{slug}")
def eq_spectrum(slug: str) -> JSONResponse:
    try:
        stamp = store.manifest_path(slug).stat().st_mtime_ns
    except OSError:
        return JSONResponse([])
    if slug not in _spectra or _spectra[slug][0] != stamp:
        files = [f for _, fs in _note_hits(slug) for f in fs]
        _spectra[slug] = (stamp, eq.spectrum(files))
    return JSONResponse(_spectra[slug][1])


@app.get("/eq-demo/{slug}")
def eq_demo(slug: str) -> Response:
    """A few notes from low to high, each its loudest hit with its signals together, to hear the EQ on."""
    notes = _note_hits(slug)
    if not notes:
        raise HTTPException(404)
    if len(notes) > DEMO_NOTES:
        notes = [notes[round(i * (len(notes) - 1) / (DEMO_NOTES - 1))] for i in range(DEMO_NOTES)]
    sounds, rate = [], None
    for _, files in notes:
        parts = []
        for f in files:
            audio, sr = sf.read(str(f), dtype="float64", always_2d=True)
            if rate not in (None, sr):
                break  # imported files can differ in rate; joining them would change their pitch
            rate = sr
            parts.append(audio.mean(axis=1))
        else:
            n = max(len(p) for p in parts)
            sounds.append(sum(np.pad(p, (0, n - len(p))) for p in parts) / len(parts))
    if not sounds or rate is None:
        raise HTTPException(404)
    step = int(DEMO_EVERY_S * rate)
    fade = np.linspace(1.0, 0.0, int(DEMO_FADE_S * rate))
    out = []
    for sound in sounds[:-1]:
        part = np.zeros(step)
        part[: min(step, len(sound))] = sound[:step]
        part[-len(fade):] *= fade
        out.append(part)
    out.append(sounds[-1])
    joined = np.concatenate(out)
    joined *= 0.5 / max(float(np.max(np.abs(joined))), 1e-6)  # room for boosts
    buf = io.BytesIO()
    sf.write(buf, joined, rate, format="WAV", subtype="PCM_24")
    return Response(buf.getvalue(), media_type="audio/wav")


CSS = """
.vi-eq { width: 100%; max-width: 56rem; }
.vi-eq svg { width: 100%; height: auto; display: block; touch-action: none; user-select: none;
             background: rgba(127,127,127,.06); border-radius: 6px; }
.vi-eq .grid { stroke: currentColor; stroke-opacity: .12; }
.vi-eq .zero { stroke: currentColor; stroke-opacity: .35; }
.vi-eq .axis { fill: currentColor; fill-opacity: .55; font-size: 11px; }
.vi-eq .spectrum { fill: currentColor; fill-opacity: .10; stroke: none; }
.vi-eq .curve { fill: none; stroke: #1976d2; stroke-width: 2.5; }
.vi-eq .area { fill: #1976d2; fill-opacity: .12; }
.vi-eq .band { fill: none; stroke-width: 1; stroke-opacity: .55; }
.vi-eq .point { cursor: grab; stroke: white; stroke-width: 1.5; }
.vi-eq .point.off { fill-opacity: .3; }
.vi-eq .point-n { font-size: 11px; fill: currentColor; pointer-events: none; }
.vi-eq .rows { overflow-x: auto; }
.vi-eq table { border-collapse: collapse; margin-top: 8px; font-size: 13px; }
.vi-eq td, .vi-eq th { padding: 2px 6px; text-align: left; white-space: nowrap; }
.vi-eq th { font-weight: 500; opacity: .6; font-size: 12px; }
.vi-eq input, .vi-eq select { font: inherit; color: inherit; background: transparent; padding: 1px 4px;
                              border: 1px solid rgba(127,127,127,.35); border-radius: 4px; }
.vi-eq input[type=number] { width: 4.8rem; }
.vi-eq input.name { width: 6rem; }
.vi-eq input:disabled { opacity: .35; }
.vi-eq .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }
.vi-eq .bar { display: flex; gap: 8px; align-items: center; margin-top: 8px; flex-wrap: wrap; }
.vi-eq button { font: inherit; padding: 3px 10px; border-radius: 4px; cursor: pointer; color: inherit;
                border: 1px solid rgba(127,127,127,.45); background: transparent; }
.vi-eq button:disabled { opacity: .4; cursor: default; }
.vi-eq .x { border: none; opacity: .55; padding: 0 6px; }
.vi-eq .hint { font-size: 12px; opacity: .65; }
"""

JS = r"""
window.viEq || (() => {
  const W = 760, H = 280, L = 34, R = 12, T = 10, B = 22, F0 = 20, F1 = 20000, DB = 24, SPEC = 60, MAX = 8;
  const COLOURS = ['#e53935', '#8e24aa', '#3949ab', '#039be5', '#00897b', '#7cb342', '#fb8c00', '#6d4c41'];
  const GAIN = ['peak', 'low_shelf', 'high_shelf'];
  const TYPES = {low_shelf: 'Low shelf', peak: 'Peak', high_shelf: 'High shelf', highpass: 'Low cut',
                 lowpass: 'High cut', notch: 'Notch'};
  const LIMITS = {freq: [20, 20000], q: [0.1, 18], gain_db: [-24, 24]};
  const clamp = (v, [lo, hi]) => Math.min(hi, Math.max(lo, v));
  const x = f => L + Math.log(f / F0) / Math.log(F1 / F0) * (W - L - R);
  const fOf = px => F0 * Math.pow(F1 / F0, (px - L) / (W - L - R));
  const y = db => T + (DB - Math.max(-DB, Math.min(DB, db))) / (2 * DB) * (H - T - B);
  const dbOf = py => DB - (py - T) / (H - T - B) * 2 * DB;
  const ySpec = db => T + Math.min(1, -db / SPEC) * (H - T - B);
  const hz = f => f >= 1000 ? (f / 1000).toPrecision(3).replace(/\.?0+$/, '') + ' kHz' : Math.round(f) + ' Hz';

  // As vinst.eq.coefficients: the audio EQ cookbook's biquads, normalised to a0 = 1.
  function coeffs(b, sr) {
    const w = 2 * Math.PI * Math.min(b.freq, sr * 0.49) / sr, cw = Math.cos(w), sw = Math.sin(w);
    const al = sw / (2 * b.q), A = Math.pow(10, b.gain_db / 40), k = 2 * Math.sqrt(A) * al;
    let n, d;
    if (b.type === 'peak') { n = [1 + al * A, -2 * cw, 1 - al * A]; d = [1 + al / A, -2 * cw, 1 - al / A]; }
    else if (b.type === 'low_shelf') {
      n = [A * ((A + 1) - (A - 1) * cw + k), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - k)];
      d = [(A + 1) + (A - 1) * cw + k, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - k];
    } else if (b.type === 'high_shelf') {
      n = [A * ((A + 1) + (A - 1) * cw + k), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - k)];
      d = [(A + 1) - (A - 1) * cw + k, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - k];
    } else if (b.type === 'highpass') { n = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]; d = [1 + al, -2 * cw, 1 - al]; }
    else if (b.type === 'lowpass') { n = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]; d = [1 + al, -2 * cw, 1 - al]; }
    else { n = [1, -2 * cw, 1]; d = [1 + al, -2 * cw, 1 - al]; }
    return {b: n.map(v => v / d[0]), a: [1, d[1] / d[0], d[2] / d[0]]};
  }
  function gainDb(c, f, sr) {
    const w = 2 * Math.PI * f / sr, c1 = Math.cos(w), s1 = Math.sin(w), c2 = Math.cos(2 * w), s2 = Math.sin(2 * w);
    const nr = c.b[0] + c.b[1] * c1 + c.b[2] * c2, ni = -(c.b[1] * s1 + c.b[2] * s2);
    const dr = c.a[0] + c.a[1] * c1 + c.a[2] * c2, di = -(c.a[1] * s1 + c.a[2] * s2);
    return 10 * Math.log10(Math.max(1e-24, (nr * nr + ni * ni) / (dr * dr + di * di)));
  }
  const FREQS = Array.from({length: 240}, (_, i) => F0 * Math.pow(F1 / F0, i / 239));
  const path = pts => pts.map(([px, py], i) => (i ? 'L' : 'M') + px.toFixed(1) + ',' + py.toFixed(1)).join('');
  const el = (tag, attrs = {}, parent) => {
    const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  };
  const pointY = b => GAIN.includes(b.type) ? y(b.gain_db) : y(0);

  function mount(root) {
    root.dataset.ready = '1';
    const s = {bands: JSON.parse(root.dataset.bands), defaults: JSON.parse(root.dataset.defaults),
               slug: root.dataset.slug, spectrum: [], drag: null, audio: null};
    root.innerHTML = '';
    const svg = el('svg', {viewBox: `0 0 ${W} ${H}`}, root);
    const grid = el('g', {}, svg), spec = el('path', {class: 'spectrum'}, svg);
    const bandsG = el('g', {}, svg), area = el('path', {class: 'area'}, svg), curve = el('path', {class: 'curve'}, svg);
    const points = el('g', {}, svg);
    for (const f of [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]) {
      el('line', {class: 'grid', x1: x(f), x2: x(f), y1: T, y2: H - B}, grid);
      el('text', {class: 'axis', x: x(f), y: H - 6, 'text-anchor': f === 20 ? 'start' : f === 20000 ? 'end' : 'middle'}, grid)
        .textContent = f >= 1000 ? f / 1000 + 'k' : f;
    }
    for (let d = -DB; d <= DB; d += 6) {
      el('line', {class: d ? 'grid' : 'zero', x1: L, x2: W - R, y1: y(d), y2: y(d)}, grid);
      el('text', {class: 'axis', x: L - 4, y: y(d) + 4, 'text-anchor': 'end'}, grid).textContent = (d > 0 ? '+' : '') + d;
    }
    const scroller = document.createElement('div'); scroller.className = 'rows'; root.appendChild(scroller);
    const table = document.createElement('table'); scroller.appendChild(table);
    const bar = document.createElement('div'); bar.className = 'bar'; root.appendChild(bar);
    bar.innerHTML = `<button data-act="add">+ Band</button><button data-act="reset">Reset</button>
      <button data-act="play">▶ Listen</button>
      <label><input type="checkbox" data-act="bypass" checked> Hear with EQ</label>
      <span class="hint">Drag a point · scroll on it for its width (Q) · double-click to add a band</span>`;

    const send = () => root.dispatchEvent(new CustomEvent('eq-change', {detail: s.bands.map(b => ({...b}))}));
    let wheelTimer = null;

    function drawCurve() {
      const sr = 48000, cs = s.bands.filter(b => b.on).map(b => coeffs(b, sr));
      const total = FREQS.map(f => cs.reduce((acc, c) => acc + gainDb(c, f, sr), 0));
      const pts = FREQS.map((f, i) => [x(f), y(total[i])]);
      curve.setAttribute('d', path(pts));
      area.setAttribute('d', path(pts) + `L${x(F1)},${y(0)}L${x(F0)},${y(0)}Z`);
      bandsG.innerHTML = '';
      s.bands.forEach((b, i) => {
        if (!b.on) return;
        const c = coeffs(b, sr);
        el('path', {class: 'band', stroke: COLOURS[i % 8], d: path(FREQS.map(f => [x(f), y(gainDb(c, f, sr))]))}, bandsG);
      });
      points.innerHTML = '';
      s.bands.forEach((b, i) => {
        const p = el('circle', {class: 'point' + (b.on ? '' : ' off'), cx: x(b.freq), cy: pointY(b), r: 8,
                                fill: COLOURS[i % 8], 'data-i': i}, points);
        el('title', {}, p).textContent = `${TYPES[b.type]} · ${hz(b.freq)} · Q ${b.q.toFixed(2)}` +
          (GAIN.includes(b.type) ? ` · ${b.gain_db.toFixed(1)} dB` : '');
        el('text', {class: 'point-n', x: x(b.freq) + 10, y: pointY(b) - 9}, points).textContent = i + 1;
      });
      if (s.spectrum.length) {
        const sp = s.spectrum.map(([f, d]) => [x(f), ySpec(d)]);
        spec.setAttribute('d', path(sp) + `L${sp[sp.length - 1][0]},${H - B}L${sp[0][0]},${H - B}Z`);
      }
      retuneSoon();
    }

    function drawTable() {
      const num = (i, k, step, dis) =>
        `<input type="number" data-i="${i}" data-k="${k}" step="${step}" value="${+s.bands[i][k].toFixed(k === 'q' ? 2 : 1)}"${dis ? ' disabled' : ''}>`;
      // Rebuilt only when bands come or go or move on the curve: rebuilding while typing would lose the focus.
      table.innerHTML = '<tr><th></th><th>On</th><th>Type</th><th>Frequency (Hz)</th><th>Q</th><th>Gain (dB)</th>' +
        '<th>Knob name</th><th></th></tr>' + s.bands.map((b, i) => `<tr>
        <td><span class="dot" style="background:${COLOURS[i % 8]}"></span>${i + 1}</td>
        <td><input type="checkbox" data-i="${i}" data-k="on"${b.on ? ' checked' : ''}></td>
        <td><select data-i="${i}" data-k="type">${Object.entries(TYPES).map(([v, t]) =>
          `<option value="${v}"${v === b.type ? ' selected' : ''}>${t}</option>`).join('')}</select></td>
        <td>${num(i, 'freq', 1)}</td><td>${num(i, 'q', 0.1)}</td><td>${num(i, 'gain_db', 0.5, !GAIN.includes(b.type))}</td>
        <td><input class="name" data-i="${i}" data-k="name" placeholder="${GAIN.includes(b.type) ? hz(b.freq) : 'no knob'}"
             value="${b.name.replace(/"/g, '&quot;')}"${GAIN.includes(b.type) ? '' : ' disabled'}></td>
        <td><button class="x" data-act="remove" data-i="${i}" title="Remove this band">✕</button></td></tr>`).join('');
      bar.querySelector('[data-act=add]').disabled = s.bands.length >= MAX;
    }

    function redraw() { drawCurve(); drawTable(); }

    // What a band's row shows that depends on its other values.
    function syncRow(i) {
      const b = s.bands[i], field = k => table.querySelector(`[data-i="${i}"][data-k="${k}"]`), gain = GAIN.includes(b.type);
      for (const k of ['freq', 'q', 'gain_db']) field(k).value = +b[k].toFixed(k === 'q' ? 2 : 1);
      field('gain_db').disabled = !gain;
      field('name').disabled = !gain;
      field('name').placeholder = gain ? hz(b.freq) : 'no knob';
    }

    table.addEventListener('change', e => {
      const t = e.target, i = +t.dataset.i, k = t.dataset.k;
      if (!k) return;
      const b = s.bands[i];
      if (k === 'on') b.on = t.checked;
      else if (k === 'type') b.type = t.value;
      else if (k === 'name') b.name = t.value;
      else if (t.value !== '' && isFinite(+t.value)) b[k] = clamp(+t.value, LIMITS[k]);
      syncRow(i); drawCurve(); send();
    });
    root.addEventListener('click', e => {
      const act = e.target.dataset && e.target.dataset.act;
      if (act === 'remove') { s.bands.splice(+e.target.dataset.i, 1); redraw(); send(); }
      else if (act === 'add' && s.bands.length < MAX) {
        s.bands.push({type: 'peak', freq: 1000, q: 1, gain_db: 0, name: '', on: true}); redraw(); send();
      } else if (act === 'reset') { s.bands = s.defaults.map(b => ({...b})); redraw(); send(); }
      else if (act === 'play') play(e.target);
      else if (act === 'bypass') retune();
    });

    const local = e => {
      const r = svg.getBoundingClientRect();
      return [(e.clientX - r.left) / r.width * W, (e.clientY - r.top) / r.height * H];
    };
    svg.addEventListener('pointerdown', e => {
      const i = e.target.dataset && e.target.dataset.i;
      if (i === undefined) return;
      s.drag = {i: +i, moved: false};
      svg.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    svg.addEventListener('pointermove', e => {
      if (!s.drag) return;
      const [px, py] = local(e), b = s.bands[s.drag.i];
      b.freq = Math.round(clamp(fOf(px), LIMITS.freq));
      if (GAIN.includes(b.type)) b.gain_db = Math.round(clamp(dbOf(py), LIMITS.gain_db) * 2) / 2;
      s.drag.moved = true;
      drawCurve();
    });
    const drop = () => { if (s.drag && s.drag.moved) { drawTable(); send(); } s.drag = null; };
    svg.addEventListener('pointerup', drop);
    svg.addEventListener('pointercancel', drop);
    svg.addEventListener('wheel', e => {
      const i = e.target.dataset && e.target.dataset.i;
      if (i === undefined) return;
      e.preventDefault();
      const b = s.bands[+i];
      b.q = Math.round(clamp(b.q * Math.exp(-e.deltaY * 0.002), LIMITS.q) * 100) / 100;
      drawCurve();
      clearTimeout(wheelTimer);
      wheelTimer = setTimeout(() => { drawTable(); send(); }, 300);
    }, {passive: false});
    svg.addEventListener('dblclick', e => {
      if (e.target.dataset && e.target.dataset.i !== undefined) return;
      if (s.bands.length >= MAX) return;
      const [px, py] = local(e);
      s.bands.push({type: 'peak', freq: Math.round(clamp(fOf(px), LIMITS.freq)), q: 1,
                    gain_db: Math.round(clamp(dbOf(py), LIMITS.gain_db) * 2) / 2, name: '', on: true});
      redraw(); send();
    });

    // Listening: the samples through the same filters. The browser's IIR filters can't change while
    // playing, so each change builds a new chain and crossfades to it, at most every TUNE_MS.
    const FADE_S = 0.03, TUNE_MS = 50;
    let tuneTimer = null;
    const say = (button, text) => { button.textContent = text; setTimeout(() => { button.textContent = '▶ Listen'; }, 2500); };
    async function play(button) {
      const a = s.audio || (s.audio = {ctx: new (window.AudioContext || window.webkitAudioContext)()});
      if (a.source) { a.source.stop(); return; }
      button.disabled = true;
      try {
        // Fetched each time: the samples may have been sliced again since.
        const r = await fetch(`/eq-demo/${encodeURIComponent(s.slug)}`);
        if (r.status === 404) { say(button, 'Nothing sliced yet'); return; }
        if (!r.ok) throw new Error(`demo: HTTP ${r.status}`);
        const buffer = await a.ctx.decodeAudioData(await r.arrayBuffer());
        await a.ctx.resume();
        const source = a.source = a.ctx.createBufferSource(); source.buffer = buffer;
        source.onended = () => {
          if (a.source === source) a.source = null;
          if (a.path) { a.path.nodes.forEach(n => n.disconnect()); a.path = null; }
          button.textContent = '▶ Listen';
        };
        retune();
        source.start();
        button.textContent = '■ Stop';
      } catch (err) {
        console.error(err);
        say(button, "Can't play");
      } finally { button.disabled = false; }
    }
    function retuneSoon() {
      if (tuneTimer || !s.audio || !s.audio.source) return;
      tuneTimer = setTimeout(() => { tuneTimer = null; retune(); }, TUNE_MS);
    }
    function retune() {
      const a = s.audio;
      if (!a || !a.source) return;
      const ctx = a.ctx, now = ctx.currentTime, source = a.source;
      const on = bar.querySelector('[data-act=bypass]').checked;
      const chain = on ? s.bands.filter(b => b.on).map(b => { const c = coeffs(b, ctx.sampleRate); return ctx.createIIRFilter(c.b, c.a); }) : [];
      const out = ctx.createGain();
      out.gain.setValueAtTime(0, now);
      out.gain.linearRampToValueAtTime(1, now + FADE_S);
      [...chain, out].forEach((node, i, all) => i && all[i - 1].connect(node));
      const head = chain[0] || out;
      source.connect(head);
      out.connect(ctx.destination);
      const old = a.path;
      a.path = {nodes: [...chain, out], head, out};
      if (old) {
        old.out.gain.cancelScheduledValues(now);
        old.out.gain.setValueAtTime(old.out.gain.value, now);
        old.out.gain.linearRampToValueAtTime(0, now + FADE_S);
        setTimeout(() => {
          try { source.disconnect(old.head); } catch (e) { /* already ended */ }
          old.nodes.forEach(n => n.disconnect());
        }, FADE_S * 1000 + 30);
      }
    }

    redraw();
    fetch(`/eq-spectrum/${encodeURIComponent(s.slug)}`).then(r => r.ok ? r.json() : []).then(sp => {
      s.spectrum = sp; drawCurve();
    }).catch(() => {});
  }

  const scan = () => document.querySelectorAll('.vi-eq:not([data-ready])').forEach(mount);
  new MutationObserver(scan).observe(document.body, {childList: true, subtree: true});
  scan();
  window.viEq = true;
})();
"""


def equalizer(ed: "Editor") -> None:
    """The bands' curve over the samples' spectrum, the bands, and listening; changes go to the spec."""
    inst, p = ed.inst, ed.inst.preset
    ui.add_css(CSS)
    ui.add_body_html(f"<script>{JS}</script>")
    box = ui.element("div").classes("vi-eq")
    box._props["data-bands"] = json.dumps([b.model_dump() for b in p.eq])
    box._props["data-defaults"] = json.dumps([b.model_dump() for b in default_eq()])
    box._props["data-slug"] = inst.meta.slug

    def changed(detail: Any) -> None:
        try:
            bands = [EqBand.model_validate(b) for b in detail or []]
        except ValidationError as e:
            ui.notify(f"Band not taken: {e.errors()[0]['msg']}", type="warning")
            return
        if len(bands) <= 8:
            ed.set(p, "eq", bands)

    box.on("eq-change", lambda e: changed(e.args["detail"]), args=["detail"])
