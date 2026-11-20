"""Clean-up EQ: its filters, and slicing the takes that use one again when it changes."""

import os
import warnings
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from conftest import piezo_and_mic
from test_rerecord import lane, result, take_audio, uke, write  # noqa: F401  (uke is a fixture)

from vinst import bundle, cleanup_eq, defaults, relocate, review, slicing, store
from vinst.slicing import eq_changed_takes, process, process_one
from vinst.spec import CleanupEq, EqFilter, InstrumentType

SR = 48000


def at(filters, hz):
    return float(cleanup_eq.response_db(filters, SR, np.array([hz]))[0])


def test_bands_do_what_they_say_filtered_both_ways():
    peak = [EqFilter(kind="peak", hz=1000, gain_db=-12, q=4)]
    assert at(peak, 1000) == pytest.approx(-12, abs=0.05)  # not -24: designed with half the gain
    assert at(peak, 100) == pytest.approx(0, abs=0.1) and at(peak, 10000) == pytest.approx(0, abs=0.1)
    low = [EqFilter(kind="low_shelf", hz=200, gain_db=-6, q=0.707)]
    assert at(low, 20) == pytest.approx(-6, abs=0.1) and at(low, 5000) == pytest.approx(0, abs=0.1)
    high = [EqFilter(kind="high_shelf", hz=5000, gain_db=4, q=0.707)]
    assert at(high, 20000) == pytest.approx(4, abs=0.2) and at(high, 100) == pytest.approx(0, abs=0.1)
    hp = [EqFilter(kind="highpass", hz=80, q=0.707)]
    assert at(hp, 80) == pytest.approx(-6, abs=0.1) and at(hp, 20) < -40 and at(hp, 1000) > -0.1
    lp = [EqFilter(kind="lowpass", hz=8000, q=0.707)]
    assert at(lp, 8000) == pytest.approx(-6, abs=0.1) and at(lp, 200) > -0.1
    assert at([EqFilter(kind="notch", hz=3200, q=10)], 3200) < -60


def test_hum_goes_with_its_harmonics_and_nothing_beside_it():
    hum = [EqFilter(kind="hum", hz=50, gain_db=-30, q=20, harmonics=6)]
    for k in range(1, 7):
        assert -32 < at(hum, 50 * k) <= -29.9  # the neighbouring notches add a little
    assert at(hum, 400) > -1 and at(hum, 1000) > -0.1  # above the last harmonic
    many = [EqFilter(kind="hum", hz=5000, gain_db=-30, q=20, harmonics=10)]
    assert len(cleanup_eq.sections(many, SR)) == 4  # none at or above half the sample rate


def test_hum_is_taken_out_and_the_note_stays_where_it_was():
    t = np.arange(3 * SR) / SR
    note = 0.5 * np.sin(2 * np.pi * 440 * t)
    out = cleanup_eq.apply(note + 0.05 * np.sin(2 * np.pi * 50 * t + 0.7), SR,
                           [EqFilter(kind="hum", hz=50, gain_db=-40, q=20, harmonics=4)])
    mid = slice(SR // 2, 5 * SR // 2)  # once the notches have rung in

    def amplitude(x, hz):
        return 2 * abs(np.mean(x[mid] * np.exp(-2j * np.pi * hz * t[mid])))

    assert 20 * np.log10(amplitude(out, 50) / 0.05) == pytest.approx(-40, abs=0.5)
    assert amplitude(out, 440) == pytest.approx(0.5, rel=0.02)
    # Zero phase: the note isn't shifted in time.
    lags = range(-5, 6)
    assert max(lags, key=lambda k: float(np.dot(out[mid], np.roll(note, k)[mid]))) == 0


def test_noise_profile_follows_the_eq():
    profile = np.ones(1025)
    shaped = cleanup_eq.shape_profile(profile, SR, [EqFilter(kind="notch", hz=1500, q=4)], 2048)
    assert shaped[64] < 1e-3 and shaped[640] == pytest.approx(1, abs=1e-3)  # bin 64 is 1500 Hz


def test_which_takes_and_signals_an_eq_is_for():
    inst = piezo_and_mic(defaults.new_instrument("Two", InstrumentType.PLUCKED))
    band = EqFilter(kind="hum", hz=50)
    inst.processing.cleanup_eqs = [CleanupEq(name="Hum", signals=["piezo"], takes=["a"], filters=[band]),
                                   CleanupEq(name="Empty", every_take=True)]  # no filters: changes nothing
    assert cleanup_eq.filters_for(inst, "a", "piezo") == [band]
    assert cleanup_eq.filters_for(inst, "a", "mic") == [] and cleanup_eq.filters_for(inst, "b", "piezo") == []
    assert '"piezo"' in cleanup_eq.take_setup(inst, "a", ["piezo", "mic"])
    assert cleanup_eq.take_setup(inst, "b", ["piezo", "mic"]) == ""
    assert cleanup_eq.describe(inst, "a", ["piezo", "mic"]) == "Hum (Piezo)"  # as the signal is called


def test_a_removed_signal_is_skipped_without_blocking_slicing():
    inst = piezo_and_mic(defaults.new_instrument("Two", InstrumentType.PLUCKED))
    band = EqFilter(kind="hum", hz=50)
    eq = CleanupEq(name="Hum", signals=["gone"], takes=["a"], filters=[band])
    inst.processing.cleanup_eqs = [eq]
    assert not any("Hum" in p for p in inst.problems())
    assert cleanup_eq.missing_signals(inst, eq) == ["gone"]
    assert cleanup_eq.filters_for(inst, "a", "piezo") == []  # not all signals: it was for another one
    eq.signals = ["gone", "mic"]
    assert cleanup_eq.filters_for(inst, "a", "mic") == [band] and cleanup_eq.filters_for(inst, "a", "piezo") == []


def hummy(hz, seconds=8, level=0.003):
    """A noise floor: hiss with, if `hz`, mains hum and its third harmonic well below it."""
    t = np.arange(seconds * SR) / SR
    noise = np.random.default_rng(3).normal(0, 0.01, len(t))
    return noise + (level * (np.sin(2 * np.pi * hz * t) + 0.5 * np.sin(2 * np.pi * 3 * hz * t)) if hz else 0)


def test_the_mains_frequency_is_found_in_a_noise_floor():
    assert cleanup_eq.mains_in(hummy(50), SR) == 50
    assert cleanup_eq.mains_in(hummy(60), SR) == 60
    assert cleanup_eq.mains_in(hummy(None), SR) is None
    assert cleanup_eq.mains_in(hummy(50)[: 3 * SR], SR) is None  # too short to tell
    assert cleanup_eq.mains_in(hummy(50, seconds=4), SR) == 50
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert cleanup_eq.mains_in(np.zeros(5 * SR), SR) is None  # digital silence


def test_hiss_alone_is_no_mains_hum():
    for seed in range(20):
        hiss = np.random.default_rng(seed).normal(0, 0.01, 4 * SR)
        assert cleanup_eq.mains_in(hiss, SR) is None


def test_a_mains_eq_is_for_the_signals_that_hum(tmp_path):
    inst = piezo_and_mic(defaults.new_instrument("Two", InstrumentType.PLUCKED))
    inst.recording.takes_dir = str(tmp_path)
    assert cleanup_eq.found_mains(inst) == {}  # no noise floor recorded
    sf.write(tmp_path / "noisefloor_piezo.wav", hummy(50), SR)
    sf.write(tmp_path / "noisefloor_mic.wav", hummy(None), SR)
    found = cleanup_eq.found_mains(inst)
    assert found == {"piezo": 50}
    eq = cleanup_eq.mains_eq(inst, 50, found)
    assert (eq.name, eq.signals, eq.every_take) == ("Mains hum", ["piezo"], True)
    assert eq.filters == [cleanup_eq.mains_filter(50)] and eq.filters[0].kind == "hum"
    inst.processing.cleanup_eqs = [eq.model_copy(update={"name": "MAINS HUM"})]  # names differ in more than case
    other = cleanup_eq.mains_eq(inst, 60, found)  # heard on neither: for all of them
    assert (other.name, other.signals, other.filters[0].hz) == ("Mains hum 2", [], 60)


def test_a_half_typed_band_is_no_filter_rather_than_nan():
    for bad in (EqFilter.model_construct(kind="peak", hz=1000.0, gain_db=-12.0, q=0.0, harmonics=1),
                EqFilter.model_construct(kind="highpass", hz=0.0, gain_db=0.0, q=0.707, harmonics=1)):
        assert len(cleanup_eq.sections([bad], SR)) == 0
        audio = np.random.default_rng(1).normal(0, 0.1, SR)
        assert np.array_equal(cleanup_eq.apply(audio, SR, [bad]), audio)


def test_bands_the_sample_rate_cant_hold_are_named():
    assert cleanup_eq.left_out(EqFilter(kind="lowpass", hz=22000), 44100).startswith("Above 21609 Hz")
    assert cleanup_eq.left_out(EqFilter(kind="lowpass", hz=16000), 44100) == ""
    assert cleanup_eq.left_out(EqFilter(kind="hum", hz=5000, harmonics=10), SR).startswith("Only 4 of the 10")
    assert cleanup_eq.left_out(EqFilter(kind="hum", hz=50, harmonics=10), SR) == ""


def test_picks_of_recordings_that_are_gone_are_forgotten():
    inst = defaults.new_instrument("One", InstrumentType.PLUCKED)
    inst.processing.cleanup_eqs = [CleanupEq(name="Hum", takes=["a", "b-more1"], filters=[EqFilter(kind="hum")])]
    assert cleanup_eq.forget_missing(inst, {"a", "c"})
    assert inst.processing.cleanup_eqs[0].takes == ["a"]
    assert not cleanup_eq.forget_missing(inst, {"a"})


def hum_db(path):
    audio, sr = sf.read(path)
    spec = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    k = int(round(50 * len(audio) / sr))
    return 20 * np.log10(spec[k - 2: k + 3].max())


def test_a_take_with_an_eq_is_sliced_again_when_it_changes(uke):
    p, f = lane(uke, "p"), lane(uke, "f")
    t = np.arange(int(6.5 * SR)) / SR
    hummed = take_audio(p, (0.3, 0.32)) + 0.01 * np.sin(2 * np.pi * 50 * t)
    write(uke, p.files[0], hummed, grid=[0.5, 3.5])
    write(uke, f.files[0], take_audio(f, (0.6, 0.62)), grid=[0.5, 3.5])
    m = process(uke)
    before = result(m, p.key)
    assert before.cleanup_eq == "" and eq_changed_takes(uke, m) == []
    folder = m.samples_dir
    sample = f"{folder}/{before.hits[0].files[uke.signals[0].id]}"
    loud_hum = hum_db(sample)

    eq = CleanupEq(name="Mains hum", takes=[p.key], filters=[EqFilter(kind="hum", hz=50, gain_db=-40, q=20)])
    uke.processing.cleanup_eqs = [eq]
    assert eq_changed_takes(uke, m) == [p.title]  # only the take that uses it
    m = process_one(uke, m, p.key)
    after = result(m, p.key)
    assert after.cleanup_eq and eq_changed_takes(uke, m) == []
    assert [h.start for h in after.hits] == [h.start for h in before.hits]  # cut where they were
    assert hum_db(sample) < loud_hum - 25
    assert result(m, f.key).cleanup_eq == ""

    eq.name = "Hum"
    assert eq_changed_takes(uke, m) == []  # a name changes no sample
    eq.filters[0].hz = 60
    assert eq_changed_takes(uke, m) == [p.title]
    eq.filters[0].hz = 50
    eq.every_take = True
    assert eq_changed_takes(uke, m) == [f.title]
    m = process(uke, m)
    assert eq_changed_takes(uke, m) == [] and result(m, f.key).cleanup_eq
    uke.processing.cleanup_eqs = []
    assert sorted(eq_changed_takes(uke, m)) == sorted([p.title, f.title])


def test_a_moved_recording_takes_its_eq_along(uke):
    p = lane(uke, "p")
    write(uke, p.files[0], take_audio(p, (0.3, 0.32)), grid=[0.5, 3.5])
    uke.processing.cleanup_eqs = [CleanupEq(name="Hum", takes=[p.key], filters=[EqFilter(kind="hum")])]
    other = lane(uke, "p", note=1)
    key = relocate.move_recording(uke, Path(uke.recording.takes_dir),
                                  p.key, other.note_base, "p")
    assert uke.processing.cleanup_eqs[0].takes == [key] and key == other.key


def hummed_take(inst, name="p"):
    take = lane(inst, name)
    t = np.arange(int(6.5 * SR)) / SR
    path = write(inst, take.files[0], take_audio(take, (0.3, 0.32)) + 0.01 * np.sin(2 * np.pi * 50 * t),
                 grid=[0.5, 3.5])
    return take, path


HUM = EqFilter(kind="hum", hz=50, gain_db=-40, q=20)


def test_denoising_works_on_what_the_eq_leaves(uke):
    take, _ = hummed_take(uke)
    floor = 0.01 * np.sin(2 * np.pi * 50 * np.arange(5 * SR) / SR) \
        + np.random.default_rng(2).normal(0, 10 ** (-75 / 20), 5 * SR)
    write(uke, f"noisefloor_{uke.signals[0].id}.wav", floor)
    uke.recording.noise_floor_seconds = 5
    uke.processing.denoise = True
    plain = process(uke)
    sample = f"{plain.samples_dir}/{result(plain, take.key).hits[0].files[uke.signals[0].id]}"
    denoised_only = hum_db(sample)
    uke.processing.cleanup_eqs = [CleanupEq(name="Hum", takes=[take.key], filters=[HUM])]
    m = process(uke, plain)
    audio, _ = sf.read(sample)
    assert np.all(np.isfinite(audio)) and result(m, take.key).cleanup_eq
    assert hum_db(sample) < denoised_only - 15


def test_the_build_waits_for_takes_whose_eq_changed(uke):
    take, _ = hummed_take(uke)
    review.save(process(uke), store.manifest_path(uke.meta.slug))
    uke.processing.cleanup_eqs = [CleanupEq(name="Hum", takes=[take.key], filters=[HUM])]
    with pytest.raises(ValueError, match="clean-up EQ changed since slicing"):
        bundle.build(uke)


def test_a_take_edited_elsewhere_is_cut_again_with_the_eq_set_now(uke):
    from scipy.signal import butter, sosfiltfilt

    take, path = hummed_take(uke)
    eq = CleanupEq(name="Hum", takes=[take.key], filters=[HUM.model_copy()])
    uke.processing.cleanup_eqs = [eq]
    review.save(process(uke), store.manifest_path(uke.meta.slug))
    eq.filters[0].harmonics = 3  # changed, and the take edited in an audio editor, before building
    audio, _ = sf.read(path)
    sf.write(path, 0.8 * sosfiltfilt(butter(2, 3000, "lowpass", fs=SR, output="sos"), audio), SR, subtype="PCM_24")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
    assert slicing.edited_takes(uke, review.load(store.manifest_path(uke.meta.slug))) == [take.key]
    bundle.build(uke)  # cuts it again, with the EQ as it is now: nothing left to slice
    after = review.load(store.manifest_path(uke.meta.slug))
    assert result(after, take.key).cleanup_eq == cleanup_eq.take_setup(uke, take.key, take.signals)
    assert eq_changed_takes(uke, after) == []
