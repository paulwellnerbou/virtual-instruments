from pathlib import Path

import pytest
from conftest import piezo_and_mic

from vinst import defaults, notes, sheet, store
from vinst.plan import natural_string, plan_takes, sampled_notes, summarize
from vinst.spec import Instrument, InstrumentType


@pytest.mark.parametrize("text, midi", [("C3", 60), ("c3", 60), ("G2", 55), ("C#3", 61), ("Db3", 61),
                                        ("A3", 69), ("C-2", 0), ("G8", 127), ("64", 64)])
def test_parse(text, midi):
    assert notes.parse(text) == midi


@pytest.mark.parametrize("bad", ["H3", "E#3", "C9", "", "x"])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        notes.parse(bad)


def test_name_roundtrip():
    for midi in range(128):
        assert notes.parse(notes.name(midi)) == midi


def uke(tuning="Ukulele, low G (G C E A)", frets=12):
    inst = piezo_and_mic(defaults.new_instrument("Uke", InstrumentType.PLUCKED))
    inst.range.strings = defaults.tuning_strings(tuning, frets)
    return inst


def test_low_g_ukulele_notes_on_natural_strings():
    got = [(n.midi, n.string, n.fret) for n in sampled_notes(uke())]
    assert got[0] == (55, "G", 0)
    assert len(got) == 81 - 55 + 1
    assert (59, "G", 4) in got and (60, "C", 0) in got and (64, "E", 0) in got
    assert (68, "E", 4) in got and (69, "A", 0) in got and got[-1] == (81, "A", 12)


def test_reentrant_ukulele_starts_at_c_and_uses_open_g():
    got = {n.midi: (n.string, n.fret) for n in sampled_notes(uke("Ukulele, re-entrant (g C E A)"))}
    assert min(got) == 60 and max(got) == 81
    assert got[67] == ("G", 0)


def test_guitar_strings_get_unique_names():
    names = [s.name for s in defaults.tuning_strings("Guitar (E A D G B E)")]
    assert names == ["E1", "A", "D", "G", "B", "E3"]


def test_natural_string_prefers_lowest_fret():
    strings = defaults.tuning_strings("Ukulele, low G (G C E A)")
    s, fret = natural_string(strings, 62)
    assert (s.name, fret) == ("C", 2)


def test_gap_between_strings_is_skipped():
    inst = uke(frets=2)  # G2..A2, C3..D3, E3..F#3, A3..B3: gaps between strings
    midis = [n.midi for n in sampled_notes(inst)]
    assert 58 not in midis and 60 in midis


def test_density_always_includes_top_note():
    inst = defaults.new_instrument("Pad", InstrumentType.SUSTAINED)
    inst.range.low, inst.range.high, inst.range.density = 60, 70, 3
    assert [n.midi for n in sampled_notes(inst)] == [60, 63, 66, 69, 70]


def test_takes_and_files():
    inst = uke()
    takes = plan_takes(inst)
    assert takes[0].kind == "noise_floor"
    first_note = takes[1]
    assert first_note.base_name == "055_G-0"
    assert first_note.files == ["055_G-0_piezo.wav", "055_G-0_mic.wav"]
    assert first_note.hits == 3 * 4
    s = summarize(inst)
    assert s.sampled == 27 and s.hits == 27 * 12 + 12 + 10 + 10


def test_unpitched_sounds():
    inst = defaults.new_instrument("Knocks", InstrumentType.UNPITCHED)
    assert [t.base_name for t in plan_takes(inst) if t.kind == "note"] == ["060_Hit"]


@pytest.mark.parametrize("kind", list(InstrumentType))
def test_every_type_has_valid_defaults_and_roundtrips(kind, tmp_path):
    inst = defaults.new_instrument("Test", kind)
    assert inst.problems() == []
    assert plan_takes(inst)
    inst.save(tmp_path / "instrument.toml")
    assert Instrument.load(tmp_path / "instrument.toml") == inst


def test_problems_flag_duplicate_signal_ids_case_insensitively():
    inst = uke()
    inst.signals[1].id = "PIEZO"
    assert any("Duplicate signal id" in p for p in inst.problems())


def test_store_create_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path))
    a = store.create(defaults.new_instrument("Bob's Uke", InstrumentType.PLUCKED))
    assert a.meta.slug == "bobs-uke"
    b = store.duplicate("bobs-uke", "Bob's Uke")
    assert b.meta.slug == "bobs-uke-2"
    assert [i.meta.slug for i in store.list_instruments()] == ["bobs-uke", "bobs-uke-2"]


def test_sheet_contains_files(tmp_path):
    html_path, csv_path = sheet.write(uke(), tmp_path)
    html = html_path.read_text()
    assert "055_G-0_piezo.wav" in html and "Cubase or WaveLab" in html and "REAPER" not in html
    assert csv_path.read_text().splitlines()[0].startswith("take,pass,kind,base_name")


def test_sample_size_and_recorded_files(tmp_path):
    inst = uke()
    # 324 note hits × 6 s + 32 extra hits × 2 s, 48 kHz, 24-bit, 2 signals
    expected = (324 * 6 + 32 * 2) * 48000 * 3 * 2 / 1e6
    s = summarize(inst)
    assert abs(s.sample_mb - expected) < 0.01
    assert s.recorded_files == 0
    inst.recording.takes_dir = str(tmp_path)
    (tmp_path / "055_G-0_piezo.wav").touch()
    (tmp_path / "unrelated.wav").touch()
    assert summarize(inst).recorded_files == 1


def test_scan_reports_broken_specs_without_hiding_the_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path))
    store.create(defaults.new_instrument("Good", InstrumentType.PLUCKED))
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "instrument.toml").write_text('[meta]\nname = "Bad\n')
    good, broken = store.scan()
    assert [i.meta.slug for i in good] == ["good"]
    assert list(broken) == ["bad"]


def test_old_room_tone_key_still_loads():
    inst = uke()
    text = inst.to_toml().replace("noise_floor_seconds", "room_tone_seconds")
    assert Instrument.from_toml(text).recording.noise_floor_seconds == 30


def test_sheet_shows_fractional_khz():
    inst = uke()
    inst.recording.sample_rate = 44100
    assert "44.1 kHz" in sheet.render_html(inst)


def test_new_instruments_default_to_the_system_user_as_author():
    inst = defaults.new_instrument("X", InstrumentType.PLUCKED)
    assert inst.meta.author == defaults.system_author() != ""
    assert defaults.new_instrument("Y", InstrumentType.PLUCKED, author="").meta.author == ""


def test_new_and_duplicated_instruments_get_a_default_takes_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path))
    assert store.load_settings().recordings_dir == store.DEFAULT_RECORDINGS_DIR
    store.save_settings(store.Settings(recordings_dir="/Volumes/Audio/Recordings"))
    a = store.create(defaults.new_instrument("Tenor: Uke", InstrumentType.PLUCKED))
    assert a.recording.takes_dir == "/Volumes/Audio/Recordings/Tenor- Uke"
    b = store.duplicate(a.meta.slug, "Second Uke")
    assert b.recording.takes_dir == "/Volumes/Audio/Recordings/Second Uke"
    (tmp_path / store.SETTINGS_FILE).write_text("not = [valid")
    assert store.load_settings().recordings_dir == store.DEFAULT_RECORDINGS_DIR


def test_ensure_folder_never_fakes_a_disconnected_disk(tmp_path):
    from vinst.ui.finder import ensure_folder, nearest_existing

    with pytest.raises(OSError, match="isn't connected"):
        ensure_folder(Path("/Volumes/NoSuchDisk-vinst-test/Recordings"))
    target = tmp_path / "a" / "b"
    ensure_folder(target)
    assert target.is_dir()
    assert nearest_existing(tmp_path / "missing" / "deeper") == tmp_path
