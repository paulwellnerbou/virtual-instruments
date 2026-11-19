import numpy as np
import pytest
from conftest import piezo_and_mic
import soundfile as sf

from vinst import defaults, review
from vinst.slicing import detect_pitch, process
from vinst.spec import InstrumentType
from vinst.synth import Mistakes, write_takes


@pytest.mark.parametrize("midi", [55, 69, 81])
@pytest.mark.parametrize("detune", [-30, -7, 0, 12])
def test_pitch_is_accurate_on_decaying_tones(midi, detune):
    sr = 48000
    t = np.arange(int(1.2 * sr)) / sr
    f = 440 * 2 ** ((midi + detune / 100 - 69) / 12)
    y = sum((1 / k) * np.exp(-(1 + 0.5 * k) * t) * np.sin(2 * np.pi * f * k * t) for k in range(1, 10))
    assert detect_pitch(y, sr, midi) == pytest.approx(midi + detune / 100, abs=0.01)


@pytest.fixture(scope="module")
def sliced(tmp_path_factory):
    folder = tmp_path_factory.mktemp("takes")
    inst = piezo_and_mic(defaults.new_instrument("Mini", InstrumentType.PLUCKED))
    for s in inst.range.strings:
        s.frets = 0  # four notes: G2 C3 E3 A3
    inst.dynamics.round_robins = 2
    inst.extras = inst.extras[:1]
    inst.recording.ring_seconds = 1.0
    inst.recording.takes_dir = str(folder)
    # take 1 is the noise floor, then each note's p, mf, f lanes (2..13), then the extra
    write_takes(inst, folder, ring=1.0, mistakes=Mistakes(wrong_note_take=3, missing_hit_take=4, clipped_take=5))
    return inst, process(inst)


def test_takes_are_cut_into_expected_hits(sliced):
    inst, m = sliced
    by_base = {t.base: t for t in m.takes}
    assert by_base["noisefloor"].status == "noise_floor"
    assert by_base["055_G-0_p"].status == "ok"
    assert [(h.id, h.layer) for h in by_base["055_G-0_p"].hits] == [("055_G-0_p_01", "p"), ("055_G-0_p_02", "p")]
    assert [h.layer for h in by_base["060_C-0_f"].hits] == ["f", "f"]
    assert by_base["x_release"].status == "ok" and len(by_base["x_release"].hits) == 12
    assert m.noise_floor_db == pytest.approx(-70, abs=1)


def test_mistakes_are_flagged(sliced):
    _, m = sliced
    by_number = {t.number: t for t in m.takes}
    assert "wrong_note" in by_number[3].hits[1].flags
    # A missing hit: the lane just has one variation fewer.
    assert by_number[4].status == "ok" and len(by_number[4].hits) == 1 and by_number[4].info == "1 hits, 2 planned"
    assert "clipped" in by_number[5].hits[-1].flags


def test_clean_hits_are_in_tune_and_layers_ordered(sliced):
    _, m = sliced
    soft = next(t for t in m.takes if t.base == "055_G-0_p")
    loud = next(t for t in m.takes if t.base == "056_G-1_f" or t.base == "060_C-0_f")
    assert all(not h.flags for h in soft.hits)
    assert all(abs(h.cents) < 15 for h in soft.hits)
    c3 = [t for t in m.takes if t.note == "060_C-0"]
    rms = {t.lane: t.hits[0].rms_db for t in c3}
    assert rms["f"] - rms["p"] > 10 and loud.hits


def test_signals_are_cut_identically_and_written(sliced):
    inst, m = sliced
    hit = m.takes[1].hits[0]
    folder = review.samples_dir(inst)
    lengths = {sf.info(folder / name).frames for name in hit.files.values()}
    assert lengths == {hit.end - hit.start}
    assert set(hit.files) == {"piezo", "mic"}


def test_rejections_survive_reslicing(sliced):
    inst, m = sliced
    m.takes[1].hits[1].rejected = True
    again = process(inst, previous=m)
    assert again.takes[1].hits[1].rejected
    assert sum(h.rejected for t in again.takes for h in t.hits) == 1


def test_missing_takes_are_reported(tmp_path):
    inst = defaults.new_instrument("Empty", InstrumentType.PLUCKED)
    inst.recording.takes_dir = str(tmp_path)
    m = process(inst)
    assert {t.status for t in m.takes} == {"missing"}


def test_rejection_is_dropped_when_take_is_rerecorded(sliced, tmp_path):
    inst, m = sliced
    take = m.takes[1]
    take.hits[0].rejected = True
    again = process(inst, previous=m)
    assert again.takes[1].hits[0].rejected
    # Re-record: same file name, new audio.
    folder = tmp_path / "rerecorded"
    write_takes(inst, folder, ring=1.0, seed=7, mistakes=Mistakes())
    rerecorded = inst.model_copy(deep=True)
    rerecorded.recording.takes_dir = str(folder)
    rerecorded.recording.samples_dir = str(review.samples_dir(inst))
    fresh = process(rerecorded, previous=again)
    assert not any(h.rejected for t in fresh.takes for h in t.hits)


def test_invalid_layer_names_refuse_slicing(sliced):
    inst, _ = sliced
    bad = inst.model_copy(deep=True)
    bad.dynamics.layers = ["p", "f", "F"]
    assert any("Duplicate lane name" in p for p in bad.problems())
    bad.dynamics.layers = ["p", "soft/hard"]
    assert any("letters, digits" in p for p in bad.problems())
    with pytest.raises(ValueError, match="Fix these first"):
        process(bad)


def test_cut_off_only_when_still_ringing(tmp_path):
    from vinst.synth import pluck

    inst = defaults.new_instrument("Cut", InstrumentType.PITCHED_PERCUSSION)
    inst.range.low = inst.range.high = 60
    inst.dynamics.layers, inst.dynamics.round_robins = ["normal"], 2
    inst.recording.noise_floor_seconds = 0
    inst.recording.takes_dir = str(tmp_path)
    sr = inst.recording.sample_rate
    rng = np.random.default_rng(0)
    long_note = pluck(261.6, 0.5, 3.0, sr, rng)
    # Second hit starts 0.5 s after the first while it still rings loudly.
    audio = np.concatenate([np.zeros(sr // 2), long_note[: sr // 2], long_note, np.zeros(sr)])
    audio += rng.normal(0, 1e-4, audio.size)
    sf.write(tmp_path / "060_normal_mic.wav", audio, sr, subtype="PCM_24")
    take = process(inst).takes[0]
    assert [("cut_off" in h.flags) for h in take.hits] == [True, False]


def test_stale_files_from_previous_run_are_removed(sliced):
    inst, m = sliced
    folder = review.samples_dir(inst)
    mf = next(t for t in m.takes if t.base == "055_G-0_mf")
    old_file = folder / mf.hits[0].files["piezo"]
    assert old_file.exists()
    changed = inst.model_copy(deep=True)
    changed.dynamics.layers = ["p", "f"]  # the mf lane is gone: its samples are no longer produced
    again = process(changed, previous=m)
    assert not old_file.exists()
    assert (folder / next(t for t in again.takes if t.base == "055_G-0_p").hits[0].files["piezo"]).exists()
    unrelated = folder / "my_own_file.wav"
    unrelated.write_bytes(b"")
    process(inst, previous=again)
    assert unrelated.exists()


def test_denoise_lowers_noise_and_keeps_the_note():
    from vinst import denoise
    from vinst.synth import pluck

    sr = 48000
    rng = np.random.default_rng(3)
    noise = lambda n: rng.normal(0, 0.003, n)  # noqa: E731
    note = np.concatenate([pluck(220, 0.5, 1.0, sr, rng), np.zeros(sr)])
    noisy = note + noise(note.size)
    clean = denoise.reduce(noisy, denoise.noise_profile(noise(sr * 2)), reduction_db=20)
    rms = lambda x: np.sqrt(np.mean(x ** 2))  # noqa: E731
    tail = slice(int(1.3 * sr), None)  # note has ended: only noise left
    assert rms(clean[tail]) < rms(noisy[tail]) / 4
    head = slice(0, int(0.3 * sr))
    assert abs(rms(clean[head]) / rms(note[head]) - 1) < 0.1


def test_denoise_is_opt_in_and_needs_the_noise_floor(sliced, tmp_path):
    inst, m = sliced
    assert not m.denoised
    on = inst.model_copy(deep=True)
    on.processing.denoise = True
    on.recording.samples_dir = str(tmp_path / "denoised")
    assert process(on).denoised
    on.recording.takes_dir = str(tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="noisefloor_piezo.wav is missing"):
        process(on)
