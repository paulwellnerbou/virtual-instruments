from pathlib import Path

import pytest
from conftest import piezo_and_mic

from vinst import defaults, notes, store
from vinst.plan import lane_bounds, natural_string, plan_takes, sampled_notes, summarize
from vinst.spec import Instrument, InstrumentType, NoteLanes


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
    # One take per note and velocity lane, each with the lane's variations.
    p, mf, f = takes[1:4]
    assert [t.lane for t in (p, mf, f)] == ["p", "mf", "f"] and {t.note_base for t in (p, mf, f)} == {"055_G-0"}
    assert p.base_name == "055_G-0_p" and p.files == ["055_G-0_p_piezo.wav", "055_G-0_p_mic.wav"]
    assert p.hits == 4 and p.title.endswith("· p")
    s = summarize(inst)
    assert s.sampled == 27 and s.takes == 1 + 27 * 3 + 3 and s.hits == 27 * 12 + 12 + 10 + 10


def test_a_note_can_have_its_own_lanes():
    inst = uke()
    inst.preset.velocity_splits = [30, 100]
    assert lane_bounds(inst, "055_G-0") == [("p", 1, 29), ("mf", 30, 99), ("f", 100, 127)]
    inst.note_lanes["055_G-0"] = NoteLanes(layers=["p", "f", "ff"], splits=[60, 120])
    assert lane_bounds(inst, "055_G-0") == [("p", 1, 59), ("f", 60, 119), ("ff", 120, 127)]
    assert [t.lane for t in plan_takes(inst) if t.note_base == "055_G-0"] == ["p", "f", "ff"]
    assert lane_bounds(inst, "056_G-1")[1] == ("mf", 30, 99)  # other notes keep the defaults


def test_unpitched_sounds():
    inst = defaults.new_instrument("Knocks", InstrumentType.UNPITCHED)
    assert [t.base_name for t in plan_takes(inst) if t.kind == "note"] == ["060_Hit_soft", "060_Hit_hard"]


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


def test_sample_size_and_recorded_files(tmp_path):
    inst = uke()
    # 324 note hits × 6 s + 32 extra hits × 2 s, 48 kHz, 24-bit, 2 signals
    expected = (324 * 6 + 32 * 2) * 48000 * 3 * 2 / 1e6
    s = summarize(inst)
    assert abs(s.sample_mb - expected) < 0.01
    assert s.recorded_files == 0
    inst.recording.takes_dir = str(tmp_path)
    (tmp_path / "055_G-0_p_piezo.wav").touch()
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


def test_a_deleted_instrument_goes_to_the_trash_and_its_recordings_stay(tmp_path, monkeypatch):
    import sys

    from vinst import defaults, store
    from vinst.spec import InstrumentType
    from vinst.ui.finder import trash

    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))  # its Trash, not the real one
    (tmp_path / "user" / ".Trash").mkdir(parents=True)
    takes = tmp_path / "takes"
    takes.mkdir()
    (takes / "060_C-0_p_pickup.wav").write_bytes(b"audio")
    inst = defaults.new_instrument("Uke", InstrumentType.PLUCKED)
    inst.recording.takes_dir = str(takes)
    inst = store.create(inst)
    went = trash(store.instrument_dir(inst.meta.slug))
    assert not store.exists(inst.meta.slug) and (takes / "060_C-0_p_pickup.wav").exists()
    if sys.platform == "darwin":
        assert went == tmp_path / "user" / ".Trash" / inst.meta.slug and (went / store.SPEC_FILE).exists()


def test_nothing_is_written_for_a_deleted_instrument(tmp_path, monkeypatch):
    import shutil

    from vinst import defaults, store, takes_meta
    from vinst.spec import InstrumentType

    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    inst = store.create(defaults.new_instrument("Uke", InstrumentType.PLUCKED))
    take = tmp_path / "take.wav"
    take.write_bytes(b"audio")
    shutil.rmtree(store.instrument_dir(inst.meta.slug))
    takes_meta.save(inst.meta.slug, take, [0.5])
    assert not store.instrument_dir(inst.meta.slug).exists()


def test_a_recording_edited_from_the_app_keeps_its_grid_while_its_length_does(tmp_path, monkeypatch):
    import time

    import numpy as np
    import soundfile as sf

    from vinst import defaults, store, takes_meta
    from vinst.spec import InstrumentType

    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    inst = defaults.new_instrument("Uke", InstrumentType.PLUCKED)
    store.save(inst)
    take = tmp_path / "take.wav"
    sf.write(str(take), np.zeros(48000), 48000)
    takes_meta.save(inst.meta.slug, take, [0.5])
    takes_meta.save_cuts(inst.meta.slug, take, [(100, 2000)])

    takes_meta.editing(inst.meta.slug, take)
    time.sleep(0.01)
    sf.write(str(take), np.full(48000, 0.1), 48000)  # a click taken out: same length
    assert takes_meta.hits_at(inst.meta.slug, take) == [0.5]
    assert takes_meta.cuts(inst.meta.slug, take) == [(100, 2000)]

    sf.write(str(take), np.zeros(24000), 48000)  # cut shorter: the hits may have moved
    assert takes_meta.hits_at(inst.meta.slug, take) is None


def test_a_recording_replaced_without_the_app_loses_its_grid(tmp_path, monkeypatch):
    import time

    import numpy as np
    import soundfile as sf

    from vinst import defaults, store, takes_meta
    from vinst.spec import InstrumentType

    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    inst = defaults.new_instrument("Uke", InstrumentType.PLUCKED)
    store.save(inst)
    take = tmp_path / "take.wav"
    sf.write(str(take), np.zeros(48000), 48000)
    takes_meta.save(inst.meta.slug, take, [0.5])
    time.sleep(0.01)
    sf.write(str(take), np.full(48000, 0.1), 48000)
    assert takes_meta.hits_at(inst.meta.slug, take) is None


def test_a_hits_tooltip_says_what_it_is():
    from vinst.review import Hit, TakeResult
    from vinst.ui.worksheet import hit_info

    take = TakeResult(number=1, base="062_C-2_forte", title="D3 (62) · forte", kind="note", status="ok",
                      signals=["pickup"], sample_rate=48000, expected_hits=1)
    hit = Hit(id="h", index=0, start=48000, end=96000, files={"pickup": "062_x_01_pickup.wav"},
              midi_detected=63.02, cents=102.0, rms_db=-20.0, peak_db={"pickup": -6.0}, flags=["wrong_note"])
    text = hit_info(take, hit)
    assert text.startswith("062_C-2_forte_pickup.wav · 1.00–2.00 s")
    assert "Pitch D#3 +2 cents" in text  # off the note it names, not the one expected
    assert "more than 50 cents away" in text
    assert hit_info(take, hit, imported=True).startswith("062_x_01_pickup.wav\n")  # its own file


def test_signals_recorded_together_share_a_pass():
    """Older specs said "together" or "separate"; a pass id groups them, keeping their takes' keys."""
    from vinst.plan import recording_passes, take_key
    from vinst.spec import Instrument, Signal

    old = [Signal(id="intmic", name="Internal mic", recorded="separate"),
           Signal(id="piezo", name="Piezo", recorded="together"), Signal(id="mic", name="Mic", recorded="together")]
    assert [s.pass_id for s in old] == ["intmic", "main", "main"]
    inst = defaults.new_instrument("Passes", InstrumentType.PLUCKED)
    inst.signals = old
    passes = recording_passes(inst)
    assert [(p.id, p.name) for p in passes] == [("main", "Piezo + Mic"), ("intmic", "Internal mic")]
    assert take_key("060_C-0_p", passes[1].id) == "060_C-0_p~intmic"
    # Two pairs, each recorded together, one pass after the other.
    inst.signals = [Signal(id="piezo", name="Piezo"), Signal(id="mic", name="Mic"),
                    Signal(id="di", name="DI", pass_id="di"), Signal(id="room", name="Room", pass_id="di")]
    assert [[s.id for s in p.signals] for p in recording_passes(inst)] == [["piezo", "mic"], ["di", "room"]]
    again = Instrument.from_toml(inst.to_toml())
    assert "recorded" not in inst.to_toml() and [s.pass_id for s in again.signals] == ["main", "main", "di", "di"]
