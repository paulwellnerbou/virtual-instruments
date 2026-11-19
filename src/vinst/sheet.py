"""Printable recording sheet (HTML) and a CSV take list."""

import csv
import io
from html import escape
from pathlib import Path

from vinst.plan import plan_takes, recording_passes, summarize
from vinst.spec import Instrument, TYPE_LABELS


HTML_NAME = "recording-sheet.html"
CSV_NAME = "recording-sheet.csv"


def checklist(inst: Instrument) -> list[str]:
    rec = inst.recording
    passes = recording_passes(inst)
    items = []
    for p in passes:
        tracks = ", ".join(f"<b>{escape(s.id)}</b> ({escape(s.name)})" for s in p.signals)
        prefix = f"Pass “{escape(p.name)}”: " if len(passes) > 1 else ""
        items.append(f"{prefix}one track per signal, named exactly: {tracks}.")
    if len(passes) > 1:
        items.append(f"Record all takes {len(passes)} times, once per pass (sections below). Change the "
                     "pickup or blend setting only between passes, never within one.")
    items += [
        f"{rec.sample_rate / 1000:g} kHz, {rec.bit_depth}-bit, mono per track.",
        "Set the gain once so the loudest hits peak around −12 dBFS, then never change it.",
        "Record the noise floor first: silence with everything connected and powered as for the takes. "
        "It is the reference level for slicing and, if you turn on denoising, the noise profile.",
    ]
    if inst.range.mode == "strings":
        items += [
            "Tune with a strobe tuner before every take.",
            "Damp the strings you're not playing.",
        ]
    items += [
        "Keep the order of hits within a take as listed, quietest layer first.",
        "Export each take as one mono WAV per signal, named <code>&lt;base name&gt;_&lt;signal&gt;.wav</code> "
        "(see the Files column), with all signals of a take covering the same time range. Any DAW or "
        "editor works, e.g. Cubase or WaveLab: a marker or region per take named with the base name, "
        "and tracks named after the signal ids, keeps the renaming to a minimum.",
    ]
    return items


def render_html(inst: Instrument) -> str:
    s = summarize(inst)
    takes = plan_takes(inst)
    rows = []
    passes = recording_passes(inst)
    current = None
    for t in takes:
        if len(passes) > 1 and t.pass_id != current:
            current = t.pass_id
            index = next(i for i, p in enumerate(passes, 1) if p.id == t.pass_id)
            rows.append(f"<tr class=pass><td colspan=6>Pass {index} of {len(passes)}: "
                        f"{escape(t.pass_name)}</td></tr>")
        files = "<br>".join(f"<code>{escape(f)}</code>" for f in t.files)
        instr = " · ".join(escape(i) for i in t.instructions)
        rows.append(
            f"<tr><td class=chk>☐</td><td class=num>{t.number}</td><td><b>{escape(t.title)}</b></td>"
            f"<td>{instr}</td><td class=num>{t.hits or ''}</td><td class=files>{files}</td></tr>"
        )
    checks = "".join(f"<li>{c}</li>" for c in checklist(inst))
    meta = inst.meta
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{escape(meta.name)}: recording sheet</title>
<style>
  body {{ font: 13px/1.4 -apple-system, system-ui, sans-serif; margin: 24px; color: #111; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: #555; margin-bottom: 16px; }}
  .stats {{ display: flex; gap: 24px; margin: 12px 0 16px; }}
  .stats div b {{ display: block; font-size: 18px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border-bottom: 1px solid #ddd; padding: 6px 8px; text-align: left; vertical-align: top; }}
  th {{ background: #f4f4f4; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.chk {{ font-size: 16px; width: 1em; }}
  td.files code {{ font-size: 11px; }}
  tr {{ break-inside: avoid; }}
  tr.pass td {{ background: #eef3f8; font-weight: 600; font-size: 14px; padding-top: 12px; }}
  ul {{ margin: 0 0 16px; padding-left: 20px; }}
  @media print {{ body {{ margin: 0; }} th {{ background: none; }} }}
</style></head><body>
<h1>{escape(meta.name)} · recording sheet</h1>
<div class="sub">{escape(TYPE_LABELS[meta.type])} · v{escape(meta.version)} · {escape(meta.author)}</div>
<div class="stats">
  <div><b>{escape(s.range_text)}</b>range</div>
  <div><b>{s.sampled}</b>sampled notes</div>
  <div><b>{s.takes}</b>takes</div>
  <div><b>{s.hits}</b>hits</div>
  <div><b>~{s.minutes:.0f} min</b>session length (est.)</div>
  <div><b>~{s.sample_mb:,.0f} MB</b>sample data (est.)</div>
</div>
<h2>Before you start</h2>
<ul>{checks}</ul>
<h2>Takes</h2>
<table><thead><tr><th></th><th>#</th><th>What</th><th>Play</th><th>Hits</th><th>Files</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
</body></html>
"""


def render_csv(inst: Instrument) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["take", "pass", "kind", "base_name", "title", "midi", "string", "fret", "hits", "instructions",
                "files"])
    for t in plan_takes(inst):
        n = t.note
        w.writerow([
            t.number, t.pass_name, t.kind, t.base_name, t.title,
            n.midi if n else "", (n.string or "") if n else "", "" if not n or n.fret is None else n.fret,
            t.hits, " / ".join(t.instructions), " ".join(t.files),
        ])
    return buf.getvalue()


def write(inst: Instrument, directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    html_path = directory / HTML_NAME
    csv_path = directory / CSV_NAME
    html_path.write_text(render_html(inst), encoding="utf-8")
    csv_path.write_text(render_csv(inst), encoding="utf-8")
    return html_path, csv_path
