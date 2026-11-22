import numpy as np
import pytest
import soundfile as sf
from conftest import piezo_and_mic

from vinst import defaults, split
from vinst.plan import recording_passes
from vinst.slicing import process
from vinst.spec import InstrumentType, NoteLanes
from vinst.synth import pluck

SR = 48000


def scale_recording(midis: list[int], hits: int, ring: float = 1.2, gap: float = 0.3, seed: int = 3,
                    amp: float = 0.4) -> np.ndarray:
    """One long recording: each note `hits` times, ringing until the next."""
    rng = np.random.default_rng(seed)
    parts = [np.zeros(int(0.5 * SR))]
    for m in midis:
        for _ in range(hits):
            parts += [pluck(440 * 2 ** ((m + rng.uniform(-0.08, 0.08) - 69) / 12), amp * rng.uniform(0.9, 1.1),
                            ring, SR, rng), np.zeros(int(gap * SR))]
    dry = np.concatenate(parts)
    return np.clip(dry + rng.normal(0, 10 ** (-70 / 20), dry.size), -1, 1)


@pytest.fixture
def uke(tmp_path):
    inst = defaults.new_instrument("Scale", InstrumentType.PLUCKED)
    for s in inst.range.strings:
        s.frets = 2
    inst.dynamics.round_robins = 3
    inst.extras = []
    inst.recording.takes_dir = str(tmp_path / "takes")
    return inst


def test_a_scale_is_cut_into_one_take_per_note(uke, tmp_path):
    p = recording_passes(uke)[0]
    midis = sorted(split.planned_notes(uke, p.id))
    audio = scale_recording(midis, 3)
    found = split.find(uke, p, [audio], SR)
    assert [(r.midi, r.hits) for r in found.runs] == [(m, 3) for m in midis]
    takes_dir = tmp_path / "takes"
    placed = split.place(uke, p, found, "p", takes_dir)
    assert all(pl.take is not None and not pl.more for pl in placed)
    keys = split.write(uke, [audio], SR, placed, takes_dir)
    assert len(keys) == len(midis)
    m = process(uke)
    by_key = {t.key: t for t in m.takes}
    for pl in placed:
        result = by_key[pl.take.key]
        assert len(result.hits) == 3, result.base
        assert all(not {"wrong_note", "no_pitch", "cut_off"} & set(h.flags) for h in result.hits), result.base


def test_hits_start_at_their_pluck_in_each_take(uke, tmp_path):
    """A take starts a little before its first pluck, in the tail of the note before; slicing still
    starts the hit at the pluck."""
    p = recording_passes(uke)[0]
    midis = sorted(split.planned_notes(uke, p.id))[:4]
    audio = scale_recording(midis, 2, ring=1.5, gap=0.0)  # each pluck cuts the ringing note before
    found = split.find(uke, p, [audio], SR)
    placed = split.place(uke, p, found, "p", tmp_path / "takes")
    split.write(uke, [audio], SR, placed, tmp_path / "takes")
    m = process(uke)
    step = int(1.5 * SR)
    for pl in placed:
        result = next(t for t in m.takes if t.key == pl.take.key)
        plucks = [int(0.5 * SR) + k * step - pl.run.start for k in range(len(midis) * 2)]
        for h in result.hits:
            assert min(abs(h.start - x) for x in plucks) < 0.004 * SR, (result.base, h.start)


def test_recorded_notes_get_more_hits_and_nothing_is_overwritten(uke, tmp_path):
    p = recording_passes(uke)[0]
    c, d = 60, 62
    takes_dir = tmp_path / "takes"
    takes_dir.mkdir()
    (takes_dir / "060_C-0_p_pickup.wav").write_bytes(b"recorded")
    audio = scale_recording([c, c, d, c], 2)  # C twice in a row is one run; C again later is another
    found = split.find(uke, p, [audio], SR)
    assert [(r.midi, r.hits) for r in found.runs] == [(c, 4), (d, 2), (c, 2)]
    placed = split.place(uke, p, found, "p", takes_dir)
    assert [pl.more for pl in placed] == [True, False, True]
    keys = split.write(uke, [audio], SR, placed, takes_dir)
    assert keys == ["060_C-0_p-more1", "062_C-2_p", "060_C-0_p-more2"]
    assert (takes_dir / "060_C-0_p_pickup.wav").read_bytes() == b"recorded"


def test_an_empty_take_is_written_over_not_added_to(uke, tmp_path):
    p = recording_passes(uke)[0]
    takes_dir = tmp_path / "takes"
    takes_dir.mkdir()
    sf.write(takes_dir / "060_C-0_p_pickup.wav", np.zeros(0), SR)  # e.g. a cancelled export
    audio = scale_recording([60], 2)
    placed = split.place(uke, p, split.find(uke, p, [audio], SR), "p", takes_dir)
    assert [pl.more for pl in placed] == [False]
    assert split.write(uke, [audio], SR, placed, takes_dir) == ["060_C-0_p"]
    assert sf.info(takes_dir / "060_C-0_p_pickup.wav").frames > 0


def test_notes_outside_the_instrument_are_left_out(uke, tmp_path):
    p = recording_passes(uke)[0]
    uke.note_lanes = {"062_C-2": NoteLanes(layers=["f"])}  # this note has no p
    audio = scale_recording([60, 62, 84], 2)  # C6 is far above a ukulele with two frets
    found = split.find(uke, p, [audio], SR)
    placed = split.place(uke, p, found, "p", tmp_path / "takes")
    assert [(pl.run.midi, pl.take is not None) for pl in placed] == [(60, True), (62, False), (84, False)]
    assert "without p" in placed[1].why and "not one of the instrument's notes" in placed[2].why
    assert split.write(uke, [audio], SR, placed, tmp_path / "takes") == ["060_C-0_p"]


def test_a_wrong_note_inside_a_run_stays_in_its_take(uke, tmp_path):
    p = recording_passes(uke)[0]
    rng = np.random.default_rng(5)
    parts = [np.zeros(int(0.5 * SR))]
    for m in [60, 60, 61, 60, 64, 64]:
        parts += [pluck(440 * 2 ** ((m - 69) / 12), 0.4, 1.2, SR, rng), np.zeros(int(0.3 * SR))]
    audio = np.concatenate(parts)
    found = split.find(uke, p, [audio], SR)
    assert [(r.midi, r.hits) for r in found.runs] == [(60, 4), (64, 2)] and found.corrected == 1
    split.write(uke, [audio], SR, split.place(uke, p, found, "p", tmp_path / "takes"), tmp_path / "takes")
    c = next(t for t in process(uke).takes if t.base == "060_C-0_p")
    assert ["wrong_note" in h.flags for h in c.hits] == [False, False, True, False]


def test_two_signals_are_cut_at_the_same_points(uke, tmp_path):
    piezo_and_mic(uke)
    p = recording_passes(uke)[0]
    names = ["scale_mic.wav", "scale_piezo.wav"]
    assert split.pass_for(uke, names) == (p, [1, 0])
    assert "one file per signal" in split.pass_for(uke, ["scale.wav"])
    piezo = scale_recording([60, 62], 2)
    mic = np.convolve(piezo, np.ones(8) / 8, mode="same") * 0.8
    found = split.find(uke, p, [piezo, mic], SR)
    keys = split.write(uke, [piezo, mic], SR, split.place(uke, p, found, "p", tmp_path / "takes"),
                       tmp_path / "takes")
    assert keys == ["060_C-0_p", "062_C-2_p"]
    for k in keys:
        a, b = (sf.info(tmp_path / "takes" / f"{k}_{s}.wav").frames for s in ("piezo", "mic"))
        assert a == b


def test_separate_passes_are_told_by_the_file_name(uke):
    from vinst.spec import Signal

    uke.signals = [Signal(id="piezo", name="Piezo"), Signal(id="mic", name="Mic", recorded="separate")]
    passes = recording_passes(uke)
    assert split.pass_for(uke, ["Scale MIC.wav"]) == (passes[1], [0])
    assert split.pass_for(uke, ["scale-piezo.wav"]) == (passes[0], [0])
    assert "file name" in split.pass_for(uke, ["scale.wav"])


def test_a_scale_played_once_up_and_down_keeps_its_top(uke):
    p = recording_passes(uke)[0]
    found = split.find(uke, p, [scale_recording([64, 65, 66, 65, 64], 1)], SR)
    assert [r.midi for r in found.runs] == [64, 65, 66, 65, 64] and found.corrected == 0


@pytest.mark.parametrize("midi", [72, 48])  # an octave above and below a note of the instrument (60)
def test_notes_an_octave_outside_the_range_are_not_taken_for_it(uke, tmp_path, midi):
    p = recording_passes(uke)[0]
    assert midi not in split.planned_notes(uke, p.id) and 60 in split.planned_notes(uke, p.id)
    found = split.find(uke, p, [scale_recording([60, midi], 2)], SR)
    assert [(r.midi, r.hits) for r in found.runs] == [(60, 2), (midi, 2)]
    assert [pl.take is None for pl in split.place(uke, p, found, "p", tmp_path / "takes")] == [False, True]


def test_an_octave_misread_is_put_right(monkeypatch):
    rng = np.random.default_rng(2)
    tone = pluck(440 * 2 ** ((60 - 69) / 12), 0.5, 1.0, SR, rng)
    monkeypatch.setattr(split, "free_pitch", lambda *_: 72.0)  # YIN an octave high
    assert split.note_of(tone, SR, {60, 62}) == 60
    monkeypatch.setattr(split, "free_pitch", lambda *_: 48.0)  # an octave low
    assert split.note_of(tone, SR, {60, 62}) == 60


def test_a_take_is_written_whole_or_not_at_all(uke, tmp_path, monkeypatch):
    piezo_and_mic(uke)
    p = recording_passes(uke)[0]
    audio = scale_recording([60], 2)
    found = split.find(uke, p, [audio, audio], SR)
    placed = split.place(uke, p, found, "p", tmp_path / "takes")
    real = sf.write

    def fail_on_mic(path, *args, **kwargs):
        if "_mic" in str(path):
            raise OSError("disk full")
        real(path, *args, **kwargs)

    monkeypatch.setattr(split.sf, "write", fail_on_mic)
    with pytest.raises(OSError):
        split.write(uke, [audio, audio], SR, placed, tmp_path / "takes")
    assert list((tmp_path / "takes").iterdir()) == []


def test_drops_that_dont_fit_the_signals_say_why(uke):
    from vinst.spec import Signal

    assert "one signal: drop one file" in split.pass_for(uke, ["a.wav", "b.wav"])
    uke.signals = [Signal(id="piezo", name="Piezo"), Signal(id="mic", name="Mic", recorded="separate")]
    assert "one pass at a time" in split.pass_for(uke, ["scale_piezo.wav", "scale_mic.wav"])
