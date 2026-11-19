"""Write the .dsbundle, install it into the DecentSampler library folder, package a .dslibrary."""

import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from vinst import level, lowcut, preset, review, sf2, store
from vinst.review import Manifest
from vinst.plan import slicing_setup
from vinst.slicing import changed_takes, cut_in_samples
from vinst.spec import Instrument
from vinst.validate import Report, validate_bundle

# Also shown in the worksheet, next to its slice button: so no pointer to a step here.
FINDER_MESSAGE = ("Hits are now found more precisely than when these takes were sliced. Slicing again keeps "
                  "your decisions and your cuts set by hand.")
CLEANUP_MESSAGE = ("The clean-up of the sliced samples (low cut, denoise) changed since they were sliced. "
                   "Slicing again applies it to all of them; your decisions are kept.")
STALE_MESSAGE = ("Signals, recording passes or the loop setting changed since the takes were sliced; "
                 "slicing again keeps your decisions.")
def changed_message(changed: list[str]) -> str:
    names = ", ".join(changed[:3]) + (f" and {len(changed) - 3} more" if len(changed) > 3 else "")
    return f"Recorded or changed since slicing: {names}."


# Marks bundles this tool wrote, so installing never replaces someone else's library.
MARKER = ".vinst-generated"


def build_root() -> Path:
    return Path(os.environ.get("VINST_BUILD", Path.cwd() / "build"))


def library_dir() -> Path:
    return Path(os.environ.get("VINST_DS_LIBRARY", Path.home() / "DecentSampler")).expanduser()


def bundle_name(inst: Instrument) -> str:
    return re.sub(r'[/\\:*?"<>|]+', "-", inst.meta.name).strip() or inst.meta.slug


@dataclass
class BuildResult:
    bundle: Path
    preset_file: Path
    samples: int
    size_bytes: int
    warnings: list[str] = field(default_factory=list)
    report: Report = field(default_factory=Report)
    info: list[str] = field(default_factory=list)
    soundfont: Path | None = None  # the same instrument as SF2, e.g. for MuseScore (see sf2)
    soundfont_samples: int = 0


def sync_files(sources: dict[str, Path], dest: Path) -> None:
    """Make `dest` contain exactly `sources` (name -> file), copying only what changed."""
    dest.mkdir(parents=True, exist_ok=True)
    for name, src in sources.items():
        target = dest / name
        st = src.stat()
        if target.exists():
            tt = target.stat()
            if tt.st_size == st.st_size and int(tt.st_mtime) == int(st.st_mtime):
                continue
        shutil.copy2(src, target)
    for existing in dest.iterdir():
        if existing.name not in sources and existing.is_file():
            existing.unlink()


def sync_raised(sources: dict[str, Path], gains: dict[str, float], dest: Path, record: Path,
                low_cuts: dict[str, tuple[float, float, float]] | None = None,
                removed: dict[str, str] | None = None) -> list[float]:
    """Like sync_files, but each sample written raised by its gain and limited (see level), after its
    low cut if any, and what the cut took out to its file in `removed`, if it has one there; `record`
    keeps what each was written from, so unchanged ones aren't written again. Returns, per sample the
    limiter touched, how much it took off."""
    low_cuts, removed = low_cuts or {}, removed or {}
    dest.mkdir(parents=True, exist_ok=True)
    try:
        done = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        done = {}
    out: dict[str, dict] = {}
    for name, src in sources.items():
        st = src.stat()
        cut = low_cuts.get(name)
        wanted = {"source": f"{st.st_size}:{st.st_mtime_ns}", "gain": round(gains.get(name, 0.0), 2),
                  "limiter": f"{level.CEILING_DB:g} dB, {level.LIMITER_S:g} s"
                             + (", with what was cut" if name in removed else ""),
                  "low cut": list(cut) if cut else None, "removed": removed.get(name)}
        was = done.get(name)
        written = [dest / name] + ([dest / removed[name]] if name in removed else [])
        if not (all(f.exists() for f in written) and was and all(was.get(k) == v for k, v in wanted.items())):
            wanted["reduced"] = round(level.write_raised(
                src, dest / name, wanted["gain"], cut, dest / removed[name] if name in removed else None), 2)
        else:
            wanted["reduced"] = was.get("reduced", 0.0)
        out[name] = wanted
    keep = set(sources) | {removed[n] for n in sources if n in removed}
    for existing in dest.iterdir():
        if existing.name not in keep and existing.is_file():
            existing.unlink()
    record.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return [v["reduced"] for v in out.values() if v["reduced"] > 0.05]


REMOVED_SUFFIX = "-low-part"


def removed_name(file: str) -> str:
    """The file for what the low cut took out of the sample `file`."""
    stem, dot, ext = file.rpartition(".")
    return f"{stem}{REMOVED_SUFFIX}{dot}{ext}" if dot else file + REMOVED_SUFFIX


def low_cut_now(inst: Instrument, manifest: Manifest | None) -> bool:
    """Whether to apply the low cut in this build: when it's on, for notes with a pitch to cut below,
    and not into samples sliced with it already."""
    return inst.processing.low_cut and lowcut.applies(inst) and not (manifest and cut_in_samples(manifest))


def after_low_cut(inst: Instrument, manifest: Manifest) -> Manifest:
    """`manifest` with each note's loudness as it is after the low cut."""
    p = inst.processing
    out = manifest.model_copy(deep=True)
    folder = Path(manifest.samples_dir)
    for t in out.takes:
        if t.kind != "note" or t.midi is None:
            continue
        for h in t.hits:
            src = folder / h.files[t.signals[0]] if t.signals and t.signals[0] in h.files else None
            if h.rejected or src is None or not src.is_file():
                continue
            d_attack, d_body = lowcut.loudness_change(src, t.midi, p.low_cut_below, p.low_cut_db)
            h.rms_db += d_attack
            if h.body_db is not None:
                h.body_db += d_body
    return out


def build(inst: Instrument) -> BuildResult:
    manifest = review.load(store.manifest_path(inst.meta.slug))
    if manifest is None:
        raise ValueError("Nothing sliced yet (step 6).")
    problems = inst.problems()
    if problems:
        raise ValueError("Fix these first: " + " ".join(problems))
    if manifest.setup != slicing_setup(inst):
        raise ValueError(STALE_MESSAGE + " Slice again in the worksheet (step 5).")
    changed = changed_takes(inst, manifest)
    if changed:
        raise ValueError(changed_message(changed) + " Slice them in the worksheet (step 5) to use them.")
    cutting = low_cut_now(inst, manifest)
    # The cut changes how loud a note is (its thump goes), so evening out and the level work with the
    # loudness after it: measured here, as the cut is applied here.
    mapping = preset.map_samples(inst, after_low_cut(inst, manifest) if cutting else manifest)
    if not mapping.notes:
        raise ValueError("No usable hits: record and slice some notes first.")
    if cut_in_samples(manifest):
        mapping.warnings.append("The sliced samples still have the low cut they were sliced with, so changes to it "
                                "don't apply; slice them again (step 5) once and it's applied when building.")

    name = bundle_name(inst)
    bundle = build_root() / inst.meta.slug / f"{name}.dsbundle"
    samples_src = Path(manifest.samples_dir)
    missing = [f for f in mapping.files if not (samples_src / f).is_file()]
    if missing:
        raise ValueError(f"{len(missing)} sliced samples are missing in {samples_src}; slice again.")
    # The level goes into the samples themselves, with each sample's evening-out correction, so their clicks
    # can be limited; the preset then plays them as they are.
    gains = {e.file: mapping.gain_db for e in mapping.release + mapping.keys}
    gains.update({e.file: mapping.gain_db + e.volume_db for e in mapping.notes})
    p = inst.processing
    low_cuts = {e.file: (float(e.root), p.low_cut_below, p.low_cut_db) for e in mapping.notes} if cutting else {}
    if cutting and "lowcut" in inst.preset.controls:
        for e in mapping.notes:
            e.removed = removed_name(e.file)
    limited = sync_raised({f: samples_src / f for f in mapping.files}, gains, bundle / preset.SAMPLES_DIR,
                          bundle.parent / "levels.json", low_cuts,
                          {e.file: e.removed for e in mapping.notes if e.removed})
    if abs(mapping.gain_db) >= 0.1:
        mapping.info.append(
            f"Level {'raised' if mapping.gain_db > 0 else 'lowered'} by {abs(mapping.gain_db):.1f} dB"
            + (f"; the peaks of {len(limited)} samples were held under {level.CEILING_DB:.0f} dBFS, by up to "
               f"{max(limited):.1f} dB." if limited else "."))
    for e in mapping.notes:
        e.volume_db = 0.0
    mapping.gain_db = 0.0

    background = None
    if inst.preset.background:
        src = Path(inst.preset.background).expanduser()
        if not src.is_file():
            raise ValueError(f"Background image not found: {src}")
        background = f"Images/background{src.suffix.lower()}"
        sync_files({Path(background).name: src}, bundle / "Images")
    elif (bundle / "Images").is_dir():
        shutil.rmtree(bundle / "Images")

    for old in bundle.glob("*.dspreset"):
        old.unlink()
    preset_file = bundle / f"{name}.dspreset"
    preset_file.write_bytes(preset.render(inst, mapping, background))
    (bundle / "DSLibraryInfo.xml").write_bytes(preset.library_info(inst))
    (bundle / MARKER).write_text(inst.meta.slug, encoding="utf-8")
    remove_renamed(bundle.parent, bundle)

    # Beside the bundle, not in it: DecentSampler would ship it along in the library.
    font = sf2.write(inst, mapping, bundle / preset.SAMPLES_DIR, bundle.parent / f"{name}.sf2")
    for old in bundle.parent.glob("*.sf2"):
        if old != font.path:  # from an earlier name of the instrument
            old.unlink()

    size = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
    return BuildResult(bundle, preset_file, len(mapping.files), size, mapping.warnings,
                       validate_bundle(bundle, preset_file), mapping.info, font.path, font.samples)


def _marked_slug(folder: Path) -> str | None:
    marker = folder / MARKER
    return marker.read_text(encoding="utf-8").strip() if marker.is_file() else None


def remove_renamed(folder: Path, keep: Path) -> list[Path]:
    """Delete bundles this tool made for the same instrument under an older name."""
    slug = _marked_slug(keep)
    removed = []
    for other in folder.glob("*.dsbundle"):
        if other != keep and other.is_dir() and slug and _marked_slug(other) == slug:
            shutil.rmtree(other)
            removed.append(other)
    return removed


def install(bundle: Path) -> Path:
    """Mirror the bundle into the DecentSampler library folder."""
    target = library_dir() / bundle.name
    if target.exists() and not (target / MARKER).exists():
        raise ValueError(f"{target} exists and wasn't created by this tool; not replacing it.")
    _mirror(bundle, target)
    remove_renamed(library_dir(), target)
    return target


def _mirror(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    wanted = {p.relative_to(src) for p in src.rglob("*")}
    for p in src.rglob("*"):
        if p.is_dir():
            (dest / p.relative_to(src)).mkdir(parents=True, exist_ok=True)
    sync_by_dir: dict[Path, dict[str, Path]] = {}
    for p in src.rglob("*"):
        if p.is_file():
            sync_by_dir.setdefault(p.parent.relative_to(src), {})[p.name] = p
    for rel_dir, files in sync_by_dir.items():
        sync_files(files, dest / rel_dir)
    for p in sorted(dest.rglob("*"), reverse=True):
        if p.relative_to(dest) not in wanted:
            p.unlink() if p.is_file() else shutil.rmtree(p)


def package(inst: Instrument, bundle: Path) -> Path:
    """Zip the bundle as <name>-<version>.dslibrary next to it."""
    out = bundle.parent / f"{bundle.stem}-{inst.meta.version}.dslibrary"
    with zipfile.ZipFile(out, "w") as z:
        for p in sorted(bundle.rglob("*")):
            if p.is_file() and p.name != MARKER:
                # Audio barely compresses; storing it keeps packaging fast.
                method = zipfile.ZIP_STORED if p.suffix.lower() in (".wav", ".aif", ".aiff", ".flac") \
                    else zipfile.ZIP_DEFLATED
                z.write(p, Path(bundle.name) / p.relative_to(bundle), compress_type=method)
    return out
