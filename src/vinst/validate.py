"""Check a generated bundle: XSD, engine vocabulary, sample files, key/velocity coverage."""

import json
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path

from lxml import etree

from vinst.spec import NOTES_TAG

SCHEMA_PKG = "vinst.vendor.decentsampler_schema"


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@cache
def _schema() -> etree.XMLSchema:
    with resources.as_file(resources.files(SCHEMA_PKG) / "DecentSampler.xsd") as path:
        return etree.XMLSchema(etree.parse(str(path)))


@cache
def _vocabulary() -> dict:
    return json.loads((resources.files(SCHEMA_PKG) / "engine-vocabulary.json").read_text(encoding="utf-8"))


def check_xml(doc: etree._ElementTree, report: Report) -> None:
    schema = _schema()
    if not schema.validate(doc):
        report.errors += [f"line {e.line}: {e.message}" for e in schema.error_log]
    vocab = _vocabulary()
    binding = vocab["binding"]
    for b in doc.iter("binding"):
        for attr, allowed in (("parameter", binding["parameters"]), ("type", binding["types"]),
                              ("level", binding["levels"])):
            v = b.get(attr)
            if v is not None and v not in allowed:
                hint = vocab["suggestions"].get(attr + "s", {}).get(v)
                report.errors.append(f"line {b.sourceline}: unknown binding {attr} “{v}”"
                                     + (f" (did you mean {hint}?)" if hint else ""))
    for el in doc.iter():
        mode = el.get("seqMode")
        if mode is not None and mode not in vocab["seqModes"]:
            report.errors.append(f"line {el.sourceline}: unknown seqMode “{mode}”")


def check_files(doc: etree._ElementTree, bundle: Path, report: Report) -> None:
    listings: dict[Path, set[str]] = {}
    for el in doc.iter("sample"):
        rel = el.get("path", "")
        if "\\" in rel:
            report.errors.append(f"sample path uses backslashes: {rel}")
        path = bundle / rel
        folder = path.parent
        if folder not in listings:
            listings[folder] = {p.name for p in folder.iterdir()} if folder.is_dir() else set()
        # Exact-case check: the default macOS filesystem would find a wrongly cased file anyway.
        if path.name not in listings[folder]:
            report.errors.append(f"missing sample file: {rel}")
    ui = doc.find("ui")
    if ui is not None and ui.get("bgImage") and not (bundle / ui.get("bgImage")).is_file():
        report.errors.append(f"missing background image: {ui.get('bgImage')}")


def _ranges(values: list[int]) -> str:
    out, start = [], None
    for i, v in enumerate(values):
        if start is None:
            start = v
        if i + 1 == len(values) or values[i + 1] != v + 1:
            out.append(f"{start}" if start == v else f"{start}–{v}")
            start = None
    return ", ".join(out)


def check_coverage(doc: etree._ElementTree, report: Report) -> None:
    """Every key between the lowest and highest mapped note must sound at every velocity."""
    for group in doc.iter("group"):
        if NOTES_TAG not in (group.get("tags") or "").split(","):
            continue
        covered: dict[int, set[int]] = {}
        for s in group.iter("sample"):
            vels = set(range(int(s.get("loVel", 0)), int(s.get("hiVel", 127)) + 1))
            for key in range(int(s.get("loNote", 0)), int(s.get("hiNote", 127)) + 1):
                covered.setdefault(key, set()).update(vels)
        if not covered:
            continue
        gaps = [k for k in range(min(covered), max(covered) + 1) if not set(range(1, 128)) <= covered.get(k, set())]
        if gaps:
            report.errors.append(f"group “{group.get('name')}”: keys {_ranges(gaps)} aren't covered at all velocities")


def check_round_robins(doc: etree._ElementTree, report: Report) -> None:
    """Within one key/velocity cell, positions must be 1..seqLength, or some notes stay silent."""
    for group in doc.iter("group"):
        if group.get("seqMode") in (None, "always"):
            continue
        cells: dict[tuple[str, ...], tuple[set[int], set[int]]] = {}
        for s in group.iter("sample"):
            cell = (s.get("loNote"), s.get("hiNote"), s.get("loVel"), s.get("hiVel"))
            positions, lengths = cells.setdefault(cell, (set(), set()))
            positions.add(int(s.get("seqPosition", 1)))
            lengths.add(int(s.get("seqLength", 0)))
        for (lo, hi, lv, hv), (positions, lengths) in cells.items():
            if len(lengths) != 1 or positions != set(range(1, max(lengths) + 1)):
                report.errors.append(f"group “{group.get('name')}”: round robins for keys {lo}–{hi}, "
                                     f"velocities {lv}–{hv} have positions {sorted(positions)}, "
                                     f"lengths {sorted(lengths)}")


def validate_bundle(bundle: Path, preset_file: Path) -> Report:
    report = Report()
    try:
        doc = etree.parse(str(preset_file))
    except etree.XMLSyntaxError as e:
        report.errors.append(f"not valid XML: {e}")
        return report
    check_xml(doc, report)
    check_files(doc, bundle, report)
    check_coverage(doc, report)
    check_round_robins(doc, report)
    info = bundle / "DSLibraryInfo.xml"
    if not info.is_file():
        report.warnings.append("DSLibraryInfo.xml is missing")
        return report
    try:
        cover = etree.parse(info).getroot().get("coverArt")
    except etree.XMLSyntaxError as e:
        report.errors.append(f"DSLibraryInfo.xml is not valid XML: {e}")
        return report
    if cover and not (info.parent / cover).is_file():
        report.errors.append(f"missing cover image: {cover}")
    return report
