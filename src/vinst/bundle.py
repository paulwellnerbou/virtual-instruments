"""Write the .dsbundle, install it into the DecentSampler library folder, package a .dslibrary."""

import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from vinst import preset, review, store
from vinst.plan import slicing_setup
from vinst.slicing import changed_takes
from vinst.spec import Instrument
from vinst.validate import Report, validate_bundle

STALE_MESSAGE = ("Signals, recording passes, range, extras, layers, round robins or the loop setting changed "
                 "since the takes were sliced. Slice again (step 6); your rejections are kept.")
def changed_message(changed: list[str]) -> str:
    names = ", ".join(changed[:3]) + (f" and {len(changed) - 3} more" if len(changed) > 3 else "")
    return f"Recorded again since slicing: {names}. Slice again (step 6) to use the new recordings."


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


def build(inst: Instrument) -> BuildResult:
    manifest = review.load(store.manifest_path(inst.meta.slug))
    if manifest is None:
        raise ValueError("Nothing sliced yet (step 6).")
    problems = inst.problems()
    if problems:
        raise ValueError("Fix these first: " + " ".join(problems))
    if manifest.setup != slicing_setup(inst):
        raise ValueError(STALE_MESSAGE)
    changed = changed_takes(inst, manifest)
    if changed:
        raise ValueError(changed_message(changed))
    mapping = preset.map_samples(inst, manifest)
    if not mapping.notes:
        raise ValueError("No usable hits: record and slice some notes first.")

    name = bundle_name(inst)
    bundle = build_root() / inst.meta.slug / f"{name}.dsbundle"
    samples_src = Path(manifest.samples_dir)
    missing = [f for f in mapping.files if not (samples_src / f).is_file()]
    if missing:
        raise ValueError(f"{len(missing)} sliced samples are missing in {samples_src}; slice again.")
    sync_files({f: samples_src / f for f in mapping.files}, bundle / preset.SAMPLES_DIR)

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

    size = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
    return BuildResult(bundle, preset_file, len(mapping.files), size, mapping.warnings,
                       validate_bundle(bundle, preset_file), mapping.info)


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
