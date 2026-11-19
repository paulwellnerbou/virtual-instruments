"""Instruments on disk: <home>/<slug>/instrument.toml."""

import os
from pathlib import Path

from vinst.spec import Instrument, slugify

SPEC_FILE = "instrument.toml"


def home() -> Path:
    return Path(os.environ.get("VINST_HOME", Path.cwd() / "instruments"))


def instrument_dir(slug: str) -> Path:
    return home() / slug


def scan() -> tuple[list[Instrument], dict[str, str]]:
    """Valid instruments, plus folder name -> error for spec files that don't load."""
    root = home()
    if not root.is_dir():
        return [], {}
    good, broken = [], {}
    for path in sorted(root.glob(f"*/{SPEC_FILE}")):
        try:
            good.append(Instrument.load(path))
        except Exception as e:  # hand-edited TOML: report it instead of hiding all instruments
            broken[path.parent.name] = str(e)
    return good, broken


def list_instruments() -> list[Instrument]:
    return scan()[0]


def load(slug: str) -> Instrument:
    return Instrument.load(instrument_dir(slug) / SPEC_FILE)


def save(inst: Instrument) -> Path:
    path = instrument_dir(inst.meta.slug) / SPEC_FILE
    inst.save(path)
    return path


def unique_slug(name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while (instrument_dir(slug) / SPEC_FILE).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def create(inst: Instrument) -> Instrument:
    inst.meta.slug = unique_slug(inst.meta.name)
    save(inst)
    return inst


def duplicate(slug: str, new_name: str) -> Instrument:
    copy = load(slug).model_copy(deep=True)
    copy.meta.name = new_name
    copy.recording.takes_dir = ""
    copy.recording.samples_dir = ""
    return create(copy)


def manifest_path(slug: str) -> Path:
    from vinst.review import MANIFEST_FILE

    return instrument_dir(slug) / MANIFEST_FILE
