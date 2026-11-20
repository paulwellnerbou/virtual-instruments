"""Loudness by velocity, and how loud to record each velocity."""

import pytest

from vinst import aims, velocity
from vinst.preset import curve_offsets
from vinst.review import Hit, TakeResult, check_lanes

BOUNDS = [("p", 1, 80), ("f", 81, 127)]


def _hit(rms: float, id: str, layer: str | None = None) -> Hit:
    return Hit(id=id, index=0, start=0, end=1, files={}, rms_db=rms, peak_db={}, layer=layer)


def played(lo: int, hi: int, range_db: float) -> dict[int, float]:
    """dB per velocity, relative to `hi`, as DecentSampler plays the slices."""
    return {v: vol + velocity.tracking_db(v, track)
            for a, b, vol, track in velocity.slices(lo, hi, range_db) for v in range(a, b + 1)}


@pytest.mark.parametrize("range_db", [0, 12, 36, 60])
@pytest.mark.parametrize("lo, hi", [(1, 127), (1, 80), (81, 127), (100, 100)])
def test_slices_cover_the_range_and_follow_the_line(lo, hi, range_db):
    level = played(lo, hi, range_db)
    assert sorted(level) == list(range(lo, hi + 1))  # every velocity, once
    for v, db in level.items():
        assert db == pytest.approx(velocity.curve_db(v, range_db) - velocity.curve_db(hi, range_db),
                                   abs=velocity.MAX_DEVIATION_DB + 1e-9)
    steps = [level[v + 1] - level[v] for v in range(lo, hi)]
    # Rising, by about a velocity's share of the range: no step to hear.
    assert all(0 <= s <= range_db / 126 + 2 * velocity.MAX_DEVIATION_DB for s in steps)


def test_the_step_is_the_instruments_own_once_enough_notes_have_two_velocities():
    note = ({"p": [-36.0], "f": [-23.0]}, BOUNDS)  # 13 dB over 63.5 velocities
    assert aims.db_per_velocity([note, note]) == (aims.DEFAULT_DB_PER_VELOCITY, False)
    step, measured = aims.db_per_velocity([note, note, note])
    assert measured and step == pytest.approx(13 / 63.5)


def test_aims_follow_a_notes_own_split_and_its_loudest_recorded_velocity():
    step = 0.2
    targets = aims.note_aims({"f": [-20.0]}, BOUNDS, step)
    assert targets["f"] == -20.0 and targets["p"] == pytest.approx(-20.0 - 0.2 * 63.5)
    own = aims.note_aims({"f": [-20.0]}, [("p", 1, 99), ("f", 100, 127)], step)
    assert own["p"] == pytest.approx(-20.0 - 0.2 * 63.5)  # middles 50 and 113.5: as far apart
    # Only the soft one recorded: the loud one is aimed at from it.
    assert aims.note_aims({"p": [-36.0]}, BOUNDS, step)["f"] == pytest.approx(-36.0 + 0.2 * 63.5)
    assert aims.note_aims({}, BOUNDS, step) == {}


def test_hits_off_their_aim_and_uneven_variations_are_flagged():
    take = TakeResult(number=1, base="n", title="C4", kind="note", status="ok", expected_hits=5, hits=[
        _hit(-23.0, "f1", "f"), _hit(-23.5, "f2", "f"), _hit(-29.0, "f3", "f"),
        _hit(-44.0, "p1", "p"), _hit(-46.0, "p2", "p")])
    check_lanes([take], ["p", "f"], {"p": -36.0, "f": -23.0}, "f", evened_out=True)
    flags = {h.id: h.flags for h in take.hits}
    assert flags["f3"] == ["uneven"]  # 5.5 dB under its velocity's others
    assert flags["p1"] == flags["p2"] == ["off_aim"]
    assert flags["f1"] == flags["f2"] == []
    assert take.message == ("p was played softer than on the other notes, next to f (9 dB under its aim of -36 dB), "
                            "so it may sound duller. Its loudness is evened out in the instrument, so record it "
                            "again only if it sounds out of place")
    check_lanes([take], ["p", "f"], {"p": -36.0, "f": -23.0}, "f")  # loudness as recorded
    assert take.message.endswith("The step to f is also larger than on the other notes")
    assert take.message.count("than on the other notes,") == 1  # checking again replaces it


def test_an_older_off_aim_message_is_replaced():
    take = TakeResult(number=1, base="n", title="C4", kind="note", status="ok", expected_hits=2,
                      hits=[_hit(-23.0, "f1", "f"), _hit(-30.0, "p1", "p")],
                      message="p is 6 dB louder than its aim of -36 dB next to f, so it sounds like a different "
                              "dynamic than on the other notes")
    check_lanes([take], ["p", "f"], {"p": -36.0, "f": -23.0}, "f", evened_out=True)
    assert take.message.startswith("p was played harder than on the other notes") and "different dynamic" not in take.message


def test_velocities_meet_where_one_hands_over_to_the_next():
    # f recorded 21 dB over p, a soft hit among p's variations.
    notes = {60: {"p": [_hit(-44.0, "p1"), _hit(-37.0, "p2"), _hit(-43.0, "p3")],
                  "f": [_hit(-23.0, "f1"), _hit(-22.0, "f2")]}}
    offsets = curve_offsets(notes, {60: BOUNDS}, {}, range_db=36, tolerance_db=1.5)
    level = {h.id: h.rms_db + offsets[h.id] for hs in notes[60].values() for h in hs}  # at velocity 127
    p_at_80 = level["p1"] + velocity.curve_db(80, 36)
    f_at_81 = level["f1"] + velocity.curve_db(81, 36)
    assert f_at_81 - p_at_80 == pytest.approx(36 / 126, abs=1.0)  # one velocity step, not 21 dB
    # Variations keep how far they are from their median (-43), by up to ±1.5 dB: p1 -1, p2 +6 → +1.5.
    assert level["p2"] - level["p1"] == pytest.approx(2.5)
    # The loudest velocity sounds as recorded in the middle of its range.
    assert (level["f1"] + level["f2"]) / 2 + velocity.curve_db(104, 36) == pytest.approx(-22.5, abs=0.01)
