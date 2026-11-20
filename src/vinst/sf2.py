"""An SF2 soundfont of the instrument, for players without DecentSampler, e.g. MuseScore's own synth.

SF2 has no round robins: each key and velocity plays one sample, the one whose root is that key's and
which is best in tune. Release noises have no place in it. Everything else follows the DecentSampler
instrument: the samples as the build wrote them (level, evening out, low cut), tuning, loops, the
release time and how much softer a soft key press plays.
"""

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf

from vinst import eq
from vinst.preset import Entry, Mapping, subtitle
from vinst.spec import Instrument

# Generators and modulator sources of the SF2 2.04 specification.
ATTACK_VOL_ENV, RELEASE_VOL_ENV, INSTRUMENT, KEY_RANGE, VEL_RANGE = 34, 38, 41, 43, 44
INITIAL_ATTENUATION, FINE_TUNE, SAMPLE_ID, SAMPLE_MODES = 48, 52, 53, 54
FILTER_FC = 8
VELOCITY_CONCAVE_NEGATIVE = 0x0502  # the default velocity → attenuation modulator's source
VELOCITY_LINEAR_NEGATIVE = 0x0102
VELOCITY_LINEAR_POSITIVE = 0x0002
VELOCITY_SWITCH = 0x0C02  # the default velocity → filter modulator's amount source
INFO_MAX = 256  # bytes of an INFO string, its terminating zeros included (FluidSynth rejects longer)
PAD = 46  # zero samples the specification wants after each sample


MARK = b"DecentSampler Instrument Builder (vinst)"
OF = "vinst instrument: "  # in its comment, which instrument a soundfont is of
MUSESCORE_FOLDER = Path("~/Documents/MuseScore4/SoundFonts")  # MuseScore 4's default
MUSESCORE_SETTINGS = Path("~/Library/Preferences/org.musescore.MuseScore4.plist")


def musescore_folder(settings: Path = MUSESCORE_SETTINGS) -> Path:
    """Where MuseScore 4 looks for soundfonts: its first folder in Preferences → Folders, or its default."""
    import plistlib

    try:
        with settings.expanduser().open("rb") as f:
            value = plistlib.load(f).get("application.paths.mySoundfonts")
    except (OSError, ValueError):
        value = None
    folders = [Path(v) for v in str(value).split(";") if v.strip()] if value else []
    return folders[0].expanduser() if folders else MUSESCORE_FOLDER.expanduser()


def header(path: Path) -> bytes:
    try:
        with path.open("rb") as f:  # the info comes first
            return f.read(4096)
    except OSError:
        return b""


def made_here(path: Path) -> bool:
    """Whether the soundfont at `path` was written by this builder (it says so in its info)."""
    return MARK in header(path)


def install(font: Path, slug: str, folder: Path | None = None) -> Path:
    """Copies the soundfont into MuseScore's SoundFonts folder, never over one made elsewhere, and
    removes copies of the same instrument under an earlier name."""
    import shutil

    folder = (folder or musescore_folder()).expanduser()
    if not folder.is_dir():
        raise ValueError(f"MuseScore's SoundFonts folder isn't there: {folder}. Is MuseScore 4 installed? Its "
                         "folder can be changed in MuseScore's Preferences → Folders.")
    target = folder / font.name
    if target.exists() and not made_here(target):
        raise ValueError(f"{target} exists and wasn't made by this builder; not replacing it.")
    shutil.copy2(font, target)
    mine = (OF + slug).encode() + b"\0"
    for other in folder.glob("*.sf2"):
        if other != target and made_here(other) and mine in header(other):
            other.unlink()
    return target


@dataclass
class Soundfont:
    path: Path
    presets: list[str] = field(default_factory=list)
    samples: int = 0


def timecents(seconds: float) -> int:
    return -12000 if seconds <= 0.001 else round(1200 * math.log2(seconds))


def ascii_text(value: str) -> bytes:
    """SF2 text is ASCII: accents folded away ("Übung" → "Ubung"), "©" as "(c)", line breaks kept,
    anything else as "_"."""
    import unicodedata

    folded = unicodedata.normalize("NFKD", value.replace("©", "(c)")).encode("ascii", "ignore").decode()
    return "".join(c if " " <= c <= "~" or c == "\n" else "_" for c in folded).encode("ascii")


def name20(value: str) -> bytes:
    return ascii_text(value)[:19].ljust(20, b"\0")


def chunk(tag: bytes, data: bytes) -> bytes:
    # SF2 wants every chunk of even length; RIFF's pad byte outside it trips up readers such as
    # MuseScore's, which then lose their place.
    assert len(data) % 2 == 0, f"{tag!r} chunk of odd length"
    return tag + struct.pack("<I", len(data)) + data


def text(value: str) -> bytes:
    """An INFO string: zero-terminated, padded with another zero to an even length, at most INFO_MAX."""
    data = ascii_text(value)[:INFO_MAX - 2] + b"\0"
    return data + b"\0" * (len(data) % 2)


def credits(inst: Instrument) -> list[bytes]:
    """The author (IENG) and copyright (ICOP), where there are any."""
    meta = inst.meta
    return [chunk(tag, text(value.strip()))
            for tag, value in [(b"IENG", meta.author), (b"ICOP", meta.copyright)] if value.strip()]


def comment(inst: Instrument) -> str:
    """The subtitle, description and website, cut short so the line saying which instrument this is fits behind."""
    mark = OF + inst.meta.slug
    room = INFO_MAX - 2 - len(ascii_text(mark)) - 2
    head = "\n".join(x for x in [subtitle(inst), inst.meta.description.strip(), inst.meta.website.strip()] if x)
    head = ascii_text(head)[:room].decode().strip() if room > 0 else ""
    return f"{head}\n\n{mark}" if head else mark


def riff_list(kind: bytes, *chunks: bytes) -> bytes:
    return chunk(b"LIST", kind + b"".join(chunks))


def pick(entries: list[Entry]) -> list[Entry]:
    """Per signal, key and velocity range, one entry: its own note's sample, the best in tune; keys
    in a row with the same sample become one range."""
    best: dict[tuple, Entry] = {}
    for e in entries:
        for key in range(e.lo, e.hi + 1):
            slot = (e.signal, key, e.lo_vel, e.hi_vel)
            rank = (abs(e.root - key), abs(e.tuning), e.seq_pos)
            have = best.get(slot)
            if have is None or rank < (abs(have.root - key), abs(have.tuning), have.seq_pos):
                best[slot] = e
    out: list[Entry] = []
    for (signal, key, lo_vel, hi_vel), e in sorted(best.items(), key=lambda kv: (kv[0][0], kv[0][2], kv[0][1])):
        last = out[-1] if out else None
        if (last and last.signal == signal and last.file == e.file and last.lo_vel == lo_vel
                and last.hi_vel == hi_vel and last.hi == key - 1):
            last.hi = key
        else:
            out.append(Entry(signal, e.file, e.root, key, key, lo_vel, hi_vel, e.tuning, 1, 1, e.volume_db, e.loop,
                             e.sample_rate))
    return out


def write(inst: Instrument, mapping: Mapping, samples_dir: Path, dest: Path) -> Soundfont:
    """Writes the soundfont of `mapping`, whose samples in `samples_dir` are as the instrument plays
    them (so their volume is in them, as the build writes them), but for `mapping.gain_db`. The
    equalizer, an effect in DecentSampler, goes into the soundfont's samples, as its knobs start."""
    note_zones = pick(mapping.notes)
    zones = note_zones + pick(mapping.keys)
    # Loudness following velocity is a line in dB, as a soundfont's linear velocity modulator makes it.
    curved = {id(z) for z in note_zones} if mapping.loudness_range_db is not None else set()
    per_velocity = (mapping.loudness_range_db or 0.0) / 126
    signals = list(dict.fromkeys(e.signal for e in zones))
    p = inst.preset
    bands = [b for b in p.eq if b.on] if "eq" in p.controls else []
    factor = 10 ** (mapping.gain_db / 20)

    # Sample data: 24 bits, as the upper 16 in smpl and the lower 8 in sm24.
    files = list(dict.fromkeys(e.file for e in zones))
    hi_parts, lo_parts, headers, index = [], [], [], {}
    at = 0
    for i, name in enumerate(files):
        audio, sr = sf.read(str(samples_dir / name), always_2d=True, dtype="float64")
        mono = np.clip(eq.apply(audio.mean(axis=1), sr, bands) * factor, -1.0, 1.0 - 2 ** -23)
        ints = np.round(mono * 2 ** 23).astype(np.int32)
        hi_parts.append(np.concatenate([(ints >> 8).astype("<i2"), np.zeros(PAD, "<i2")]).tobytes())
        lo_parts.append(np.concatenate([(ints & 0xFF).astype("u1"), np.zeros(PAD, "u1")]).tobytes())
        e = next(z for z in zones if z.file == name)
        n = len(ints)
        loop_start, loop_end = (e.loop[0], e.loop[1] + 1) if e.loop else (0, max(1, n - 1))
        headers.append(struct.pack("<20sIIIIIBbHH", name20(f"s{i} {Path(name).stem}"), at, at + n,
                                   at + loop_start, at + loop_end, sr, e.root, 0, 0, 1))
        index[name] = i
        at += n + PAD
    if at % 2:  # sm24 holds a byte per frame: an even number of them, as every chunk must be
        hi_parts.append(np.zeros(1, "<i2").tobytes())
        lo_parts.append(np.zeros(1, "u1").tobytes())
    headers.append(struct.pack("<20sIIIIIBbHH", name20("EOS"), 0, 0, 0, 0, 0, 0, 0, 0, 0))

    # Instruments, one per signal: a global zone (envelope, velocity response), then one zone per range.
    vel_track = p.vel_track if p.vel_track is not None else (0.3 if len(inst.dynamics.layers) > 1 else 0.8)
    softest_cb = round(-200 * math.log10(max(1e-3, 1 - vel_track)))  # as DecentSampler's ampVelTrack
    inst_rec, ibag, igen, imod = [], [], [], []

    def gen(op: int, amount: int) -> bytes:
        return struct.pack("<Hh", op, amount)

    def rng(op: int, lo: int, hi: int) -> bytes:
        return struct.pack("<HBB", op, lo, hi)

    for signal in signals:
        inst_rec.append(struct.pack("<20sH", name20(f"{inst.meta.name} {signal}"), len(ibag)))
        ibag.append(struct.pack("<HH", len(igen), len(imod)))
        igen += [gen(ATTACK_VOL_ENV, timecents(float(p.attack))), gen(RELEASE_VOL_ENV, timecents(float(p.release)))]
        # Players make a soft key press up to 96 dB quieter by default; DecentSampler only a little.
        imod += [struct.pack("<HHhHH", VELOCITY_CONCAVE_NEGATIVE, INITIAL_ATTENUATION, 0, 0, 0),
                 struct.pack("<HHhHH", VELOCITY_LINEAR_NEGATIVE, INITIAL_ATTENUATION, softest_cb, 0, 0),
                 # and from velocity 64 up darker, by up to 1200 cents of filter cutoff: off, as in DecentSampler
                 struct.pack("<HHhHH", VELOCITY_LINEAR_NEGATIVE, FILTER_FC, 0, VELOCITY_SWITCH, 0)]
        for e in (z for z in zones if z.signal == signal):
            ibag.append(struct.pack("<HH", len(igen), len(imod)))
            igen += [rng(KEY_RANGE, e.lo, e.hi), rng(VEL_RANGE, e.lo_vel, e.hi_vel)]
            if id(e) in curved:
                # In cB: per_velocity·(hi_vel − velocity) − volume_db, as A − amount·velocity/128, never below
                # 0 within the zone (players clamp it there). The instrument's velocity modulator is
                # replaced by one of 0: same source and destination.
                amount = round(1280 * per_velocity)
                igen.append(gen(INITIAL_ATTENUATION, round(10 * (per_velocity * e.hi_vel - e.volume_db))))
                imod += [struct.pack("<HHhHH", VELOCITY_LINEAR_NEGATIVE, INITIAL_ATTENUATION, 0, 0, 0),
                         struct.pack("<HHhHH", VELOCITY_LINEAR_POSITIVE, INITIAL_ATTENUATION, -amount, 0, 0)]
            elif e.volume_db < 0:
                igen.append(gen(INITIAL_ATTENUATION, round(-10 * e.volume_db)))
            if e.tuning:
                igen.append(gen(FINE_TUNE, round(e.tuning * 100)))
            if e.loop:
                igen.append(gen(SAMPLE_MODES, 1))
            igen.append(gen(SAMPLE_ID, index[e.file]))
    inst_rec.append(struct.pack("<20sH", name20("EOI"), len(ibag)))
    ibag.append(struct.pack("<HH", len(igen), len(imod)))
    igen.append(struct.pack("<HH", 0, 0))
    imod.append(struct.pack("<HHhHH", 0, 0, 0, 0, 0))

    # Presets, one per signal, each playing its instrument.
    presets, phdr, pbag, pgen = [], [], [], []
    for number, signal in enumerate(signals):
        title = inst.meta.name if len(signals) == 1 else f"{inst.meta.name} {signal}"
        presets.append(title)
        phdr.append(struct.pack("<20sHHHIII", name20(title), number, 0, len(pbag), 0, 0, 0))
        pbag.append(struct.pack("<HH", len(pgen), 0))
        pgen.append(gen(INSTRUMENT, number))
    phdr.append(struct.pack("<20sHHHIII", name20("EOP"), 0, 0, len(pbag), 0, 0, 0))
    pbag.append(struct.pack("<HH", len(pgen), 0))
    pgen.append(struct.pack("<HH", 0, 0))
    pmod = [struct.pack("<HHhHH", 0, 0, 0, 0, 0)]

    info = riff_list(b"INFO", chunk(b"ifil", struct.pack("<HH", 2, 4)), chunk(b"isng", text("EMU8000")),
                     chunk(b"INAM", text(inst.meta.name)), chunk(b"ISFT", text(MARK.decode())),
                     *credits(inst), chunk(b"ICMT", text(comment(inst))))
    sdta = riff_list(b"sdta", chunk(b"smpl", b"".join(hi_parts)), chunk(b"sm24", b"".join(lo_parts)))
    pdta = riff_list(b"pdta", chunk(b"phdr", b"".join(phdr)), chunk(b"pbag", b"".join(pbag)),
                     chunk(b"pmod", b"".join(pmod)), chunk(b"pgen", b"".join(pgen)),
                     chunk(b"inst", b"".join(inst_rec)), chunk(b"ibag", b"".join(ibag)),
                     chunk(b"imod", b"".join(imod)), chunk(b"igen", b"".join(igen)),
                     chunk(b"shdr", b"".join(headers)))
    body = b"sfbk" + info + sdta + pdta
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    part.replace(dest)
    return Soundfont(dest, presets, len(files))
