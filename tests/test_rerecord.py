"""Lane takes in the worksheet: more variations, old combined takes, moving hits, change detection."""

import io
import os

import numpy as np
import pytest
import soundfile as sf
from lxml import etree

from vinst import bundle, defaults, review, store, takes_meta
from vinst.plan import combined_base, more_file, plan_takes
from vinst.slicing import changed_takes, cut_points, on_grid, process, process_one
from vinst.spec import InstrumentType, NoteLanes
from vinst.synth import knock, pluck

SR = 48000


@pytest.fixture
def uke(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VINST_BUILD", str(tmp_path / "build"))
    inst = defaults.new_instrument("Uke", InstrumentType.PLUCKED)
    for s in inst.range.strings:
        s.frets = 0
    inst.extras = []
    inst.dynamics.layers = ["p", "f"]
    inst.dynamics.round_robins = 2
    inst.recording.ring_seconds = 2.0
    inst.recording.noise_floor_seconds = 0
    inst.recording.takes_dir = str(tmp_path / "takes")
    (tmp_path / "takes").mkdir()
    return store.create(inst)


def lane(inst, name="p", note=0):
    notes = [t for t in plan_takes(inst) if t.kind == "note"]
    return [t for t in notes if t.lane == name][note]


def take_audio(take, amps, noises=(), bar=3.0, offset=0.5, seed=None):
    """Hits as the app records them: pre-roll, one per bar, plus handling noise at the given times."""
    rng = np.random.default_rng(take.number if seed is None else seed)
    audio = rng.normal(0, 10 ** (-75 / 20), int((offset + bar * len(amps)) * SR))
    for i, amp in enumerate(amps):
        at = int((offset + i * bar + 0.1) * SR)
        tone = pluck(440 * 2 ** ((take.note.midi - 69) / 12), amp, 2.0, SR, rng)
        audio[at: at + len(tone)] += tone[: len(audio) - at]
    for t in noises:
        k = knock(0.02, SR, rng)
        audio[int(t * SR): int(t * SR) + len(k)] += k
    return audio


def write(inst, name, audio, grid=None):
    path = store.instrument_dir(inst.meta.slug).parent.parent / "takes" / name
    sf.write(path, audio, SR, subtype="PCM_24")
    if grid is not None:
        takes_meta.save(inst.meta.slug, path, grid)
    return path


def result(manifest, key):
    return next(t for t in manifest.takes if t.key == key)


def test_noise_ends_the_hit_before_it_but_starts_none():
    rng = np.random.default_rng(3)
    ref = rng.normal(0, 1e-4, 8 * SR)
    tone = pluck(261.6, 0.5, 6.0, SR, rng)
    ref[int(0.5 * SR): int(0.5 * SR) + len(tone)] += tone
    noise = int(3.0 * SR)
    (start, end, cut_off), = cut_points(ref, SR, [int(0.5 * SR)], -60, noises=[noise], ring_frames=2 * SR)
    assert end <= noise and not cut_off  # rang 2.5 s before the touch: long enough
    (_, _, cut_off), = cut_points(ref, SR, [int(0.5 * SR)], -60, noises=[noise], ring_frames=4 * SR)
    assert cut_off


def test_on_grid_takes_the_strongest_onset_in_each_bar():
    ref = np.zeros(10 * SR)
    for at, amp in ((0.9, 0.01), (1.1, 0.5), (4.0, 0.3), (5.6, 0.02)):
        ref[int(at * SR): int(at * SR) + 100] = amp
    onsets = [int(t * SR) for t in (0.9, 1.1, 4.0, 5.6)]
    assert on_grid(onsets, ref, SR, [1.0, 4.0, 7.0]) == [int(1.1 * SR), int(4.0 * SR)]


def test_the_click_grid_drops_handling_noise(uke):
    take = lane(uke, "p")
    write(uke, take.files[0], take_audio(take, (0.2, 0.25), noises=[0.05]), grid=[0.5, 3.5])
    r = result(process(uke), take.key)
    assert r.status == "ok" and len(r.hits) == 2 and all(h.layer == "p" for h in r.hits)


def test_the_grid_is_ignored_once_the_file_changes(uke):
    take = lane(uke, "p")
    path = write(uke, take.files[0], take_audio(take, (0.2, 0.25)), grid=[0.5])
    write(uke, take.files[0], take_audio(take, (0.2, 0.25, 0.3)))  # replaced from a DAW
    assert takes_meta.hits_at(uke.meta.slug, path) is None


def test_more_variations_join_their_lane_and_keep_decisions(uke):
    take = lane(uke, "f")
    write(uke, take.files[0], take_audio(take, (0.7, 0.72)), grid=[0.5, 3.5])
    first = process(uke)
    more = write(uke, more_file(take, 1, take.signals[0]), take_audio(take, (0.69, 0.71, 0.73), seed=9),
                 grid=[0.5, 3.5, 6.5])
    assert changed_takes(uke, first) == [f"{take.title} · more (1)"]
    after = process_one(uke, first, f"{take.base_name}-more1")
    more_result = result(after, f"{take.base_name}-more1")
    assert [h.layer for h in more_result.hits] == ["f", "f", "f"] and more_result.origin == "more"
    assert result(after, take.key) == result(first, take.key)  # only the new recording was sliced
    more_result.hits[1].rejected = True
    again = process(uke, previous=after)
    assert result(again, f"{take.base_name}-more1").hits[1].rejected
    assert changed_takes(uke, again) == []
    more.unlink()  # going back
    assert changed_takes(uke, again) == [f"{take.title} · more (1)"]


def test_takes_recorded_again_are_noticed_before_building(uke):
    take = lane(uke, "p")
    path = write(uke, take.files[0], take_audio(take, (0.2, 0.25)))
    review.save(process(uke), store.manifest_path(uke.meta.slug))
    manifest = review.load(store.manifest_path(uke.meta.slug))
    assert changed_takes(uke, manifest) == []
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))  # recorded again
    assert changed_takes(uke, manifest) == [take.title]
    with pytest.raises(ValueError, match="Recorded again since slicing"):
        bundle.build(uke)
    other = lane(uke, "f")
    write(uke, other.files[0], take_audio(other, (0.7, 0.7)))  # a new lane take, not sliced yet
    assert other.title in changed_takes(uke, manifest)


def test_a_combined_take_starts_in_the_first_lane_and_hits_can_be_moved(uke):
    p, f = lane(uke, "p"), lane(uke, "f")
    name = f"{combined_base(p)}_{p.signals[0]}.wav"
    write(uke, name, take_audio(p, (0.2, 0.25, 0.7, 0.75)), grid=[0.5, 3.5, 6.5, 9.5])
    m = process(uke)
    combined = result(m, p.note_base)
    assert combined.origin == "combined" and [h.layer for h in combined.hits] == ["p"] * 4
    assert result(m, p.key).status == "missing" and result(m, f.key).status == "missing"
    # Drag the loud ones to f, as in the worksheet.
    for h in combined.hits[2:]:
        h.layer_set = "f"
    review.settle(combined, ["p", "f"])
    review.check_lanes([combined], ["p", "f"])
    assert [h.layer for h in combined.hits] == ["p", "p", "f", "f"]
    again = result(process(uke, previous=m), p.note_base)
    assert [h.layer for h in again.hits] == ["p", "p", "f", "f"]  # the move survives slicing again
    review.save(process(uke, previous=m), store.manifest_path(uke.meta.slug))
    built = bundle.build(uke)
    vels = {(s.get("loVel"), s.get("hiVel")) for s in etree.parse(str(built.preset_file)).iter("sample")
            if s.get("rootNote") == str(p.note.midi)}
    assert vels == {("1", "64"), ("65", "127")}


def test_hits_of_a_removed_lane_are_left_out(uke):
    take, soft = lane(uke, "f"), lane(uke, "p")
    write(uke, take.files[0], take_audio(take, (0.7, 0.72)))
    write(uke, soft.files[0], take_audio(soft, (0.2, 0.22)))
    review.save(process(uke), store.manifest_path(uke.meta.slug))
    uke.note_lanes[take.note_base] = NoteLanes(layers=["p"])  # this note now has only a p lane
    m = process(uke, previous=review.load(store.manifest_path(uke.meta.slug)))
    kept = result(m, take.key)  # not planned any more, but kept so its hits can be dragged into a lane
    assert kept.origin == "unplanned" and all(h.layer is None for h in kept.hits)
    review.save(m, store.manifest_path(uke.meta.slug))
    built = bundle.build(uke)
    vels = {(s.get("loVel"), s.get("hiVel")) for s in etree.parse(str(built.preset_file)).iter("sample")
            if s.get("rootNote") == str(take.note.midi)}
    assert built.report.ok and vels == {("1", "127")}  # its p lane plays all velocities


def test_lanes_out_of_order_are_reported(uke):
    p, f = lane(uke, "p"), lane(uke, "f")
    write(uke, p.files[0], take_audio(p, (0.7, 0.72)))  # played loud
    write(uke, f.files[0], take_audio(f, (0.2, 0.22)))  # played soft
    m = process(uke)
    texts = " ".join(r.message for r in m.takes if r.note == p.note_base)
    assert "don't get louder in order" in texts


def test_a_note_with_its_own_lanes_builds_its_ranges(uke):
    note = lane(uke, "p").note_base
    uke.note_lanes[note] = NoteLanes(layers=["p", "f", "ff"], splits=[50, 110])
    for name, amps in (("p", (0.2, 0.22)), ("f", (0.6, 0.62)), ("ff", (0.9, 0.92))):
        take = next(t for t in plan_takes(uke) if t.note_base == note and t.lane == name)
        write(uke, take.files[0], take_audio(take, amps))
    review.save(process(uke), store.manifest_path(uke.meta.slug))
    built = bundle.build(uke)
    midi = lane(uke, "p").note.midi
    vels = {(s.get("loVel"), s.get("hiVel")) for s in etree.parse(str(built.preset_file)).iter("sample")
            if s.get("rootNote") == str(midi)}
    assert vels == {("1", "49"), ("50", "109"), ("110", "127")}


def test_compare_plays_the_attacks_one_after_another(uke):
    from vinst.ui.worksheet import compare_file

    take = lane(uke, "f")
    write(uke, take.files[0], take_audio(take, (0.7, 0.72)))
    review.save(process(uke), store.manifest_path(uke.meta.slug))
    r = result(review.load(store.manifest_path(uke.meta.slug)), take.key)
    names = [h.files["pickup"] for h in r.hits]
    folder = review.samples_dir(uke)
    lengths = [sf.info(folder / n).frames for n in names]
    audio, sr = sf.read(io.BytesIO(compare_file(uke.meta.slug, names).body))
    step = int(0.75 * SR)  # every sample but the last is cut to 0.75 s
    assert sr == SR and len(audio) == step * (len(names) - 1) + lengths[-1]
    first, _ = sf.read(folder / names[0])
    assert np.allclose(audio[: SR // 4], first[: SR // 4], atol=1e-6)  # the attack is untouched
    assert abs(audio[step - 1]) < 1e-6  # faded out before the next attack
    with pytest.raises(Exception):
        compare_file(uke.meta.slug, ["../instrument.toml"])


def test_a_removed_lane_keeps_its_recording_and_moved_hits(uke):
    p, f = lane(uke, "p"), lane(uke, "f")
    write(uke, p.files[0], take_audio(p, (0.2, 0.22)))
    write(uke, f.files[0], take_audio(f, (0.7, 0.72)))
    m = process(uke)
    f_take = result(m, f.key)
    f_take.hits[0].layer_set = "p"  # dragged into p
    review.settle(f_take, ["p", "f"])
    uke.note_lanes[f.note_base] = NoteLanes(layers=["p"])  # then the f lane is removed from the note
    again = process(uke, previous=m)
    kept = result(again, f.key)
    assert kept.origin == "unplanned" and "velocity removed" in kept.title
    assert [h.layer for h in kept.hits] == ["p", None]  # the moved one stays in p, the other has no lane
    review.save(again, store.manifest_path(uke.meta.slug))
    built = bundle.build(uke)
    assert any("have no velocity" in w for w in built.warnings)
    paths = {s.get("path").split("/")[-1] for s in etree.parse(str(built.preset_file)).iter("sample")}
    assert kept.hits[0].files["pickup"] in paths and kept.hits[1].files["pickup"] not in paths


def test_lanes_follow_a_change_before_slicing_again(uke):
    from vinst.review import lane_of

    p, f = lane(uke, "p"), lane(uke, "f")
    write(uke, f.files[0], take_audio(f, (0.7, 0.72)))
    write(uke, p.files[0], take_audio(p, (0.2, 0.22)))
    m = process(uke)
    r = result(m, f.key)
    assert [lane_of(h, r, ["p"]) for h in r.hits] == [None, None]  # f removed: no lane, at once
    review.save(m, store.manifest_path(uke.meta.slug))
    uke.note_lanes[f.note_base] = NoteLanes(layers=["p"])
    built = bundle.build(uke)  # not sliced again: the f take is reported, its hits stay out
    assert any("not in the current plan" in w for w in built.warnings)
    paths = {s.get("path").split("/")[-1] for s in etree.parse(str(built.preset_file)).iter("sample")}
    assert not any(h.files["pickup"] in paths for h in r.hits)


def test_files_of_earlier_versions_are_not_lanes(uke):
    from vinst.slicing import recordings

    p = lane(uke, "p")
    write(uke, f"{p.note_base}_hit3_{p.signals[0]}.wav", take_audio(p, (0.2,)))
    write(uke, f"{p.note_base}_add-f-1_{p.signals[0]}.wav", take_audio(p, (0.2,)))
    takes_dir = store.instrument_dir(uke.meta.slug).parent.parent / "takes"
    assert not [t for t in recordings(uke, takes_dir) if t.origin == "unplanned"]


def test_takes_from_before_lanes_count_as_recorded(uke):
    from vinst.plan import summarize

    p = lane(uke, "p")
    assert summarize(uke).recorded_files == 0
    write(uke, f"{combined_base(p)}_{p.signals[0]}.wav", take_audio(p, (0.2, 0.7)))
    assert summarize(uke).recorded_files == 1


def test_dropped_files_become_the_take_or_more_variations(uke):
    import asyncio

    from vinst.ui.worksheet import drop_file

    class Body:  # the part of a request the endpoint reads
        def __init__(self, data):
            self.data = data

        async def body(self):
            return self.data

    take = lane(uke, "f")
    buf = io.BytesIO()
    sf.write(buf, take_audio(take, (0.7, 0.72)), 44100, format="WAV", subtype="PCM_16")
    drop = lambda name: asyncio.run(drop_file(uke.meta.slug, take.key, Body(buf.getvalue()), name))  # noqa: E731
    first = drop("C3 loud take.wav")
    takes = store.instrument_dir(uke.meta.slug).parent.parent / "takes"
    assert first["ok"] and first["key"] == take.key and (takes / take.files[0]).exists()
    assert "44100 Hz" in first["message"]  # the instrument records at 48 kHz
    assert sf.info(takes / take.files[0]).subtype == "PCM_24"
    second = drop("another.wav")  # already recorded: kept, the new file becomes more variations
    assert second["ok"] and second["key"] == f"{take.base_name}-more1"
    assert (takes / more_file(take, 1, take.signals[0])).exists()
    bad = asyncio.run(drop_file(uke.meta.slug, take.key, Body(b"not audio"), "notes.txt"))
    assert not bad["ok"] and "isn't an audio file" in bad["message"]
