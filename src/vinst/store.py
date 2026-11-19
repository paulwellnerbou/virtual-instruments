"""Instruments on disk: <home>/<slug>/instrument.toml."""

import os
import re
import tomllib
from pathlib import Path

import tomli_w
from pydantic import BaseModel, ValidationError

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


SETTINGS_FILE = "settings.toml"
DEFAULT_RECORDINGS_DIR = "~/Music/DecentSampler Recordings"


class Settings(BaseModel):
    """App-wide preferences, stored next to the instruments."""

    recordings_dir: str = DEFAULT_RECORDINGS_DIR  # new instruments record into <this>/<instrument name>
    input_device: str = ""  # audio interface for recording in the app; empty: the system's default input


def load_settings() -> Settings:
    path = home() / SETTINGS_FILE
    try:
        return Settings.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except (OSError, tomllib.TOMLDecodeError, ValidationError):
        return Settings()


def save_settings(settings: Settings) -> None:
    home().mkdir(parents=True, exist_ok=True)
    (home() / SETTINGS_FILE).write_text(tomli_w.dumps(settings.model_dump()), encoding="utf-8")


def default_takes_dir(name: str) -> str:
    folder = re.sub(r'[/\\:*?"<>|]+', "-", name).strip() or "instrument"
    return str(Path(load_settings().recordings_dir) / folder)


def create(inst: Instrument) -> Instrument:
    inst.meta.slug = unique_slug(inst.meta.name)
    if not inst.recording.takes_dir:
        inst.recording.takes_dir = default_takes_dir(inst.meta.name)
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
