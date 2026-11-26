"""Lane takes in the worksheet: more variations, old combined takes, moving hits, change detection, deleting."""

import io
import os
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from lxml import etree

from conftest import lane_ranges
from vinst import bundle, defaults, discard, review, slicing, store, takes_meta
from vinst.plan import combined_base, more_file, plan_takes
from vinst.slicing import changed_takes, cut_points, process, process_one
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


def write(inst, name, audio, in_app=False):
    path = store.instrument_dir(inst.meta.slug).parent.parent / "takes" / name
    sf.write(path, audio, SR, subtype="PCM_24")
    if in_app:
        takes_meta.save(inst.meta.slug, path)
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


def test_handling_noise_isnt_a_hit(uke):
    take = lane(uke, "p")
    write(uke, take.files[0], take_audio(take, (0.2, 0.25), noises=[0.05]), in_app=True)
    r = result(process(uke), take.key)
    assert r.status == "ok" and len(r.hits) == 2 and all(h.layer == "p" for h in r.hits)


def test_hits_played_at_any_pace_are_all_found(uke):
    take = lane(uke, "p")
    # Three hits half a ring apart: no click asked for them.
    write(uke, take.files[0], take_audio(take, (0.2, 0.25, 0.22), bar=1.0), in_app=True)
    assert len(result(process(uke), take.key).hits) == 3


def test_more_variations_join_their_lane_and_keep_decisions(uke):
    take = lane(uke, "f")
    write(uke, take.files[0], take_audio(take, (0.7, 0.72)), in_app=True)
    first = process(uke)
    more = write(uke, more_file(take, 1, take.signals[0]), take_audio(take, (0.69, 0.71, 0.73), seed=9),
                 in_app=True)
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
    # Recorded again, outside the app: the same length, another performance.
    sf.write(path, take_audio(take, (0.2, 0.25), seed=99), SR, subtype="PCM_24")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
    assert changed_takes(uke, manifest) == [take.title]
    with pytest.raises(ValueError, match="Recorded or changed since slicing"):
        bundle.build(uke)
    other = lane(uke, "f")
    write(uke, other.files[0], take_audio(other, (0.7, 0.7)))  # a new lane take, not sliced yet
    assert other.title in changed_takes(uke, manifest)


def test_a_combined_take_starts_in_the_first_lane_and_hits_can_be_moved(uke):
    p, f = lane(uke, "p"), lane(uke, "f")
    name = f"{combined_base(p)}_{p.signals[0]}.wav"
    write(uke, name, take_audio(p, (0.2, 0.25, 0.7, 0.75)), in_app=True)
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
    vels = lane_ranges(s for s in etree.parse(str(built.preset_file)).iter("sample")
                       if s.get("rootNote") == str(p.note.midi))
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
    vels = lane_ranges(s for s in etree.parse(str(built.preset_file)).iter("sample")
                       if s.get("rootNote") == str(take.note.midi))
    assert built.report.ok and vels == {("1", "127")}  # its p lane plays all velocities


def test_lanes_out_of_order_are_reported(uke):
    p, f = lane(uke, "p"), lane(uke, "f")
    write(uke, p.files[0], take_audio(p, (0.7, 0.72)))  # played loud
    write(uke, f.files[0], take_audio(f, (0.2, 0.22)))  # played soft
    m = process(uke)
    texts = " ".join(r.message for r in m.takes if r.note == p.note_base)
    assert "is no louder than p" in texts and "playing harder wouldn't sound louder" in texts


def test_a_note_with_its_own_lanes_builds_its_ranges(uke):
    note = lane(uke, "p").note_base
    uke.note_lanes[note] = NoteLanes(layers=["p", "f", "ff"], splits=[50, 110])
    for name, amps in (("p", (0.2, 0.22)), ("f", (0.6, 0.62)), ("ff", (0.9, 0.92))):
        take = next(t for t in plan_takes(uke) if t.note_base == note and t.lane == name)
        write(uke, take.files[0], take_audio(take, amps))
    review.save(process(uke), store.manifest_path(uke.meta.slug))
    built = bundle.build(uke)
    midi = lane(uke, "p").note.midi
    vels = lane_ranges(s for s in etree.parse(str(built.preset_file)).iter("sample")
                       if s.get("rootNote") == str(midi))
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
        def __init__(self, data, headers=None):
            self.data = data
            self.headers = {"X-Vinst-Drop": "1"} if headers is None else headers

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
    from fastapi import HTTPException

    with pytest.raises(HTTPException):  # another web page can't send the header without asking first
        asyncio.run(drop_file(uke.meta.slug, take.key, Body(buf.getvalue(), headers={}), "x.wav"))
    bad = asyncio.run(drop_file(uke.meta.slug, take.key, Body(b"not audio"), "notes.txt"))
    assert not bad["ok"] and "isn't an audio file" in bad["message"]
    empty = io.BytesIO()
    sf.write(empty, np.zeros(0), 44100, format="WAV")
    nothing = asyncio.run(drop_file(uke.meta.slug, take.key, Body(empty.getvalue()), "export.wav"))
    assert not nothing["ok"] and "is empty" in nothing["message"]
    sf.write(takes / take.files[0], np.zeros(0), SR)  # empty, e.g. from an older version
    again = drop("C3 loud take, exported again.wav")
    assert again["ok"] and again["key"] == take.key  # not more hits beside the empty one


def test_velocities_out_of_loudness_order_name_the_pair():
    from vinst.review import Hit, TakeResult, check_lanes

    def hit(i: int, lane: str, db: float) -> Hit:
        return Hit(id=f"h{i}", index=i, layer=lane, start=0, end=1, rms_db=db, files={}, peak_db={})

    take = TakeResult(number=1, base="060_C-0_p", title="C3", kind="note", status="ok", expected_hits=4,
                      hits=[hit(1, "p", -18), hit(2, "p", -18), hit(3, "f", -20), hit(4, "f", -20)])
    check_lanes([take], ["p", "f"])
    assert take.message == "f (-20 dBFS) is no louder than p (-18 dBFS), so playing harder wouldn't sound louder"
    check_lanes([take], ["p", "f"])  # checking again replaces the message instead of adding one
    assert take.message.count("playing harder") == 1


def test_a_take_played_on_the_wrong_note_moves_to_the_right_one(uke):
    from vinst.relocate import move_recording

    takes = store.instrument_dir(uke.meta.slug).parent.parent / "takes"
    wrong, right = lane(uke, "p", 0), lane(uke, "p", 1)
    path = write(uke, wrong.files[0], take_audio(right, (0.3, 0.32)), in_app=True)
    key = move_recording(uke, takes, wrong.key, right.note_base, "p")
    assert key == right.key and not path.exists() and (takes / right.files[0]).exists()
    assert takes_meta.recorded_now(uke.meta.slug, takes / right.files[0])  # what the app knows came along
    m = process(uke)
    assert result(m, wrong.key).status == "missing" and len(result(m, right.key).hits) == 2


def test_a_moved_take_never_replaces_a_recorded_one(uke):
    from vinst.relocate import move_recording

    takes = store.instrument_dir(uke.meta.slug).parent.parent / "takes"
    wrong, right = lane(uke, "f", 0), lane(uke, "f", 1)
    write(uke, wrong.files[0], take_audio(right, (0.6, 0.62)))
    write(uke, right.files[0], take_audio(right, (0.6, 0.61)))
    key = move_recording(uke, takes, wrong.key, right.note_base, "f")
    assert (takes / more_file(right, 1, right.signals[0])).exists() and key.endswith(f"{right.base_name}-more1")
    m = process(uke)
    assert len(result(m, right.key).hits) == 2 and len(result(m, key).hits) == 2


def test_a_pluck_after_a_quiet_touch_is_still_the_hit(uke):
    """A late pluck, after a quiet touch of the string, is the hit; a faint click later isn't."""
    take = lane(uke, "p")
    rng = np.random.default_rng(7)
    audio = rng.normal(0, 10 ** (-75 / 20), int(7.5 * SR))
    touch_at, pluck_at = int(1.45 * SR), int(1.55 * SR)
    audio[touch_at:pluck_at] += rng.normal(0, 10 ** (-50 / 20), pluck_at - touch_at)
    tone = pluck(440 * 2 ** ((take.note.midi - 69) / 12), 0.3, 2.0, SR, rng)
    audio[pluck_at: pluck_at + len(tone)] += tone
    k = knock(0.004, SR, rng)  # a faint click later in the take
    audio[int(5.7 * SR): int(5.7 * SR) + len(k)] += k
    write(uke, take.files[0], audio, in_app=True)
    hits = result(process(uke), take.key).hits
    assert len(hits) == 1 and 1.4 * SR <= hits[0].start <= 1.6 * SR


def test_decisions_stay_with_hits_cut_where_they_were(uke):
    from vinst.review import Hit, TakeResult
    from vinst.slicing import carry_over

    def take(*starts, rejected=False):
        return TakeResult(number=1, base="055_G-0_p", title="G2", kind="note", status="ok", expected_hits=2,
                          note="055_G-0", lane="p", source="same", sample_rate=SR, hits=[
                              Hit(id=f"h{i}", index=i, layer="p", start=s, end=s + SR, rms_db=-20, files={},
                                  peak_db={}, rejected=rejected) for i, s in enumerate(starts, 1)])

    earlier = take(int(5.7 * SR), int(9 * SR), rejected=True)
    now = take(int(1.5 * SR), int(9 * SR))  # found differently: h1 is another hit now, h2 the same
    carry_over(uke, now, earlier)
    assert [h.rejected for h in now.hits] == [False, True]


def test_cuts_set_by_hand_replace_the_found_ones_until_reset(uke):
    take = lane(uke, "p")
    path = write(uke, take.files[0], take_audio(take, (0.3, 0.32)), in_app=True)
    found = result(process(uke), take.key)
    assert len(found.hits) == 2 and not found.cut_by_hand
    takes_meta.save_cuts(uke.meta.slug, path, [(int(1.0 * SR), int(2.0 * SR)), (found.hits[1].start, found.hits[1].end)])
    by_hand = result(process(uke), take.key)
    assert by_hand.cut_by_hand and [(h.start, h.end) for h in by_hand.hits][0] == (SR, 2 * SR)
    takes_meta.save_cuts(uke.meta.slug, path, None)
    again = result(process(uke), take.key)
    assert not again.cut_by_hand and [h.start for h in again.hits] == [h.start for h in found.hits]


@pytest.mark.parametrize("touch_s", [0.08, 0.012, 0.0])
def test_a_hit_starts_at_its_attack_not_at_the_touch_before_it(uke, touch_s):
    take = lane(uke, "p")
    rng = np.random.default_rng(7)
    audio = rng.normal(0, 10 ** (-75 / 20), int(7.5 * SR))
    pluck_at = int(1.56 * SR) + 37  # not on a frame boundary
    touch_at = pluck_at - int(touch_s * SR)
    audio[touch_at:pluck_at] += rng.normal(0, 10 ** (-58 / 20), pluck_at - touch_at)
    tone = pluck(440 * 2 ** ((take.note.midi - 69) / 12), 0.3, 2.0, SR, rng)
    audio[pluck_at: pluck_at + len(tone)] += tone
    write(uke, take.files[0], audio, in_app=True)
    (hit,) = result(process(uke), take.key).hits
    assert pluck_at - 0.003 * SR <= hit.start <= pluck_at  # the sample starts with the pluck


def touched_pluck(take, pluck_at, rng):
    """A pluck at `pluck_at` with a quiet touch of the string 12 ms before it."""
    audio = rng.normal(0, 10 ** (-75 / 20), int(7.5 * SR))
    audio[pluck_at - int(0.012 * SR):pluck_at] += rng.normal(0, 10 ** (-58 / 20), int(0.012 * SR))
    tone = pluck(440 * 2 ** ((take.note.midi - 69) / 12), 0.3, 2.0, SR, rng)
    audio[pluck_at: pluck_at + len(tone)] += tone
    return audio


def test_a_start_set_by_hand_moves_forward_to_the_pluck(uke):
    take = lane(uke, "p")
    pluck_at = int(1.56 * SR) + 37
    path = write(uke, take.files[0], touched_pluck(take, pluck_at, np.random.default_rng(7)), in_app=True)
    early, late = pluck_at - int(0.03 * SR), pluck_at + int(0.5 * SR)  # dragged 30 ms early; inside the note
    takes_meta.save_cuts(uke.meta.slug, path, [(early, pluck_at + int(0.4 * SR)), (late, late + SR)])
    found = result(process(uke), take.key)
    first, second = found.hits
    assert pluck_at - 0.003 * SR <= first.start <= pluck_at and first.end == pluck_at + int(0.4 * SR)
    assert second.start == late  # no attack after it: left where it was set
    # The cut editor keeps a dragged start where slicing puts it, or as dragged if it can't read the take.
    assert slicing.hand_start(uke, found, early, pluck_at + int(0.4 * SR)) == first.start
    path.unlink()
    assert slicing.hand_start(uke, found, early, pluck_at + int(0.4 * SR)) == early


def test_a_snap_never_makes_a_cut_too_short():
    rng = np.random.default_rng(3)
    ref = rng.normal(0, 1e-4, SR)
    at = int(0.5 * SR)
    ref[at: at + SR // 4] += pluck(440, 0.5, 0.25, SR, rng)
    start = at - int(0.1 * SR)
    assert slicing.snap_to_attack(ref, SR, start, at + int(0.2 * SR)) > start
    assert slicing.snap_to_attack(ref, SR, start, at + int(0.03 * SR)) == start  # would be 32 ms long


def test_a_slow_attack_keeps_its_rise_on_a_sustained_instrument():
    """A bowed note rising over 80 ms: only the silence before it is left out."""
    rng = np.random.default_rng(5)
    ref = rng.normal(0, 10 ** (-80 / 20), SR)
    at, rise = int(0.5 * SR), int(0.08 * SR)
    t = np.arange(SR - at) / SR
    ref[at:] += 0.5 * np.sin(2 * np.pi * 220 * t) * np.minimum(1, (np.arange(SR - at) / rise) ** 2)
    early = at - int(0.05 * SR)
    plucked = slicing.snap_to_attack(ref, SR, early, SR)
    sustained = slicing.snap_to_attack(ref, SR, early, SR, slicing.SUSTAINED_RISE_DB)
    assert plucked > at + 0.02 * SR  # a pluck's rule cuts 20 ms into the bow's rise
    assert at - 0.003 * SR <= sustained <= at + 0.01 * SR
    assert slicing.rise_db(uke_type(InstrumentType.SUSTAINED)) == slicing.SUSTAINED_RISE_DB
    assert slicing.rise_db(uke_type(InstrumentType.PLUCKED)) == slicing.MAIN_RISE_DB


def uke_type(kind):
    return defaults.new_instrument("Uke", kind)


def test_a_soft_hit_over_a_louder_tail_starts_at_its_rise():
    rng = np.random.default_rng(9)
    n = 2 * SR
    t = np.arange(n) / SR
    ref = 3.0 * np.exp(-3 * t) * np.sin(2 * np.pi * 196 * t)  # 0.15 when the hit comes
    at = SR
    ref[at:] += pluck(330, 0.12, 1.0, SR, rng)  # less than 6 dB above the tail
    begins = at - 3 * slicing.HOP  # the onset found frames early
    assert at - 0.003 * SR <= slicing.attack_start(ref, SR, begins) <= at + 0.003 * SR


def test_a_hit_cut_by_hand_keeps_its_decisions_when_its_start_snaps_far(uke):
    take = lane(uke, "p")
    pluck_at = int(1.56 * SR) + 37
    path = write(uke, take.files[0], touched_pluck(take, pluck_at, np.random.default_rng(7)), in_app=True)
    early = pluck_at - int(0.1 * SR)  # set before starts snapped: 100 ms early
    takes_meta.save_cuts(uke.meta.slug, path, [(early, pluck_at + SR)])
    m = process(uke)
    (hit,) = result(m, take.key).hits
    assert hit.start > early + slicing.SAME_CUT_S * SR
    hit.start, hit.rejected = early, True  # as sliced before starts set by hand snapped
    assert result(process(uke, m), take.key).hits[0].rejected


def test_a_rejected_recording_only_warns_when_its_velocity_keeps_nothing(uke):
    from vinst.preset import map_samples

    p = lane(uke, "p")
    write(uke, p.files[0], take_audio(p, (0.3, 0.32)), in_app=True)
    write(uke, more_file(p, 1, p.signals[0]), take_audio(p, (0.3, 0.31), seed=9), in_app=True)
    m = process(uke)
    more = next(t for t in m.takes if t.origin == "more")
    for h in more.hits:
        h.rejected = True
    assert not [w for w in map_samples(uke, m).warnings if "rejected" in w]  # p keeps its own take's hits
    for h in result(m, p.key).hits:
        h.rejected = True
    (warning,) = [w for w in map_samples(uke, m).warnings if "rejected" in w]
    assert warning.startswith(f"“{p.title}”: all its hits are rejected")


def test_results_remember_which_hit_finding_made_them(uke):
    from vinst.slicing import HIT_FINDER

    p = lane(uke, "p")
    write(uke, p.files[0], take_audio(p, (0.3, 0.32)), in_app=True)
    m = process(uke)
    assert m.finder == HIT_FINDER
    m.finder = 0  # sliced before hits were found as they are now
    f = lane(uke, "f")
    write(uke, f.files[0], take_audio(f, (0.6, 0.62)), in_app=True)
    assert process_one(uke, m, f.key).finder == 0  # one recording sliced again; the others are still older


def test_the_first_sample_is_sliced_as_set_now(uke):
    from vinst.slicing import HIT_FINDER

    m = process(uke).model_copy(update={"finder": 0, "cleanup": "denoise 12"})  # nothing recorded yet
    assert not slicing.has_samples(m)
    p = lane(uke, "p")
    write(uke, p.files[0], take_audio(p, (0.3, 0.32)), in_app=True)
    again = process_one(uke, m, p.key)
    assert slicing.has_samples(again)
    assert (again.finder, again.cleanup) == (HIT_FINDER, slicing.cleanup_setup(uke))


def test_a_tail_ends_before_the_next_sound_not_at_its_attack():
    rng = np.random.default_rng(5)
    ref = rng.normal(0, 1e-4, 5 * SR)
    t = np.arange(int(0.3 * SR)) / SR
    strum = 0.5 * rng.normal(0, 1, len(t)) * np.exp(-25 * t)  # a muted strum, gone in ~0.2 s
    ref[int(0.5 * SR): int(0.5 * SR) + len(t)] += strum
    ref[int(2.9 * SR): int(3.0 * SR)] += rng.normal(0, 0.01, int(0.1 * SR))  # the hand moving in
    ref[int(3.0 * SR): int(3.0 * SR) + len(t)] += strum
    onsets = [int(0.5 * SR), int(3.0 * SR)]
    (_, end, _), _ = cut_points(ref, SR, onsets, -80, begins={onsets[1]: int(2.9 * SR)})
    assert end < 1.0 * SR  # with the strum, not running on into the hand before the next one
    (_, longer, _), _ = cut_points(ref, SR, onsets, -80)
    assert longer > 2.8 * SR  # without knowing where the next sound begins


def test_a_new_take_is_sliced_with_all_others_while_they_carry_the_old_low_cut(uke):
    p, f = lane(uke, "p"), lane(uke, "f")
    write(uke, p.files[0], take_audio(p, (0.3, 0.32)), in_app=True)
    m = process(uke).model_copy(update={"cleanup": "low cut 0.75 12"})  # sliced with the cut in them
    write(uke, f.files[0], take_audio(f, (0.6, 0.62)), in_app=True)
    again = process_one(uke, m, f.key)
    assert again.cleanup == "" and result(again, f.key).hits  # all sliced again, none cut


def test_a_take_edited_without_moving_a_hit_is_cut_where_it_was_when_building(uke):
    from scipy.signal import butter, sosfiltfilt

    take = lane(uke, "p")
    path = write(uke, take.files[0], take_audio(take, (0.2, 0.25)))
    sliced = process(uke)
    result(sliced, take.key).hits[1].rejected = True  # a decision made in the worksheet
    review.save(sliced, store.manifest_path(uke.meta.slug))
    before = [(h.start, h.end) for h in result(sliced, take.key).hits]
    # An EQ in an audio editor: same length, same performance, other sound.
    audio, _ = sf.read(path)
    sf.write(path, 0.8 * sosfiltfilt(butter(2, 3000, "lowpass", fs=SR, output="sos"), audio), SR, subtype="PCM_24")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
    manifest = review.load(store.manifest_path(uke.meta.slug))
    assert slicing.edited_takes(uke, manifest) == [take.key]
    built = bundle.build(uke)  # no slicing asked for
    assert any("cut again where they were cut" in line for line in built.info)
    after = review.load(store.manifest_path(uke.meta.slug))
    again = result(after, take.key)
    assert [(h.start, h.end) for h in again.hits] == before and again.hits[1].rejected
    assert slicing.changed_takes(uke, after) == []


def test_an_empty_take_is_recorded_again_not_added_to(uke, monkeypatch):
    from types import SimpleNamespace

    from vinst.ui import recording

    take = lane(uke)
    takes = store.instrument_dir(uke.meta.slug).parent.parent / "takes"
    opened = []
    monkeypatch.setattr(recording, "open_dialog", lambda ed, title, *a, **k: opened.append(title))
    monkeypatch.setattr(recording, "record_more", lambda ed, key, *a, **k: opened.append("more"))
    ed = SimpleNamespace(inst=uke)
    sf.write(takes / take.files[0], np.zeros(0), SR)  # e.g. a cancelled export
    recording.record_take(ed, take.number)
    sf.write(takes / take.files[0], take_audio(take, (0.5,)), SR)
    recording.record_take(ed, take.number)
    assert opened == [take.title, "more"]


def test_split_on_the_command_line_says_why_a_file_cant_be_used(uke, tmp_path):
    from typer.testing import CliRunner

    from vinst.cli import app

    sf.write(tmp_path / "scale_pickup.wav", np.zeros(0), SR)
    result = CliRunner().invoke(app, ["split", uke.meta.slug, "p", str(tmp_path / "scale_pickup.wav")])
    assert result.exit_code == 1 and "scale_pickup.wav is empty" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_a_dropped_file_whose_signal_isnt_named_waits_to_be_asked(uke):
    """With signals recorded together, a file without a signal in its name (e.g. a stereo export, a
    channel per signal) is kept for the worksheet to ask which is which; then each is stored."""
    import asyncio

    from conftest import piezo_and_mic
    from vinst.split import Source, load_sources
    from vinst.ui.worksheet import DROP_BATCH, drop_file, long_upload_dir, store_take_audio

    piezo_and_mic(uke)
    store.save(uke)
    take = lane(uke, "p")

    class Body:
        headers = {"X-Vinst-Drop": "1"}

        def __init__(self, data):
            self.data = data

        async def body(self):
            return self.data

    left, right = take_audio(take, (0.7, 0.7)), take_audio(take, (0.35, 0.1))
    buf = io.BytesIO()
    sf.write(buf, np.stack([left, right], axis=1), SR, format="WAV", subtype="PCM_24")
    asked = asyncio.run(drop_file(uke.meta.slug, take.key, Body(buf.getvalue()), "export.wav", "123"))
    assert asked["ask"] and not asked["ok"]
    kept = long_upload_dir(uke.meta.slug, f"{DROP_BATCH}123") / "export.wav"
    assert kept.is_file()
    signals, sr = load_sources([kept], [Source(0, 1), Source(0, 0)])  # mic on the left, here
    for sig, audio in zip(["piezo", "mic"], signals):
        assert store_take_audio(uke, take, sig, audio, sr, "export.wav")["ok"]
    takes = store.instrument_dir(uke.meta.slug).parent.parent / "takes"
    piezo, mic = (sf.read(takes / f)[0] for f in take.files)
    assert np.max(np.abs(mic)) == pytest.approx(2 * np.max(np.abs(piezo)), rel=0.05)
    m = slicing.process(uke)
    result = next(t for t in m.takes if t.key == take.key)
    assert set(result.envelopes) == {"piezo", "mic"}  # each its own waveform in the worksheet
    assert result.envelope_of("mic") != result.envelope_of("piezo")
    named = asyncio.run(drop_file(uke.meta.slug, take.key, Body(buf.getvalue()), "export_mic.wav", "124"))
    assert named["ok"] and named["signal"] == "mic"  # its name says: no question
    both = asyncio.run(drop_file(uke.meta.slug, take.key, Body(buf.getvalue()), "export piezo+mic.wav", "125"))
    assert both["ask"]  # a channel each, but which is which?


def test_deleting_a_recording_takes_its_samples_and_decisions_and_keeps_the_others(uke, trash_bin):
    take, other = lane(uke, "f"), lane(uke, "p")
    first = write(uke, take.files[0], take_audio(take, (0.7, 0.72)), in_app=True)
    more = write(uke, more_file(take, 1, take.signals[0]), take_audio(take, (0.69, 0.71), seed=9))
    kept = write(uke, other.files[0], take_audio(other, (0.2, 0.25)))
    manifest = process(uke)
    more_key = f"{take.base_name}-more1"
    samples = [Path(manifest.samples_dir) / f for r in manifest.takes if r.key in (take.key, more_key)
               for h in r.hits for f in h.files.values()]
    kept_samples = [Path(manifest.samples_dir) / f for h in result(manifest, other.key).hits for f in h.files.values()]
    result(manifest, take.key).hits[0].rejected = True

    found = discard.find(uke, manifest, {take.key, more_key})
    assert sorted(found.files) == sorted([first, more]) and found.size > 0 and found.samples_size > 0
    after, _ = discard.delete(uke, manifest, {take.key, more_key})
    assert not first.exists() and not more.exists() and kept.exists()
    assert (trash_bin / first.name).exists()
    assert not any(p.exists() for p in samples) and all(p.exists() for p in kept_samples)
    assert result(after, take.key).status == "missing" and not result(after, take.key).hits
    assert all(t.key != more_key for t in after.takes)  # not a planned recording: gone, not unrecorded
    assert [h.files for h in result(after, other.key).hits] == [h.files for h in result(manifest, other.key).hits]
    assert first.name not in takes_meta._load(uke.meta.slug)
    assert changed_takes(uke, after) == []  # nothing to slice again
    # Recorded again under the same name: a new recording, without the old one's rejections.
    write(uke, take.files[0], take_audio(take, (0.7, 0.72), seed=5))
    assert not any(h.rejected for h in result(process(uke, previous=after), take.key).hits)


def test_deleting_all_recordings_leaves_other_files_in_the_folder(uke, trash_bin):
    takes_dir = Path(uke.recording.takes_dir)
    for t in [lane(uke, "p"), lane(uke, "f"), lane(uke, "p", note=1)]:
        write(uke, t.files[0], take_audio(t, (0.3, 0.35)))
    gone_signal = write(uke, f"{lane(uke, 'p').note_base}_p_mic.wav", take_audio(lane(uke, "p"), (0.3,)))
    (takes_dir / "notes.txt").write_text("mine")
    (takes_dir / "my-scale.wav").write_bytes(b"not the instrument's")
    manifest = process(uke)
    sliced = list(Path(manifest.samples_dir).glob("*.wav"))
    assert sliced

    after, found = discard.delete(uke, manifest, None)
    assert gone_signal in found.files and len(found.files) == 4
    assert sorted(p.name for p in takes_dir.iterdir() if p.is_file()) == ["my-scale.wav", "notes.txt"]
    assert not any(p.exists() for p in sliced)
    assert all(r.status == "missing" for r in after.takes) and after.counts()["hits"] == 0
    assert sorted(p.name for p in trash_bin.iterdir()) == sorted(p.name for p in found.files)  # each on its own
    assert discard.find(uke, after, None).files == []


def test_without_a_manifest_the_recordings_still_go(uke, trash_bin):
    t = lane(uke, "p")
    path = write(uke, t.files[0], take_audio(t, (0.3,)))
    after, found = discard.delete(uke, None, {t.key})
    assert after is None and found.files == [path] and not path.exists()


def test_delete_all_takes_the_takes_of_notes_the_range_no_longer_has(uke, trash_bin):
    low = lane(uke, "p")
    write(uke, low.files[0], take_audio(low, (0.3,)))
    partial = Path(uke.recording.takes_dir) / f"{low.files[0]}.part"  # a recording cut short
    partial.write_bytes(b"x")
    manifest = process(uke)
    uke.range.strings = uke.range.strings[1:]  # its string, and so its notes, gone from the plan
    assert all(t.note_base != low.note_base for t in plan_takes(uke))
    after, found = discard.delete(uke, manifest, None)
    assert Path(uke.recording.takes_dir) / low.files[0] in found.files and partial in found.files
    assert not [p for p in Path(uke.recording.takes_dir).iterdir() if p.is_file()]
    assert all(t.note != low.note_base for t in after.takes)


def test_a_file_that_cant_go_keeps_its_recording(uke, trash_bin, monkeypatch):
    stuck, other = lane(uke, "p"), lane(uke, "f")
    write(uke, stuck.files[0], take_audio(stuck, (0.3, 0.35)))
    write(uke, other.files[0], take_audio(other, (0.7,)))
    manifest = process(uke)
    real = discard.trash

    def trash(path):
        if path.name == stuck.files[0]:
            raise PermissionError("locked")
        return real(path)

    monkeypatch.setattr(discard, "trash", trash)
    after, found = discard.delete(uke, manifest, {stuck.key, other.key})
    assert [p.name for p in found.failed] == [stuck.files[0]]
    assert [h.id for h in result(after, stuck.key).hits] == [h.id for h in result(manifest, stuck.key).hits]
    assert all((Path(after.samples_dir) / f).exists() for h in result(after, stuck.key).hits for f in h.files.values())
    assert result(after, other.key).status == "missing"
    assert "1 file" in discard.went(found)


def test_instruments_recording_into_one_folder_are_told(uke, tmp_path):
    other = defaults.new_instrument("Second uke", uke.meta.type)
    other.recording.takes_dir = uke.recording.takes_dir + "/"
    other = store.create(other)
    assert discard.sharing(uke) == ["Second uke"] and discard.sharing(other) == ["Uke"]
    other.recording.takes_dir = str(tmp_path / "own")
    store.save(other)
    assert discard.sharing(uke) == []


def test_on_a_disk_without_a_trash_recordings_stay_until_deleted_for_good(uke, monkeypatch):
    from vinst import trash

    t = lane(uke, "p")
    path = write(uke, t.files[0], take_audio(t, (0.3, 0.35)))
    manifest = process(uke)

    def no_trash(p):
        raise trash.NoTrash(f"{p.name}: its disk has no Trash")

    monkeypatch.setattr(trash, "_system_trash", no_trash)
    kept, found = discard.delete(uke, manifest, {t.key})
    assert found.no_trash == [path] and path.exists() and not found.failed
    assert [h.id for h in result(kept, t.key).hits] == [h.id for h in result(manifest, t.key).hits]
    after, found = discard.delete(uke, kept, {t.key}, for_good=True)
    assert not path.exists() and found.for_good == 1 and result(after, t.key).status == "missing"
    assert discard.went(found) == "1 file (" + discard.size_text(found.size) + ") deleted."


def test_an_unexpected_error_keeps_that_file_and_the_others_still_go(uke, monkeypatch):
    from vinst import trash

    stuck, other = lane(uke, "p"), lane(uke, "f")
    stuck_path = write(uke, stuck.files[0], take_audio(stuck, (0.3,)))
    other_path = write(uke, other.files[0], take_audio(other, (0.7,)))
    manifest = process(uke)
    to_bin = trash._system_trash

    def system_trash(p):
        if p == stuck_path:
            raise ValueError("not an OSError")
        return to_bin(p)

    monkeypatch.setattr(trash, "_system_trash", system_trash)
    after, found = discard.delete(uke, manifest, {stuck.key, other.key})
    assert found.failed == [stuck_path] and stuck_path.exists() and not other_path.exists()
    assert result(after, stuck.key).hits and result(after, other.key).status == "missing"


def test_after_deleting_trashed_and_deleted_files_are_told_apart(tmp_path):
    a, b, c = tmp_path / "a.wav", tmp_path / "b.wav", tmp_path / "c.wav"
    found = discard.Found(files=[a, b, c], sizes={a: 2_000_000, b: 3_000_000, c: 5_000_000}, trashed=1, for_good=1,
                          failed=[c])
    assert discard.went(found) == "2 files (5 MB): 1 moved to the Trash (empty it to free the space), 1 deleted."
    found = discard.Found(files=[a], sizes={a: 2_000_000}, for_good=1)
    assert discard.went(found) == "1 file (2 MB) deleted."
    found = discard.Found(files=[a, b], sizes={a: 2_000_000, b: 3_000_000}, trashed=2)
    assert discard.went(found) == "2 files (5 MB) moved to the Trash; empty it to free the space."
