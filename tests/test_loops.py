import numpy as np
import pytest
import soundfile as sf
from lxml import etree

from vinst import bundle, defaults, loops, review, store
from vinst.preset import BALANCE_MAX_DB, balance_corrections, keyboard_line
from vinst.preset import map_samples as preset_map
from vinst.review import Hit
from vinst.slicing import process
from vinst.spec import InstrumentType
from vinst.synth import Mistakes, write_takes


def test_wav_loop_markers_roundtrip(tmp_path):
    path = tmp_path / "x.wav"
    sf.write(path, np.zeros(48000), 48000, subtype="PCM_24")
    loops.write_wav_loops(path, [(1000, 5000), (20000, 30000)], 48000)
    assert loops.read_wav_loops(path) == [(1000, 5000), (20000, 30000)]
    assert sf.info(path).frames == 48000  # still a valid WAV
    assert loops.read_wav_loops(tmp_path / "missing.wav") == []


def test_markers_are_mapped_into_the_hit_they_fall_into():
    markers = [(500, 900), (12_000, 18_000)]
    assert loops.markers_for_hit(markers, 10_000, 20_000) == loops.Loop(2000, 8000, 1.0, "marker")
    assert loops.markers_for_hit(markers, 20_000, 30_000) is None


@pytest.fixture
def sustained(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VINST_BUILD", str(tmp_path / "build"))
    monkeypatch.setenv("VINST_DS_LIBRARY", str(tmp_path / "library"))
    inst = defaults.new_instrument("Pad", InstrumentType.SUSTAINED)
    assert inst.processing.loops
    inst.range.low, inst.range.high, inst.range.density = 60, 62, 1
    inst.dynamics.layers, inst.dynamics.round_robins = ["mp", "f"], 2
    inst.recording.ring_seconds = 2.0
    inst.recording.takes_dir = str(tmp_path / "takes")
    store.create(inst)
    write_takes(inst, tmp_path / "takes", ring=2.0, mistakes=Mistakes())
    return inst


def test_sustained_notes_get_good_loops_in_the_preset(sustained):
    manifest = process(sustained)
    hits = [h for t in manifest.takes if t.kind == "note" for h in t.hits]
    assert hits and all(h.loop_source == "auto" and h.loop_score > 0.95 for h in hits)
    assert all((h.loop_end - h.loop_start) / 48000 >= 0.5 for h in hits)
    review.save(manifest, store.manifest_path(sustained.meta.slug))
    result = bundle.build(sustained)
    assert result.report.ok, result.report.errors
    for s in etree.parse(str(result.preset_file)).iter("sample"):
        if s.get("loopEnabled") is None:
            continue  # extras don't loop
        frames = sf.info(result.bundle / s.get("path")).frames
        start, end, xf = int(s.get("loopStart")), int(s.get("loopEnd")), int(s.get("loopCrossfade"))
        assert xf <= start < end < frames
    assert any("Loops:" in line for line in result.info)


def test_loop_markers_in_takes_win(sustained):
    take = sustained.recording.takes_dir + "/061_mp_mic.wav"
    first_hit_region = (int(0.9 * 48000), int(1.6 * 48000))  # inside the first hit (starts at 0.5 s)
    from pathlib import Path
    loops.write_wav_loops(Path(take), [first_hit_region], 48000)
    manifest = process(sustained)
    t = next(t for t in manifest.takes if t.base == "061_mp")
    first = t.hits[0]
    assert first.loop_source == "marker"
    assert (first.loop_start + first.start, first.loop_end + first.start) == first_hit_region
    assert t.hits[1].loop_source == "auto"


def test_loop_can_be_turned_off_per_hit_and_survives_reslicing(sustained):
    manifest = process(sustained)
    hit = manifest.takes[1].hits[0]
    hit.loop_off = True
    again = process(sustained, previous=manifest)
    assert again.takes[1].hits[0].loop_off
    review.save(again, store.manifest_path(sustained.meta.slug))
    result = bundle.build(sustained)
    doc = etree.parse(str(result.preset_file))
    off = [s for s in doc.iter("sample") if s.get("path").endswith(hit.files["mic"])]
    assert off and all(s.get("loopEnabled") is None for s in off)


def test_turning_loops_on_after_slicing_asks_to_slice_again(sustained):
    sustained.processing.loops = False
    review.save(process(sustained), store.manifest_path(sustained.meta.slug))
    sustained.processing.loops = True
    with pytest.raises(ValueError, match="loop setting"):
        bundle.build(sustained)


def _hit(rms: float, id: str = "x") -> Hit:
    return Hit(id=id, index=0, start=0, end=1, files={}, rms_db=rms, peak_db={})


def test_at_zero_tolerance_every_sample_is_brought_onto_the_line():
    levels = {60: [-20, -30], 61: [-21], 62: [-26, -22], 63: [-14]}
    notes = {r: {"mf": [_hit(v, f"mf{r}_{i}") for i, v in enumerate(vs)],
                 "f": [_hit(v + 10, f"f{r}_{i}") for i, v in enumerate(vs)]} for r, vs in levels.items()}
    corr = balance_corrections(notes, ["mf", "f"], tolerance_db=0)
    slope, at_zero = keyboard_line({60: -25.0, 61: -21.0, 62: -24.0, 63: -14.0})  # each note's median
    line = {r: at_zero + slope * r for r in levels}
    assert corr["mf60_1"] == pytest.approx(line[60] + 30, abs=0.01)  # the quiet hit of a note, not just the note
    assert corr["mf63_0"] == pytest.approx(line[63] + 14, abs=0.01)
    assert corr["f60_1"] == pytest.approx(corr["mf60_1"])  # each velocity to its own line: f stays louder


def test_differences_within_the_tolerance_stay_and_larger_ones_are_brought_back_to_it():
    levels = {60: -20, 61: -21.5, 62: -19, 63: -32, 64: -20.5, 65: -12, 66: -20}
    notes = {r: {"mf": [_hit(v, f"h{r}")]} for r, v in levels.items()}
    corr = balance_corrections(notes, ["mf"], tolerance_db=3)
    slope, at_zero = keyboard_line(dict(levels))
    assert abs(slope) < 0.5  # two notes far off don't tilt it
    assert corr["h61"] == corr["h62"] == corr["h64"] == 0  # natural variation stays
    assert corr["h63"] == pytest.approx(at_zero + slope * 63 - 3 + 32, abs=0.01)  # to the band's edge
    assert corr["h65"] == pytest.approx(at_zero + slope * 65 + 3 + 12, abs=0.01)


def test_a_register_trend_is_kept_and_only_notes_off_it_move():
    # 1 dB softer per note up the keyboard, with one note 8 dB off.
    levels = {60 + i: -10.0 - i for i in range(20)}
    levels[70] -= 8
    notes = {r: {"mf": [_hit(v, f"h{r}")]} for r, v in levels.items()}
    corr = balance_corrections(notes, ["mf"], tolerance_db=2)
    assert corr["h70"] == pytest.approx(6.0)  # back to 2 dB off the trend
    assert all(c == 0 for h, c in corr.items() if h != "h70")


def test_a_quieter_string_is_brought_up_to_the_others():
    # Four strings of four notes; the third is 6 dB quieter, as a piezo pickup can make it.
    levels = {60 + i: -20.0 - (6.0 if 8 <= i < 12 else 0.0) for i in range(16)}
    notes = {r: {"mf": [_hit(v, f"h{r}")]} for r, v in levels.items()}
    corr = balance_corrections(notes, ["mf"], tolerance_db=1.5)
    assert all(corr[f"h{r}"] == pytest.approx(4.5) for r in range(68, 72))  # a neighbours' median would follow it
    assert all(corr[f"h{r}"] == 0 for r in levels if not 68 <= r < 72)


def test_corrections_are_capped():
    notes = {r: {"mf": [_hit(-60.0 if r == 63 else -20.0, f"h{r}")]} for r in range(60, 67)}
    assert balance_corrections(notes, ["mf"], tolerance_db=0)["h63"] == BALANCE_MAX_DB
    # The cap is on the correction, not on how far off the sample is: 25 dB off, 12 allowed, needs 13.
    notes[63] ={"mf": [_hit(-45.0, "h63")]}
    assert balance_corrections(notes, ["mf"], tolerance_db=12)["h63"] == 13


def test_balance_writes_sample_volumes(sustained):
    manifest = process(sustained)
    review.save(manifest, store.manifest_path(sustained.meta.slug))
    sustained.preset.balance = True
    mapping = preset_map(sustained, manifest)
    assert mapping.notes and all(abs(e.volume_db) <= BALANCE_MAX_DB for e in mapping.notes)
    assert any("Loudness evened out" in i for i in mapping.info)


def test_sustained_notes_are_evened_out_by_their_held_level():
    # Note 63 starts quieter (slow attack) but holds at the same level as the others.
    notes = {r: {"mf": [Hit(id=f"h{r}", index=0, start=0, end=1, files={}, peak_db={},
                            rms_db=-26.0 if r == 63 else -20.0, body_db=-18.0)]} for r in range(60, 67)}
    assert balance_corrections(notes, ["mf"], 0, sustained=True)["h63"] == 0
    assert balance_corrections(notes, ["mf"], 0, sustained=False)["h63"] == 6.0


def test_marker_problems_are_reported(sustained):
    from pathlib import Path

    first = process(sustained).takes[1]  # note 060, lane mp: hits around 0.5–2.5 s and 3.5–5.5 s
    hit = first.hits[0]
    in_fade = (hit.start + 30_000, hit.end - 100)  # ends inside the last 50 ms
    across = (hit.end - 5_000, first.hits[1].start + 5_000)  # spans the cut between two hits
    loops.write_wav_loops(Path(sustained.recording.takes_dir) / "060_mp_mic.wav", [in_fade, across], 48000)
    again = process(sustained).takes[1]
    assert "loop_in_fade" in again.hits[0].flags
    assert "doesn't lie inside one hit" in again.message


def test_shortened_crossfades_are_reported(sustained):
    review.save(process(sustained), store.manifest_path(sustained.meta.slug))
    sustained.processing.loop_crossfade_ms = 500  # longer than the loops' start positions allow
    result = bundle.build(sustained)
    assert any("shorter one" in w for w in result.warnings)


def test_two_notes_are_evened_out_towards_each_other():
    # Two can't tell a trend from a mis-hit: a line through both would leave the mis-hit as it is.
    notes = {60: {"mf": [_hit(-20.0, "h60")]}, 62: {"mf": [_hit(-30.0, "h62")]}}
    corr = balance_corrections(notes, ["mf"], tolerance_db=1.5)
    assert corr["h60"] == pytest.approx(-3.5) and corr["h62"] == pytest.approx(3.5)
