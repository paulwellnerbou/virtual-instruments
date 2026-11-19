import numpy as np
import pytest
import soundfile as sf
from lxml import etree

from vinst import bundle, defaults, loops, review, store
from vinst.preset import balance_corrections
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
    inst.dynamics.layers, inst.dynamics.round_robins = ["mp", "f"], 1
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
    take = sustained.recording.takes_dir + "/061_mic.wav"
    first_hit_region = (int(0.9 * 48000), int(1.6 * 48000))  # inside the first hit (starts at 0.5 s)
    from pathlib import Path
    loops.write_wav_loops(Path(take), [first_hit_region], 48000)
    manifest = process(sustained)
    t = next(t for t in manifest.takes if t.base == "061")
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


def _hit(rms: float) -> Hit:
    return Hit(id="x", index=0, start=0, end=1, files={}, rms_db=rms, peak_db={})


def test_balance_only_corrects_notes_that_stick_out():
    levels = {60: -20, 61: -20.5, 62: -21, 63: -14, 64: -22, 65: -22.5, 66: -23}
    notes = {r: {"mf": [_hit(v)], "f": [_hit(v + 10)]} for r, v in levels.items()}
    corr = balance_corrections(notes, ["mf", "f"], max_db=6)
    assert corr[(63, "mf")] == pytest.approx(-6.0)  # 7 dB louder than its neighbours, clamped to 6
    assert corr[(63, "f")] == pytest.approx(-6.0)
    assert all(abs(c) < 1 for (r, _), c in corr.items() if r != 63)
    assert balance_corrections(notes, ["mf"], max_db=3)[(63, "mf")] == -3.0


def test_balance_writes_sample_volumes(sustained):
    manifest = process(sustained)
    review.save(manifest, store.manifest_path(sustained.meta.slug))
    sustained.preset.balance = True
    doc = etree.parse(str(bundle.build(sustained).preset_file))
    # Three synthetic notes at similar levels: only small corrections, if any, and never above the limit.
    vols = [float(s.get("volume")[:-2]) for s in doc.iter("sample") if s.get("volume")]
    assert all(abs(v) <= sustained.preset.balance_max_db for v in vols)


def test_sustained_notes_are_balanced_by_their_held_level():
    # Note 63 starts quieter (slow attack) but holds at the same level as its neighbours.
    notes = {r: {"mf": [Hit(id="x", index=0, start=0, end=1, files={}, peak_db={},
                            rms_db=-26.0 if r == 63 else -20.0, body_db=-18.0)]} for r in range(60, 67)}
    assert balance_corrections(notes, ["mf"], 6, sustained=True)[(63, "mf")] == 0
    assert balance_corrections(notes, ["mf"], 6, sustained=False)[(63, "mf")] == 6.0


def test_marker_problems_are_reported(sustained):
    from pathlib import Path

    first = process(sustained).takes[1]  # note 060: hits around 0.5–2.5 s and 3.5–5.5 s
    hit = first.hits[0]
    in_fade = (hit.start + 30_000, hit.end - 100)  # ends inside the last 50 ms
    across = (hit.end - 5_000, first.hits[1].start + 5_000)  # spans the cut between two hits
    loops.write_wav_loops(Path(sustained.recording.takes_dir) / "060_mic.wav", [in_fade, across], 48000)
    again = process(sustained).takes[1]
    assert "loop_in_fade" in again.hits[0].flags
    assert "doesn't lie inside one hit" in again.message


def test_shortened_crossfades_are_reported(sustained):
    review.save(process(sustained), store.manifest_path(sustained.meta.slug))
    sustained.processing.loop_crossfade_ms = 500  # longer than the loops' start positions allow
    result = bundle.build(sustained)
    assert any("shorter one" in w for w in result.warnings)
