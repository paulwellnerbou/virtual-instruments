"""Write the .dsbundle, install it into the DecentSampler library folder, package a .dslibrary."""

import json
import math
import os
import re
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from vinst import eq, level, lowcut, preset, reverb_send, review, sf2, store
from vinst.review import Manifest
from vinst.plan import slicing_setup
from vinst.slicing import (LOUDNESS_WINDOW_S, body_level, changed_takes, cut_in_samples, db, edited_takes,
                           eq_changed_takes, recut)
from vinst.spec import EqBand, Instrument
from vinst.validate import Report, validate_bundle

# Also shown in the worksheet, next to its slice button: so no pointer to a step here.
FINDER_MESSAGE = ("Hits are now found more precisely than when these takes were sliced. Slicing again keeps "
                  "your decisions and your cuts set by hand.")
CLEANUP_MESSAGE = ("The clean-up of the sliced samples (pitch-relative low cut, denoise) changed since they were "
                   "sliced. Slicing again applies it to all of them; your decisions are kept.")
STALE_MESSAGE = ("Signals, recording passes or the loop setting changed since the takes were sliced; "
                 "slicing again keeps your decisions.")
def _names(titles: list[str]) -> str:
    return ", ".join(titles[:3]) + (f" and {len(titles) - 3} more" if len(titles) > 3 else "")


def changed_message(changed: list[str]) -> str:
    return f"Recorded or changed since slicing: {_names(changed)}."


def eq_message(changed: list[str]) -> str:
    return f"The clean-up EQ changed since slicing for {_names(changed)}. Slicing again applies it; your decisions are kept."


# Marks bundles this tool wrote, so installing never replaces someone else's library.
MARKER = ".vinst-generated"
IMAGES_DIR = "Images"
IMAGE_SIGNATURES = {".png": b"\x89PNG\r\n\x1a\n", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff"}


def build_root() -> Path:
    return Path(os.environ.get("VINST_BUILD", Path.cwd() / "build"))


def library_dir() -> Path:
    return Path(os.environ.get("VINST_DS_LIBRARY", Path.home() / "DecentSampler")).expanduser()


def bundle_name(inst: Instrument) -> str:
    return re.sub(r'[/\\:*?"<>|]+', "-", inst.meta.name).strip() or inst.meta.slug


def is_artwork_image(path: Path) -> bool:
    signature = IMAGE_SIGNATURES.get(path.suffix.lower())
    if signature is None:
        return False
    with path.open("rb") as image:
        return image.read(len(signature)) == signature


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


def sync_files(sources: dict[str, Path], dest: Path, *, compare_contents: bool = False) -> None:
    """Make `dest` contain exactly `sources` (name -> file), copying only what changed."""
    dest.mkdir(parents=True, exist_ok=True)
    for name, src in sources.items():
        target = dest / name
        st = src.stat()
        if target.exists():
            tt = target.stat()
            same_metadata = tt.st_size == st.st_size and int(tt.st_mtime) == int(st.st_mtime)
            if same_metadata and (not compare_contents or src.read_bytes() == target.read_bytes()):
                continue
        shutil.copy2(src, target)
    for existing in dest.iterdir():
        if existing.name not in sources and existing.is_file():
            existing.unlink()


def sync_raised(sources: dict[str, Path], gains: dict[str, float], dest: Path, record: Path,
                low_cuts: dict[str, tuple[float, float, float]] | None = None,
                removed: dict[str, str] | None = None,
                step: Callable[[int, int], None] | None = None) -> list[float]:
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
    for i, (name, src) in enumerate(sources.items()):
        if step:
            step(i, len(sources))
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


def eq_now(inst: Instrument) -> list[EqBand]:
    """The equalizer's bands that play in this build, as its knobs start."""
    return [b for b in inst.preset.eq if b.on] if "eq" in inst.preset.controls else []


def measured(inst: Instrument, manifest: Manifest, cutting: bool, bands: list[EqBand],
             step: Callable[[int, int], None] | None = None) -> Manifest:
    """`manifest` with each note's loudness as it is after the low cut (at lowcut.EVEN_SHARE) and the
    equalizer (as its knobs start), so evening out and the level work with what's played."""
    p = inst.processing
    out = manifest.model_copy(deep=True)
    folder = Path(manifest.samples_dir)
    notes = [t for t in out.takes if t.kind == "note" and t.midi is not None]
    for i, t in enumerate(notes):
        if step:
            step(i, len(notes))
        for h in t.hits:
            src = folder / h.files[t.signals[0]] if t.signals and t.signals[0] in h.files else None
            if h.rejected or src is None or not src.is_file():
                continue
            audio, sr = sf.read(str(src), always_2d=True, dtype="float64")
            x = audio[:, 0]  # the first signal's first channel, as slicing measures loudness
            if cutting:
                y = x - lowcut.EVEN_SHARE * lowcut.removed(x, sr, t.midi, p.low_cut_below, p.low_cut_db)[1]
            else:
                y = x
            y = eq.apply(y, sr, bands)
            n = int(LOUDNESS_WINDOW_S * sr)
            h.rms_db += round(float(db(np.sqrt(np.mean(y[:n] ** 2))) - db(np.sqrt(np.mean(x[:n] ** 2)))), 2)
            if h.body_db is not None:
                h.body_db += round(body_level(y, sr) - body_level(x, sr), 2)
    return out


def eq_overshoot(folder: Path, files: list[str], removed: dict[str, str], bands: list[EqBand],
                 step: Callable[[int, int], None] | None = None) -> float:
    """How far, in dB, the written samples go over the ceiling when played through the equalizer as
    its knobs start; with the low cut knob, at both its ends (a sample alone and with what was cut)."""
    worst = -np.inf
    for i, name in enumerate(files):
        if step:
            step(i, len(files))
        audio, sr = sf.read(str(folder / name), always_2d=True, dtype="float64")
        played = [audio]
        if name in removed:
            low, _ = sf.read(str(folder / removed[name]), always_2d=True, dtype="float64")
            played.append(audio + low)
        for a in played:
            worst = max(worst, float(db(np.max(np.abs(eq.apply(a, sr, bands))))))
    return max(0.0, worst - level.CEILING_DB)


# Share of a build's time per stage, roughly, for its progress bar: (stage, where it starts, where it ends).
STAGES = {"recut": ("Updating edited recordings", 0.0, 0.1), "measure": ("Measuring loudness", 0.1, 0.3),
          "samples": ("Writing samples", 0.3, 0.8), "room": ("Making room for the Equalizer", 0.8, 0.85),
          "soundfont": ("Writing the soundfont", 0.85, 0.95), "validate": ("Validating", 0.95, 1.0)}


def build(inst: Instrument, progress: Callable[[float, str], None] | None = None) -> BuildResult:
    """Builds the bundle; `progress` hears how far it is (0 to 1) and what it's doing."""

    def stage(key: str) -> Callable[[int, int], None]:
        title, lo, hi = STAGES[key]

        def step(i: int, n: int) -> None:
            if progress:
                progress(lo + (hi - lo) * (i / n if n else 0), f"{title} · {i + 1}/{n}" if n > 1 else title)

        return step

    manifest = review.load(store.manifest_path(inst.meta.slug))
    if manifest is None:
        raise ValueError("Nothing sliced yet (step 5).")
    problems = inst.problems()
    if problems:
        raise ValueError("Fix these first: " + " ".join(problems))
    if manifest.setup != slicing_setup(inst):
        raise ValueError(STALE_MESSAGE + " Slice again in the worksheet (step 5).")
    images: dict[str, Path] = {}
    image_paths: dict[str, str] = {}
    for role, source in (("background", inst.preset.background), ("cover", inst.preset.cover_art)):
        if source:
            path = Path(source).expanduser()
            if not path.is_file():
                raise ValueError(f"{role.capitalize()} image not found: {path}")
            if not is_artwork_image(path):
                raise ValueError(f"{role.capitalize()} image must be a PNG or JPG: {path}")
            filename = f"{role}{path.suffix.lower()}"
            images[filename] = path
            image_paths[role] = f"{IMAGES_DIR}/{filename}"

    # Recordings edited without moving a hit (e.g. an EQ) need no slicing: they're cut where they were.
    edited = edited_takes(inst, manifest)
    recut_info = []
    if edited:
        step = stage("recut")
        manifest = recut(inst, manifest, edited, lambda i, n, _title: step(i, n))
        review.save(manifest, store.manifest_path(inst.meta.slug))
        titles = [t.title for t in manifest.takes if t.key in edited]
        recut_info.append(f"Cut again where they were cut, as edited since slicing: {', '.join(titles[:3])}"
                          + (f" and {len(titles) - 3} more." if len(titles) > 3 else "."))
    changed = changed_takes(inst, manifest)
    if changed:
        raise ValueError(changed_message(changed) + " Slice them in the worksheet (step 5) to use them.")
    eq_changed = eq_changed_takes(inst, manifest)
    if eq_changed:
        raise ValueError(eq_message(eq_changed) + " Slice them in the worksheet (step 5).")
    cutting = low_cut_now(inst, manifest)
    bands = eq_now(inst)
    # The cut and the equalizer change how loud a note is (its low resonance goes, a boost adds), so evening
    # out and the level work with the loudness after them: measured here, as they're applied here and live.
    measuring = cutting or bands
    mapping = preset.map_samples(inst, measured(inst, manifest, cutting, bands, stage("measure")) if measuring
                                 else manifest)
    if not mapping.notes:
        raise ValueError("No usable hits: record and slice some notes first.")
    mapping.info[:0] = recut_info
    if cut_in_samples(manifest):
        mapping.warnings.append("Changes to the low cut don't apply: the sliced samples still have the pitch-relative "
                                "low cut they were sliced with; slice them again (step 5) once and it's applied when "
                                "building.")

    name = bundle_name(inst)
    bundle = build_root() / inst.meta.slug / f"{name}.dsbundle"
    samples_src = Path(manifest.samples_dir)
    missing = [f for f in mapping.files if not (samples_src / f).is_file()]
    if missing:
        raise ValueError(f"{len(missing)} sliced samples are missing in {samples_src}; slice again.")
    # The level goes into the samples themselves, with each sample's evening-out correction, so their clicks
    # can be limited; the preset then plays them as they are, or (with loudness following velocity) at
    # most as loud as that, softer below their loudest velocity.
    gains = {e.file: mapping.gain_db for e in mapping.release + mapping.keys}
    loudest: dict[str, float] = {}
    for e in mapping.notes:
        loudest[e.file] = max(loudest.get(e.file, -math.inf), e.volume_db)
    gains.update({f: mapping.gain_db + v for f, v in loudest.items()})
    p = inst.processing
    low_cuts = {e.file: (float(e.root), p.low_cut_below, p.low_cut_db) for e in mapping.notes} if cutting else {}
    if cutting:  # with the knob, which blends back to the uncut samples
        for e in mapping.notes:
            e.removed = removed_name(e.file)
    limited = sync_raised({f: samples_src / f for f in mapping.files}, gains, bundle / preset.SAMPLES_DIR,
                          bundle.parent / "levels.json", low_cuts,
                          {e.file: e.removed for e in mapping.notes if e.removed}, stage("samples"))
    if abs(mapping.gain_db) >= 0.1:
        mapping.info.append(
            f"Level {'raised' if mapping.gain_db > 0 else 'lowered'} by {abs(mapping.gain_db):.1f} dB"
            + (f": the peaks of {len(limited)} samples were held under {level.CEILING_DB:.0f} dBFS, by up to "
               f"{max(limited):.1f} dB." if limited else "."))
    for e in mapping.notes:
        e.volume_db = round(e.volume_db - loudest[e.file], 2)
    mapping.gain_db = 0.0
    if bands:
        # Boosts run after the limiter (an effect in DecentSampler), so they get their room as a volume
        # the instrument plays at; the soundfont takes it into its samples.
        over = eq_overshoot(bundle / preset.SAMPLES_DIR, sorted(mapping.files),
                            {e.file: e.removed for e in mapping.notes if e.removed}, bands, stage("room"))
        if over >= 0.1:
            mapping.gain_db = -over
            mapping.info.append(f"Played {over:.1f} dB quieter: the Equalizer's boosts (as its knobs start) "
                                "stay under full scale.")

    images_dir = bundle / IMAGES_DIR
    if images:
        sync_files(images, images_dir, compare_contents=True)
    elif images_dir.is_dir():
        shutil.rmtree(images_dir)

    reverb_bands = preset.reverb_bands(inst, mapping, preset.active_controls(inst, mapping))
    if reverb_bands:
        own = preset.own_reverb_ir(inst)
        if own and not own.is_file():
            raise ValueError(f"Impulse response not found: {own}")
        irs = reverb_send.write_irs(bundle, reverb_bands, inst.preset.reverb_room, inst.preset.reverb_damping, own,
                                    filtered=inst.preset.reverb_send)
        size = sum((bundle / p).stat().st_size for p in irs) / 1e6
        source = "DecentSampler's reverb (same room and damping)" if own is None else (
            f"{own.name}, cut to {reverb_send.IR_MAX_S:g} s" if reverb_send.too_long(own) else own.name)
        if inst.preset.reverb_send:
            width = reverb_send.band_width([reverb_send.sample_pitch(e.lo, e.hi) for e in mapping.notes])
            mapping.info.append(
                f"No reverb hiss from bright attacks: every sample is also sent to one of "
                f"{len(irs)} buses, one per {width} semitones. Each bus has a convolution reverb playing {source} "
                f"with a −{abs(reverb_send.SHELF_DB):g} dB high shelf from {reverb_send.SHELF_ABOVE:g} × its "
                f"middle note's pitch and a low-pass at {reverb_send.LOWPASS_HZ / 1000:g} kHz "
                f"({len(irs)} impulse responses, {size:.1f} MB).")
        else:
            mapping.info.append(f"Reverb from {source} ({size:.1f} MB).")
    elif (bundle / reverb_send.IR_DIR).is_dir():
        shutil.rmtree(bundle / reverb_send.IR_DIR)

    for old in bundle.glob("*.dspreset"):
        old.unlink()
    preset_file = bundle / f"{name}.dspreset"
    preset_file.write_bytes(preset.render(inst, mapping, image_paths.get("background")))
    (bundle / "DSLibraryInfo.xml").write_bytes(preset.library_info(inst, image_paths.get("cover")))
    (bundle / MARKER).write_text(inst.meta.slug, encoding="utf-8")
    remove_renamed(bundle.parent, bundle)

    # Beside the bundle, not in it: DecentSampler would ship it along in the library.
    stage("soundfont")(0, 1)
    font = sf2.write(inst, mapping, bundle / preset.SAMPLES_DIR, bundle.parent / f"{name}.sf2")
    for old in bundle.parent.glob("*.sf2"):
        if old != font.path:  # from an earlier name of the instrument
            old.unlink()

    stage("validate")(0, 1)
    report = validate_bundle(bundle, preset_file)
    size = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
    return BuildResult(bundle, preset_file, len(mapping.files), size, mapping.warnings,
                       report, mapping.info, font.path, font.samples)


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
        # Images and the XML files can change and keep their size and timestamp (to the second), e.g. a
        # rebuild within a second of installing; they're small, so their contents are compared too.
        sync_files(files, dest / rel_dir, compare_contents=rel_dir in (Path("."), Path(IMAGES_DIR)))
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
