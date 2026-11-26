from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from conftest import piezo_and_mic

from vinst import defaults, guide, notes, recorder
from vinst.plan import plan_takes, planned_notes, recording_passes
from vinst.spec import InstrumentType

SR = 48000
OK = SimpleNamespace(input_overflow=False)


class FakeStream:
    """Stands in for sounddevice.InputStream: the test feeds blocks through the callback."""

    def __init__(self, channels, callback, **_):
        self.channels, self.callback, self.closed = channels, callback, False

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def feed(self, block, status=OK):
        self.callback(block.astype("float32"), len(block), None, status)


def open_recorder(channels):
    streams = []

    def factory(**kw):
        streams.append(FakeStream(**kw))
        return streams[-1]

    rec = recorder.Recorder(recorder.Device(0, "Fake", 4, SR), channels, SR, stream_factory=factory)
    return rec, streams[0]


def test_each_signal_is_written_from_its_input(tmp_path):
    rec, stream = open_recorder({"piezo": 1, "mic": 3})
    assert stream.channels == 3
    block = np.zeros((SR // 10, 3))
    block[:, 0], block[:, 2] = 0.25, -0.5
    stream.feed(block)  # armed, not recording: only the levels move
    assert rec.levels()["mic"] == pytest.approx(-6.02, abs=0.01) and rec.heard
    paths = {"piezo": tmp_path / "a_piezo.wav", "mic": tmp_path / "a_mic.wav"}
    rec.start(paths, 24)
    for _ in range(10):
        stream.feed(block)
    result = rec.stop(keep=True)
    assert result.seconds == pytest.approx(1.0) and result.overflows == 0 and not result.clipped
    piezo, sr = sf.read(paths["piezo"])
    assert sr == SR and len(piezo) == SR and np.allclose(piezo, 0.25, atol=1e-6)
    assert sf.info(paths["mic"]).subtype == "PCM_24"
    assert np.allclose(sf.read(paths["mic"])[0], -0.5, atol=1e-6)
    assert not list(tmp_path.glob("*.part"))
    rec.close()
    assert stream.closed


def test_a_playing_monitor_mixes_its_inputs_onto_the_output():
    streams = []

    def factory(**kw):
        streams.append(kw)
        return SimpleNamespace(start=lambda: None, stop=lambda: None, close=lambda: None)

    rec = recorder.Recorder(recorder.Device(0, "Fake", 4, SR), {"piezo": 1, "mic": 3}, SR,
                            stream_factory=factory, play_channels=2)
    block = np.zeros((64, 3), dtype="float32")
    block[:, 0], block[:, 1], block[:, 2] = 0.25, 0.9, 0.5  # input 2 isn't monitored
    out = np.zeros((64, 2), dtype="float32")
    streams[0]["callback"](block, out, 64, None, OK)
    assert np.allclose(out, 0.75)
    assert rec.levels()["mic"] == pytest.approx(-6.02, abs=0.01)  # metered as without playing
    block[:, 0] = 0.8
    streams[0]["callback"](block, out, 64, None, OK)
    assert np.allclose(out, 1.0)  # clipped, not wrapped
    rec.close()


def test_rms_is_read_per_input_for_the_monitor():
    rec, stream = open_recorder({"piezo": 1, "mic": 2})
    block = np.zeros((SR // 10, 2))
    block[:, 0], block[:, 1] = 0.1, 0.001
    stream.feed(block)
    stream.feed(block)
    rms = rec.rms()
    assert rms["piezo"] == pytest.approx(-20, abs=0.01) and rms["mic"] == pytest.approx(-60, abs=0.01)
    assert rec.rms()["mic"] == float("-inf")  # read since the last call only
    rec.close()


def test_take_stopped_during_the_count_in_keeps_the_old_one(tmp_path):
    path = tmp_path / "take_pickup.wav"
    sf.write(path, np.full(100, 0.1), SR)
    rec, stream = open_recorder({"pickup": 1})
    rec.start({"pickup": path}, 24, skip_seconds=2.0)
    stream.feed(np.ones((SR // 10, 1)) * 0.5)  # still in the part that isn't kept
    result = rec.stop(keep=True)
    assert result.error and not result.files
    assert len(sf.read(path)[0]) == 100 and not list(tmp_path.glob("*.part"))
    rec.close()


def test_cancelled_recording_keeps_the_old_take(tmp_path):
    path = tmp_path / "take_pickup.wav"
    sf.write(path, np.full(100, 0.1), SR)
    rec, stream = open_recorder({"pickup": 1})
    rec.start({"pickup": path}, 24)
    stream.feed(np.ones((1000, 1)) * 0.5)
    rec.stop(keep=False)
    assert len(sf.read(path)[0]) == 100 and not list(tmp_path.glob("*.part"))


def test_dropouts_and_clipping_are_reported(tmp_path):
    rec, stream = open_recorder({"pickup": 2})
    rec.start({"pickup": tmp_path / "t_pickup.wav"}, 16)
    stream.feed(np.zeros((1000, 2)))
    stream.feed(np.ones((1000, 2)), SimpleNamespace(input_overflow=True))
    result = rec.stop(keep=True)
    assert result.overflows == 1 and result.clipped == ["pickup"]


def test_silence_is_noticed():
    rec, stream = open_recorder({"pickup": 1})
    stream.feed(np.zeros((1000, 1)))
    assert not rec.heard and rec.levels()["pickup"] == -np.inf


def uke(monkeypatch, tmp_path):
    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    inst = defaults.new_instrument("Uke", InstrumentType.PLUCKED)
    for s in inst.range.strings:
        s.frets = 1
    inst.dynamics.layers = ["p", "f"]
    inst.dynamics.round_robins = 2
    inst.recording.ring_seconds = 2.0
    return inst


def test_a_take_is_played_at_its_own_pace(tmp_path, monkeypatch):
    inst = uke(monkeypatch, tmp_path)
    take = next(t for t in plan_takes(inst) if t.kind == "note" and t.lane == "f")
    g = guide.take_guide(inst, take)
    assert g.free and g.skip == 0 and g.total == 0
    (play,) = g.steps
    assert (play.kind, play.title) == ("play", "2 hits · f (loud)") and "ring out" in play.detail
    assert guide.take_guide(inst, take, 4).steps[0].title == "4 hits · f (loud)"
    assert guide.more_guide(inst, take, 1).steps[0].title == "1 new hit · f (loud)"


def test_the_noise_floor_counts_in_and_stops_by_itself(tmp_path, monkeypatch):
    inst = uke(monkeypatch, tmp_path)
    g = guide.take_guide(inst, plan_takes(inst)[0])
    count, silence = g.steps
    assert not g.free and count.kind == "count" and g.skip == pytest.approx(3.5)
    assert silence.kind == "silence" and silence.length == 30 and g.total == 34


def test_signals_recorded_together_default_to_separate_inputs(tmp_path, monkeypatch):
    inst = uke(monkeypatch, tmp_path)
    piezo_and_mic(inst)
    assert recorder.channels_for(inst) == {"piezo": 1, "mic": 2}
    inst.recording.input_channels = {"mic": 4}
    assert recorder.channels_for(inst)["mic"] == 4
    device = recorder.Device(0, "Two in", 2, 44100)
    assert "44100 Hz" in recorder.problem(inst, device, ["piezo", "mic"])
    device.sample_rate = 48000
    assert "choose another input for mic" in recorder.problem(inst, device, ["piezo", "mic"])
    assert recorder.problem(inst, device, ["piezo"]) is None


def test_a_take_that_cannot_be_created_leaves_nothing_behind(tmp_path):
    rec, _ = open_recorder({"piezo": 1, "mic": 2})
    (tmp_path / "x_mic.wav.part").mkdir()  # the second file can't be created
    with pytest.raises(recorder.RecorderError, match="Can't write the take"):
        rec.start({"piezo": tmp_path / "x_piezo.wav", "mic": tmp_path / "x_mic.wav"}, 24)
    assert not (tmp_path / "x_piezo.wav.part").exists() and not rec.recording


def test_write_errors_and_short_takes_keep_the_old_take(tmp_path):
    path = tmp_path / "t_pickup.wav"
    sf.write(path, np.full(100, 0.1), SR)
    rec, stream = open_recorder({"pickup": 1})

    class Broken:
        def write(self, _):
            raise OSError("disk full")

        def close(self):
            pass

    rec.start({"pickup": path}, 24)
    rec._files["pickup"] = Broken()
    stream.feed(np.ones((1000, 1)) * 0.1)
    result = rec.stop(keep=True)
    assert "disk full" in result.error and not result.files and len(sf.read(path)[0]) == 100

    rec.start({"pickup": path}, 24)
    stream.feed(np.zeros((SR, 1)))
    result = rec.stop(keep=True, min_seconds=5)  # the interface stopped after 1 s of a 6 s take
    assert "stopped delivering audio after 1 s" in result.error and len(sf.read(path)[0]) == 100
    assert not list(tmp_path.glob("*.part"))


def test_a_block_left_from_the_last_take_is_not_written(tmp_path):
    rec, stream = open_recorder({"pickup": 1})
    rec.start({"pickup": tmp_path / "a_pickup.wav"}, 24)
    rec.stop(keep=False)
    rec._queue.put(np.ones((500, 1), dtype="float32"))  # queued by the audio thread after the stop
    rec.start({"pickup": tmp_path / "b_pickup.wav"}, 24)
    stream.feed(np.zeros((100, 1)))
    rec.stop(keep=True)
    assert len(sf.read(tmp_path / "b_pickup.wav")[0]) == 100


def test_close_is_safe_twice_and_frees_the_device():
    before = recorder.Recorder.open_count
    rec, stream = open_recorder({"pickup": 1})
    assert recorder.Recorder.open_count == before + 1
    rec.close()
    rec.close()
    assert recorder.Recorder.open_count == before and stream.closed


def test_a_chosen_interface_that_is_unplugged_is_not_replaced():
    devices = [recorder.Device(0, "MacBook Pro Microphone", 1, SR)]
    assert recorder.find_device("ZEDi10", devices) is None
    assert recorder.find_device("MacBook Pro Microphone", devices) is devices[0]


def test_detect_needs_one_clearly_loudest_input():
    from vinst.ui.recording import loudest

    assert loudest({"in1": -60.0, "in2": -20.0, "in3": -58.0}) == "in2"
    assert loudest({"in1": -25.0, "in2": -20.0}) is None  # both inputs carry the signal
    assert loudest({"in1": -70.0, "in2": -65.0}) is None  # nothing played


def test_the_count_in_is_not_written(tmp_path):
    rec, stream = open_recorder({"pickup": 1})
    rec.start({"pickup": tmp_path / "t_pickup.wav"}, 24, skip_seconds=0.5)
    noise = np.full((SR // 4, 1), 0.3)  # handling noise during the count-in
    stream.feed(noise)
    stream.feed(noise)
    stream.feed(np.full((SR // 2, 1), 0.1))
    result = rec.stop(keep=True)
    audio, _ = sf.read(tmp_path / "t_pickup.wav")
    assert result.seconds == pytest.approx(0.5) and np.allclose(audio, 0.1, atol=1e-6)


def test_a_quiet_loud_take_says_how_much_to_turn_the_gain_up():
    assert recorder.level_advice(-8.0) is None
    assert recorder.level_advice(-55.0) is None  # nothing played: no gain to suggest
    advice = recorder.level_advice(-18.0)
    assert "peaks at -18 dBFS" in advice and "up by about 12 dB" in advice and "same for all takes" in advice


def test_scale_guide_asks_for_the_scale_low_to_high(tmp_path, monkeypatch):
    inst = uke(monkeypatch, tmp_path)
    p = recording_passes(inst)[0]
    midis = sorted(planned_notes(inst, p.id))
    assert len(midis) > 2
    g = guide.scale_guide(inst, p.id, 2)
    (play,) = g.steps
    assert g.free and play.title.startswith(f"{notes.name(midis[0])}, ") and play.title.endswith(" · 2 hits each")
