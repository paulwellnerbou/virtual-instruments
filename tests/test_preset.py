import zipfile
from pathlib import Path

import numpy as np
import pytest
from conftest import piezo_and_mic
from lxml import etree

from vinst import bundle, defaults, preset, review, store
from vinst.plan import layer_bounds
from vinst.slicing import process
from vinst.spec import InstrumentType
from vinst.synth import Mistakes, write_takes
from vinst.validate import validate_bundle


def test_layer_bounds_cover_all_velocities():
    assert layer_bounds(3) == [(1, 42), (43, 85), (86, 127)]
    assert layer_bounds(1) == [(1, 127)]


def test_missing_layer_borrows_from_below_then_above():
    bounds = [("p", 1, 42), ("mf", 43, 85), ("f", 86, 127)]
    assert preset.velocity_slots(bounds, {"p", "f"}) == [("p", 1, 85), ("f", 86, 127)]
    assert preset.velocity_slots(bounds, {"f"}) == [("f", 1, 127)]
    assert preset.velocity_slots(bounds, {"mf", "f"}) == [("mf", 1, 85), ("f", 86, 127)]


def test_gaps_are_played_by_the_note_above_pitched_down():
    assert preset.key_zones([60, 61], 0, 0) == {60: (60, 60), 61: (61, 61)}
    assert preset.key_zones([60, 64], 0, 0) == {60: (60, 60), 64: (61, 64)}
    assert preset.key_zones([60, 63], 2, 3) == {60: (58, 60), 63: (61, 66)}  # only the top one goes up


@pytest.fixture
def built(tmp_path, monkeypatch):
    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VINST_BUILD", str(tmp_path / "build"))
    monkeypatch.setenv("VINST_DS_LIBRARY", str(tmp_path / "library"))
    inst = piezo_and_mic(defaults.new_instrument("Test Uke", InstrumentType.PLUCKED))
    for s in inst.range.strings:
        s.frets = 1  # 8 notes
    inst.dynamics.round_robins = 2
    inst.recording.ring_seconds = 1.0
    inst.recording.takes_dir = str(tmp_path / "takes")
    store.create(inst)
    # take 2 (G2 p): a wrong note; take 4 (G2 f): one variation missing
    write_takes(inst, tmp_path / "takes", ring=1.0, mistakes=Mistakes(wrong_note_take=2, missing_hit_take=4))
    manifest = process(inst)
    manifest.takes[1].hits[1].rejected = True  # the wrong note: G2 p rr2
    review.save(manifest, store.manifest_path(inst.meta.slug))
    return inst, manifest


def samples(result, group_name):
    doc = etree.parse(str(result.preset_file))
    return [s.attrib for g in doc.iter("group") if g.get("name") == group_name for s in g.iter("sample")]


def test_build_is_valid_and_covers_the_range(built):
    inst, _ = built
    result = bundle.build(inst)
    assert result.report.ok, result.report.errors
    piezo = samples(result, "Piezo")
    keys = {k for s in piezo for k in range(int(s["loNote"]), int(s["hiNote"]) + 1)}
    # Recorded: G2 G#2 C3 C#3 E3 F3 A3 A#3; gaps between strings are covered.
    assert keys == set(range(55, 71))
    assert {s["rootNote"] for s in piezo} == {"55", "56", "60", "61", "64", "65", "69", "70"}


def test_rejected_hit_is_left_out_and_round_robins_renumbered(built):
    inst, manifest = built
    rejected = manifest.takes[1].hits[1]
    result = bundle.build(inst)
    piezo = samples(result, "Piezo")
    assert not any(s["path"].endswith(rejected.files["piezo"]) for s in piezo)
    p_layer = [s for s in piezo if s["rootNote"] == "55" and s["loVel"] == "1"]
    assert [(s["seqPosition"], s["seqLength"]) for s in p_layer] == [("1", "1")]


def test_tuning_correction_modes(built):
    inst, manifest = built
    hit = manifest.takes[2].hits[0]
    assert preset.tuning_for(inst, hit) == pytest.approx(-hit.cents / 100, abs=1e-4)
    inst.preset.tuning = "partial"
    inst.preset.tuning_amount = 0.5
    assert preset.tuning_for(inst, hit) == pytest.approx(-hit.cents / 200, abs=1e-4)
    inst.preset.tuning = "off"
    assert preset.tuning_for(inst, hit) == 0


def test_extras_become_release_and_key_groups(built):
    inst, _ = built
    result = bundle.build(inst)
    doc = etree.parse(str(result.preset_file))
    release = [g for g in doc.iter("group") if g.get("trigger") == "release"]
    assert len(release) == 2  # one per signal
    assert {s.get("loNote") for s in release[0].iter("sample")} == {"55"}
    fx = {g.get("name"): {s.get("rootNote") for s in g.iter("sample")} for g in doc.iter("group")
          if g.get("name", "").startswith("Piezo") and "fx" in g.get("tags", "").split(",")}
    assert fx == {"Piezo Chuck / muted strum": {"48"}, "Piezo Body knock": {"50"}}  # each in its own group


def test_a_chuck_stops_ringing_notes_and_plays_its_hits_in_turn(built):
    inst, _ = built
    result = bundle.build(inst)
    assert result.report.ok, result.report.errors  # the schema knows silencedByTags and silencingDecay
    doc = etree.parse(str(result.preset_file))
    groups = {g.get("name"): g for g in doc.iter("group")}
    chuck, knock, notes = groups["Piezo Chuck / muted strum"], groups["Piezo Body knock"], groups["Piezo"]
    assert preset.MUTE_TAG in chuck.get("tags").split(",") and preset.MUTE_TAG not in knock.get("tags").split(",")
    assert notes.get("silencedByTags") == preset.MUTE_TAG and float(notes.get("silencingDecay")) > 0
    assert chuck.get("seqMode") == "round_robin"  # recorded down, up, down…: played that way
    inst.extras = [x.model_copy(update={"mutes": False}) for x in inst.extras]
    doc = etree.parse(str(bundle.build(inst).preset_file))
    assert all(g.get("silencedByTags") is None for g in doc.iter("group"))


def test_older_instruments_get_a_muting_chuck():
    from vinst.spec import Extra

    assert Extra.model_validate({"id": "chuck", "name": "Chuck"}).mutes
    assert not Extra.model_validate({"id": "knock", "name": "Knock"}).mutes
    assert not Extra.model_validate({"id": "chuck", "name": "Chuck", "mutes": False}).mutes


def test_neighbour_round_robins_are_valid(built):
    inst, _ = built
    inst.preset.neighbour_round_robins = 1
    result = bundle.build(inst)
    assert result.report.ok, result.report.errors
    piezo = samples(result, "Piezo")
    on_56 = [s for s in piezo if s["loNote"] == "56" and s["loVel"] == "43"]  # mf layer on G#2
    assert {s["rootNote"] for s in on_56} == {"55", "56"}  # its own hits plus G2's


def test_validator_catches_broken_presets(built, tmp_path):
    inst, _ = built
    result = bundle.build(inst)
    text = result.preset_file.read_text()
    broken = text.replace('parameter="ENV_RELEASE"', 'parameter="GROUP_VOLUME"', 1)
    broken = broken.replace('seqPosition="2"', 'seqPosition="3"', 1)
    result.preset_file.write_text(broken)
    (result.bundle / "Samples" / next(iter(sorted((result.bundle / "Samples").iterdir()))).name).unlink()
    report = validate_bundle(result.bundle, result.preset_file)
    assert any("GROUP_VOLUME" in e and "AMP_VOLUME" in e for e in report.errors)
    assert any("round robins" in e for e in report.errors)
    assert any("missing sample file" in e for e in report.errors)


def test_install_package_and_foreign_bundle_protection(built, tmp_path):
    inst, _ = built
    result = bundle.build(inst)
    target = bundle.install(result.bundle)
    assert (target / f"{result.bundle.stem}.dspreset").is_file()
    out = bundle.package(inst, result.bundle)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert all(n.startswith("Test Uke.dsbundle/") for n in names)
    assert not any(n.endswith(bundle.MARKER) for n in names)
    (target / bundle.MARKER).unlink()
    with pytest.raises(ValueError, match="wasn't created by this tool"):
        bundle.install(result.bundle)


def test_rebuild_removes_samples_no_longer_used(built):
    inst, manifest = built
    result = bundle.build(inst)
    hit = manifest.takes[2].hits[0]
    used = result.bundle / "Samples" / hit.files["piezo"]
    assert used.exists()
    hit.rejected = True
    review.save(manifest, store.manifest_path(inst.meta.slug))
    bundle.build(inst)
    assert not used.exists()


@pytest.fixture
def two_passes(tmp_path, monkeypatch):
    """Piezo and internal mic share one output, so the mic is recorded in its own pass."""
    monkeypatch.setenv("VINST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VINST_BUILD", str(tmp_path / "build"))
    monkeypatch.setenv("VINST_DS_LIBRARY", str(tmp_path / "library"))
    inst = piezo_and_mic(defaults.new_instrument("Pass Uke", InstrumentType.PLUCKED))
    inst.signals[1].recorded = "separate"
    for s in inst.range.strings:
        s.frets = 0
    inst.dynamics.round_robins = 2
    inst.extras = inst.extras[:1]
    inst.recording.ring_seconds = 1.0
    inst.recording.takes_dir = str(tmp_path / "takes")
    store.create(inst)
    write_takes(inst, tmp_path / "takes", ring=1.0, mistakes=Mistakes())
    review.save(process(inst), store.manifest_path(inst.meta.slug))
    return inst


def test_separate_pass_doubles_the_takes(two_passes):
    from vinst.plan import plan_takes, summarize

    takes = plan_takes(two_passes)
    assert len({t.key for t in takes}) == len(takes) == 2 * (1 + 4 * 3 + 1)  # noise floor, 4 notes × 3 lanes, extra
    mic = [t for t in takes if t.pass_id == "mic"]
    assert mic[1].files == ["055_G-0_p_mic.wav"] and mic[1].title.endswith("· Internal mic")
    assert summarize(two_passes).sampled == 4


def test_separate_passes_slice_independently(two_passes):
    m = review.load(store.manifest_path(two_passes.meta.slug))
    ids = [h.id for t in m.takes for h in t.hits]
    assert len(ids) == len(set(ids))
    g = [t for t in m.takes if t.base == "055_G-0_p"]
    assert {t.pass_id for t in g} == {"main", "mic"} and all(t.status == "ok" for t in g)
    assert set(g[1].hits[0].files) == {"mic"}


def test_switch_mode_builds_a_sound_menu(two_passes):
    result = bundle.build(two_passes)
    assert result.report.ok, result.report.errors
    doc = etree.parse(str(result.preset_file))
    menu = doc.find(".//menu")
    assert [o.get("name") for o in menu.iter("option")] == ["Piezo", "Internal mic"]
    groups = {g.get("name"): g for g in doc.iter("group")}
    assert groups["Piezo"].get("enabled") is None and groups["Internal mic"].get("enabled") == "false"
    assert "pass-mic" in groups["Internal mic"].get("tags")
    assert not any(k.get("label") == "Mix" for k in doc.iter("labeled-knob"))


def test_layer_mode_mixes_the_passes(two_passes):
    two_passes.preset.pass_mode = "layer"
    result = bundle.build(two_passes)
    assert result.report.ok, result.report.errors
    doc = etree.parse(str(result.preset_file))
    assert doc.find(".//menu") is None
    assert any(k.get("label") == "Mix" for k in doc.iter("labeled-knob"))
    assert all(g.get("enabled") is None for g in doc.iter("group"))


def test_build_refuses_hits_sliced_under_another_setup(built):
    inst, _ = built
    moved = inst.model_copy(deep=True)
    moved.signals[1].recorded = "separate"
    with pytest.raises(ValueError, match="Slice again"):
        bundle.build(moved)
    renamed = inst.model_copy(deep=True)
    renamed.dynamics.layers = ["soft", "mf", "f"]  # a lane renamed: only its takes are new to record
    result = bundle.build(renamed)
    assert result.report.ok and any("not in the current plan" in w for w in result.warnings)
    styled = inst.model_copy(deep=True)
    styled.preset.reverb = 0.5  # sound settings don't affect slicing
    assert bundle.build(styled).report.ok


def test_extra_keys_inside_the_range_are_reported():
    inst = defaults.new_instrument("Bari", InstrumentType.PLUCKED)
    inst.range.strings = defaults.tuning_strings("Baritone ukulele (D G B E)")
    assert any("Body knock" in p and "inside the playable range" in p for p in inst.problems())
    inst.extras[2].key = 40
    assert not any("inside the playable range" in p for p in inst.problems())


def test_reserved_signal_ids_are_reported():
    inst = defaults.new_instrument("X", InstrumentType.PLUCKED)
    inst.signals[0].id = "fx"
    assert any("reserved" in p for p in inst.problems())


def test_tone_default_sets_the_filter_and_cc_follows_knob_range(built):
    inst, _ = built
    inst.preset.tone = 0.5
    doc = etree.parse(str(bundle.build(inst).preset_file))
    assert doc.find(".//effect[@type='lowpass']").get("frequency") == "1100"
    inst.preset.controls = ["release", "reverb"]
    doc = etree.parse(str(bundle.build(inst).preset_file))
    cc = doc.find(".//cc/binding")
    assert (cc.get("translationOutputMin"), cc.get("translationOutputMax")) == ("0", "8")


def test_many_knobs_fit_in_one_row():
    for n in range(1, 13):
        x0, step, size = preset.knob_layout(n)
        assert x0 >= 16 - 1e-9 and x0 + n * step <= preset.UI_W - 16 + 1e-9 and size > 0
    assert preset.knob_layout(5)[2] == preset.KNOB_W


def test_dashes_in_name_and_rename_cleanup(built):
    inst, _ = built
    first = bundle.install(bundle.build(inst).bundle)
    inst.meta.name = "Test -- Uke"
    second_build = bundle.build(inst)
    assert second_build.report.ok
    assert [p.name for p in second_build.bundle.parent.glob("*.dsbundle")] == ["Test -- Uke.dsbundle"]
    second = bundle.install(second_build.bundle)
    assert second.exists() and not first.exists()


def test_velocity_layers_start_where_chosen(built):
    inst, _ = built
    assert layer_bounds(3) == [(1, 42), (43, 85), (86, 127)]
    assert layer_bounds(3, [30, 100]) == [(1, 29), (30, 99), (100, 127)]
    assert layer_bounds(3, [100]) == layer_bounds(3)  # doesn't fit three layers: even split
    inst.preset.velocity_splits = [20, 110]
    result = bundle.build(inst)
    vels = {(s["loVel"], s["hiVel"]) for s in samples(result, "Piezo") if s["rootNote"] == "64"}
    assert vels == {("1", "19"), ("20", "109"), ("110", "127")}


def test_quiet_recordings_play_louder_by_how_loud_their_notes_are(built):
    inst, manifest = built
    before = preset.map_samples(inst, manifest).gain_db
    for t in manifest.takes:  # as if recorded 12 dB lower
        for h in t.hits:
            h.rms_db -= 12
    mapping = preset.map_samples(inst, manifest)
    assert mapping.gain_db == pytest.approx(before + 12, abs=0.11)
    xml = etree.fromstring(preset.render(inst, mapping))
    assert xml.find("groups").get("volume") == f"{mapping.gain_db:.1f}dB"


def test_the_limiter_holds_a_click_under_the_ceiling_and_leaves_the_note():
    from vinst.level import CEILING_DB, limit

    sr = 48000
    t = np.arange(sr) / sr
    note = (0.05 * np.sin(2 * np.pi * 440 * t) * np.exp(-3 * t))[:, None]
    note[100:110] = 0.5  # a 0.2 ms click, 20 dB above the note
    out, reduced = limit(note, sr, 12.0)
    assert np.max(np.abs(out)) <= 10 ** (CEILING_DB / 20) + 1e-9 and reduced == pytest.approx(7, abs=0.1)
    later = slice(int(0.1 * sr), sr)  # the note away from the click: just 12 dB louder
    assert np.allclose(out[later], note[later] * 10 ** (12 / 20))


def test_the_bundle_plays_quiet_samples_raised(built, monkeypatch):
    import soundfile as sf

    inst, manifest = built
    monkeypatch.setattr(preset, "TARGET_LOUDNESS_DB", 0.0)  # far louder than the test takes
    result = bundle.build(inst)
    assert any(i.startswith("Level raised") and "held under -1 dBFS" in i for i in result.info)
    xml = etree.parse(str(result.preset_file))
    assert xml.find("groups").get("volume") is None  # it's in the samples now
    s = next(iter(xml.iter("sample")))
    path = s.get("path").split("/")[-1]
    src, _ = sf.read(str(Path(manifest.samples_dir) / path))
    out, _ = sf.read(str(result.bundle / preset.SAMPLES_DIR / path))
    assert np.max(np.abs(out)) > np.max(np.abs(src)) and np.max(np.abs(out)) <= 10 ** (-1 / 20) + 1e-4


def test_a_borrowed_hit_counts_once_for_the_level(built):
    inst, manifest = built
    alone = preset.map_samples(inst, manifest).gain_db
    inst.preset.neighbour_round_robins = 2  # every hit now plays on several keys
    assert preset.map_samples(inst, manifest).gain_db == alone


def test_the_low_cut_is_applied_when_building(built, monkeypatch):
    import soundfile as sf

    from vinst import lowcut

    inst, manifest = built
    inst.processing.low_cut = True
    monkeypatch.setattr(inst.processing, "low_cut_db", 20.0)
    result = bundle.build(inst)
    xml = etree.parse(str(result.preset_file))
    s = next(iter(xml.iter("sample")))
    name = s.get("path").split("/")[-1]
    src, sr = sf.read(str(Path(manifest.samples_dir) / name))
    out, _ = sf.read(str(result.bundle / preset.SAMPLES_DIR / name))

    def below(x, root):  # share of energy under the cut
        spec = np.abs(np.fft.rfft(x)) ** 2
        f = np.fft.rfftfreq(len(x), 1 / sr)
        return spec[f < lowcut.frequency(root, 0.6)].sum() / spec.sum()

    root = int(s.get("rootNote"))
    assert below(out, root) < below(src, root)  # the sliced sample stays, the bundle's copy is cut


def _riff(data: bytes, at: int, end: int) -> dict[bytes, list[bytes]]:
    import struct

    out: dict[bytes, list[bytes]] = {}
    while at < end:
        tag, size = data[at:at + 4], struct.unpack("<I", data[at + 4:at + 8])[0]
        assert size % 2 == 0, f"{tag!r} has an odd length: readers like MuseScore's lose their place after it"
        out.setdefault(tag, []).append(data[at + 8:at + 8 + size])
        at += 8 + size
    assert at == end
    return out


def test_the_build_writes_a_valid_soundfont(built):
    import struct

    inst, _ = built
    result = bundle.build(inst)
    data = result.soundfont.read_bytes()
    assert data[:4] == b"RIFF" and data[8:12] == b"sfbk" and struct.unpack("<I", data[4:8])[0] == len(data) - 8
    lists = {c[:4]: _riff(c, 4, len(c)) for c in _riff(data, 12, len(data))[b"LIST"]}
    assert struct.unpack("<HH", lists[b"INFO"][b"ifil"][0]) == (2, 4)
    smpl, sm24 = lists[b"sdta"][b"smpl"][0], lists[b"sdta"][b"sm24"][0]
    assert len(sm24) == len(smpl) // 2
    pdta = {k: v[0] for k, v in lists[b"pdta"].items()}
    sizes = {b"phdr": 38, b"pbag": 4, b"pmod": 10, b"pgen": 4, b"inst": 22, b"ibag": 4, b"imod": 10,
             b"igen": 4, b"shdr": 46}
    records = {k: [pdta[k][i:i + n] for i in range(0, len(pdta[k]), n)] for k, n in sizes.items()}
    assert all(len(pdta[k]) % n == 0 for k, n in sizes.items())
    assert records[b"phdr"][-1][:3] == b"EOP" and records[b"inst"][-1][:3] == b"EOI"
    assert records[b"shdr"][-1][:3] == b"EOS"
    frames = len(smpl) // 2
    for h in records[b"shdr"][:-1]:
        start, end, loop_start, loop_end, rate, root = struct.unpack("<IIIIIB", h[20:41])
        assert start < end <= frames and start <= loop_start < loop_end <= end and rate == 48000
    gens = [struct.unpack("<HH", g) for g in records[b"igen"]]
    bags = [struct.unpack("<HH", b)[0] for b in records[b"ibag"]]
    for a, b in zip(bags, bags[1:]):
        zone = [op for op, _ in gens[a:b]]
        if 53 in zone:  # a sample's zone: its ranges first, its sample last
            assert zone[0] == 43 and zone[1] == 44 and zone[-1] == 53
    sample_ids = {amount for op, amount in gens if op == 53}
    assert sample_ids == set(range(len(records[b"shdr"]) - 1))
    assert result.soundfont.parent == result.bundle.parent and result.soundfont_samples == len(sample_ids)


def test_the_soundfont_goes_to_musescore_but_never_over_someone_elses(built, tmp_path):
    from vinst import sf2

    inst, _ = built
    font = bundle.build(inst).soundfont
    folder = tmp_path / "SoundFonts"
    with pytest.raises(ValueError, match="isn't there"):
        sf2.install(font, folder)
    folder.mkdir()
    assert sf2.install(font, folder).read_bytes() == font.read_bytes()
    assert sf2.install(font, folder)  # again over its own copy
    (folder / font.name).write_bytes(b"RIFF....sfbk someone else's")
    with pytest.raises(ValueError, match="wasn't made by this builder"):
        sf2.install(font, folder)
