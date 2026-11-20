import numpy as np
import soundfile as sf
from lxml import etree
from test_preset import built  # noqa: F401  (fixture)

from vinst import bundle, reverb_send


def _preset(result) -> etree._ElementTree:
    return etree.parse(str(result.preset_file))


def test_off_builds_the_preset_as_before(built):
    inst, _ = built
    assert inst.preset.reverb_send is False
    first = bundle.build(inst)
    doc = _preset(first)
    assert doc.find("buses") is None and not any(s.get("output2Target") for s in doc.iter("sample"))
    assert [e.get("type") for e in doc.find("effects")][-1] == "reverb"
    assert not (first.bundle / reverb_send.IR_DIR).exists()
    assert not any("attack's hiss" in line for line in first.info)
    before = first.preset_file.read_bytes()

    inst.preset.reverb_send = True
    assert (bundle.build(inst).bundle / reverb_send.IR_DIR).is_dir()
    inst.preset.reverb_send = False
    again = bundle.build(inst)
    assert again.preset_file.read_bytes() == before
    assert not (again.bundle / reverb_send.IR_DIR).exists()


def test_on_sends_every_sample_to_a_reverb_bus_of_its_pitch(built):
    inst, _ = built
    inst.preset.reverb_send = True
    result = bundle.build(inst)
    assert result.report.ok, result.report.errors
    doc = _preset(result)
    assert "reverb" not in [e.get("type") for e in doc.find("effects")]
    buses = doc.find("buses").findall("bus")
    assert any(line.startswith(f"Reverb without the attack's hiss: {len(buses)} reverbs") for line in result.info)
    assert buses and all(float(b.get("busVolume")) == inst.preset.reverb for b in buses)
    for b in buses:
        conv = b.find("effects/effect")
        assert conv.get("type") == "convolution" and float(conv.get("mix")) == 1
        ir, rate = sf.read(str(result.bundle / conv.get("irFile")))
        assert rate == reverb_send.IR_RATE and ir.shape[1] == 2
        assert sf.info(str(result.bundle / conv.get("irFile"))).subtype == "PCM_24"
    targets = {s.get("output2Target") for s in doc.iter("sample")}
    assert None not in targets and targets <= {f"BUS_{i + 1}" for i in range(len(buses))}
    knob = next(k for k in doc.iter("labeled-knob") if k.get("label") == "Reverb")
    assert [(b.get("level"), b.get("position"), b.get("parameter")) for b in knob.findall("binding")] == [
        ("bus", str(i), "BUS_VOLUME") for i in range(len(buses))]


def test_a_sample_s_reverb_is_filtered_for_the_keys_it_plays(built):
    inst, _ = built
    inst.preset.reverb_send = True
    inst.preset.extend_above = 12  # the highest sample plays an octave above its root
    doc = _preset(bundle.build(inst))
    notes = next(g for g in doc.iter("group") if "notes" in g.get("tags", "").split(","))
    pitches = [reverb_send.sample_pitch(int(s.get("loNote")), int(s.get("hiNote"))) for s in notes.iter("sample")]
    bands = reverb_send.bands(pitches)
    top = max(notes.iter("sample"), key=lambda s: int(s.get("hiNote")))
    assert int(top.get("hiNote")) - int(top.get("rootNote")) == 12
    for s in notes.iter("sample"):
        want = reverb_send.band_of(reverb_send.sample_pitch(int(s.get("loNote")), int(s.get("hiNote"))), bands)
        assert s.get("output2Target") == f"BUS_{want + 1}"


def test_the_validator_wants_the_impulse_responses_exactly_as_named(built):
    from vinst.validate import validate_bundle

    inst, _ = built
    inst.preset.reverb_send = True
    result = bundle.build(inst)
    ir = result.bundle / reverb_send.ir_path(0)
    ir.rename(ir.with_name(ir.name.upper()))
    report = validate_bundle(result.bundle, result.preset_file)
    assert any("missing impulse response" in e for e in report.errors)


def test_on_without_a_reverb_knob_adds_nothing(built):
    inst, _ = built
    inst.preset.reverb_send = True
    inst.preset.controls = [c for c in inst.preset.controls if c != "reverb"]
    result = bundle.build(inst)
    assert _preset(result).find("buses") is None and not (result.bundle / reverb_send.IR_DIR).exists()


def test_bands_cover_every_root_within_decentsampler_s_buses():
    roots = list(range(40, 100, 2))
    bands = reverb_send.bands(roots)
    assert len(bands) <= reverb_send.MAX_BUSES
    for r in roots:
        assert sum(b.lo <= r <= b.hi for b in bands) == 1
    small = reverb_send.bands([60, 61, 62, 63, 81])
    assert [(b.lo, b.hi) for b in small] == [(60, 62), (63, 63), (81, 81)]
    assert reverb_send.band_of(100, small) == 2 and reverb_send.band_of(20, small) == 0


def test_the_band_ir_keeps_the_fundamental_and_tames_the_partials_above():
    base = reverb_send.builtin_reverb_ir(0.6, 0.9, 4.0)
    band = reverb_send.Band(63, 63)  # D#4, 311 Hz
    ir = reverb_send.band_ir(base, band) * reverb_send.CONVOLUTION_SCALE

    def level(x, hz):
        spec = np.abs(np.fft.rfft(x, 1 << 18))
        f = np.fft.rfftfreq(1 << 18, 1 / reverb_send.IR_RATE)
        return 20 * np.log10(np.mean(spec[(f > hz / 1.06) & (f < hz * 1.06)]))

    change = [level(ir[:, 0], hz) - level(base[0], hz) for hz in (311, 1245, 8000)]
    assert abs(change[0]) < 0.5 and -10 < change[1] < -7 and change[2] < -20
