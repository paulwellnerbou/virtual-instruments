import numpy as np
import pytest
import soundfile as sf
from lxml import etree

from vinst import bundle, defaults, importer, review, store
from vinst.slicing import process
from vinst.spec import InstrumentType, Signal
from vinst.synth import pluck

SR = 48000


def test_names_give_both_octave_readings_and_numbers():
    info = importer.parse_name("Metallophone_50_D3_1", ["normal"], ["mic"])
    assert info.names == [(62, 50)]  # D3 is 62 if middle C is C3, 50 if it is C4
    assert info.numbers == [50, 1]
    assert importer.candidates(info, "c3") == [62, 50, 1]
    assert importer.candidates(info, "c4")[0] == 50
    info = importer.parse_name("Uke_mf_rr2_piezo", ["p", "mf", "f"], ["piezo", "mic"])
    assert (info.layer, info.rr, info.signal) == ("mf", 2, "piezo")
    assert importer.parse_name("Pad Eb2 v3", ["x"], ["mic"]).velocity_rank == 3
    assert importer.parse_name("E#3", ["x"], ["mic"]).names == []  # not a note name


def write(path, midi, amp=0.5, silence=0.0):
    rng = np.random.default_rng(midi)
    audio = np.concatenate([np.zeros(int(silence * SR)),
                            pluck(440 * 2 ** ((midi - 69) / 12), amp, 1.2, SR, rng)])
    sf.write(path, audio, SR, subtype="PCM_24")


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VINST_BUILD", str(tmp_path / "build"))
    lib = tmp_path / "lib"
    (lib / "sub").mkdir(parents=True)
    write(lib / "Harp C4.wav", 60)  # named the scientific way: C4 = 60
    write(lib / "Harp E4 soft.wav", 64, amp=0.15)
    write(lib / "Harp E4 loud.wav", 64, amp=0.8)
    write(lib / "sub" / "067.wav", 67, silence=0.4)  # MIDI number, starts with silence
    write(lib / "unnamed take.wav", 72)  # no name: pitch decides
    sf.write(lib / "noise.wav", np.random.default_rng(0).normal(0, 0.1, SR), SR)
    inst = defaults.new_instrument("Harp", InstrumentType.PITCHED_PERCUSSION)
    inst.recording.source = "samples"
    inst.recording.import_dir = str(lib)
    inst.dynamics.layers = ["p", "f"]
    store.create(inst)
    return inst, lib


def test_import_finds_notes_layers_and_unassigned_files(library):
    inst, _ = library
    m = process(inst)
    notes = {t.midi: t for t in m.takes if t.midi is not None}
    assert sorted(notes) == [60, 64, 67, 72]
    assert all("name_mismatch" not in h.flags for t in notes.values() for h in t.hits)
    e4 = {h.layer: h for h in notes[64].hits}
    assert e4["f"].rms_db > e4["p"].rms_db  # quieter file went to the soft layer
    assert notes[67].hits[0].cents is not None and abs(notes[67].hits[0].cents) < 15
    unassigned = next(t for t in m.takes if t.base == "unassigned")
    assert unassigned.status == "error" and "noise" in unassigned.message
    out = review.samples_dir(inst)
    assert (out / notes[60].hits[0].files["mic"]).is_file()


def test_imported_instrument_builds_and_detects_changes(library):
    inst, lib = library
    review.save(process(inst), store.manifest_path(inst.meta.slug))
    result = bundle.build(inst)
    assert result.report.ok, result.report.errors
    roots = {s.get("rootNote") for s in etree.parse(str(result.preset_file)).iter("sample")}
    assert roots == {"60", "64", "67", "72"}
    write(lib / "Harp G4 extra.wav", 67)
    with pytest.raises(ValueError, match="Slice again"):
        bundle.build(inst)


def test_signals_are_matched_by_name_and_incomplete_pairs_left_out(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    lib = tmp_path / "lib"
    lib.mkdir()
    write(lib / "C3_piezo.wav", 60)
    write(lib / "C3_mic.wav", 60, amp=0.3)
    write(lib / "D3_piezo.wav", 62)  # its mic file is missing
    inst = defaults.new_instrument("Two", InstrumentType.PLUCKED)
    inst.signals = [Signal(id="piezo", name="Piezo"), Signal(id="mic", name="Mic")]
    inst.extras = []
    inst.recording.source = "samples"
    inst.recording.import_dir = str(lib)
    inst.dynamics.layers = ["normal"]
    store.create(inst)
    m = process(inst)
    notes = {t.midi: t for t in m.takes if t.midi is not None}
    assert set(notes[60].hits[0].files) == {"piezo", "mic"}
    d3 = notes[62].hits[0]
    assert "incomplete" in d3.flags and d3.rejected


def test_rejections_survive_reimport(library):
    inst, _ = library
    first = process(inst)
    target = next(t for t in first.takes if t.midi == 64)
    target.hits[0].rejected = True
    again = process(inst, previous=first)
    assert next(t for t in again.takes if t.midi == 64).hits[0].rejected


def test_copies_folder_inside_the_import_folder_is_refused(library):
    inst, lib = library
    inst.recording.samples_dir = str(lib / "copies")
    with pytest.raises(ValueError, match="folder you import from"):
        process(inst)
    inst.recording.samples_dir = str(lib)
    with pytest.raises(ValueError, match="folder you import from"):
        process(inst)


def test_each_file_is_read_once(library, monkeypatch):
    inst, _ = library
    reads = []
    real = importer.load
    monkeypatch.setattr(importer, "load", lambda path: reads.append(path) or real(path))
    process(inst)
    assert len(reads) == len(set(reads)) == 6  # five notes' files plus the noise file


def test_import_summary_ignores_sliced_takes(library):
    from vinst.ui.app import imported_notes

    inst, _ = library
    manifest = process(inst)
    review.save(manifest, store.manifest_path(inst.meta.slug))
    assert sorted(imported_notes(inst)) == [60, 64, 67, 72]
    manifest.source = "takes"  # as left behind by slicing before switching to import
    review.save(manifest, store.manifest_path(inst.meta.slug))
    assert imported_notes(inst) is None


def test_mixed_sample_rates_keep_their_own_rate(tmp_path, monkeypatch):
    from vinst import preset

    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    lib = tmp_path / "lib"
    lib.mkdir()
    for name, sr in (("C3 a", 44100), ("C3 b", 48000)):
        rng = np.random.default_rng(sr)
        sf.write(lib / f"{name}.wav", pluck(261.63, 0.5, 1.2, sr, rng), sr, subtype="PCM_24")
    inst = defaults.new_instrument("Rates", InstrumentType.PITCHED_PERCUSSION)
    inst.recording.source = "samples"
    inst.recording.import_dir = str(lib)
    inst.dynamics.layers = ["normal"]
    store.create(inst)
    m = process(inst)
    note = next(t for t in m.takes if t.midi == 60)
    assert sorted(h.sample_rate for h in note.hits) == [44100, 48000]
    entries = preset.map_samples(inst, m).notes
    assert sorted({e.sample_rate for e in entries}) == [44100, 48000]
