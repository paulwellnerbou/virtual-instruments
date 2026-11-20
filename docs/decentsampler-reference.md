# DecentSampler format: working reference

Researched 2026-11-19. Installed plugin: **DecentSampler 1.43.2**, the current release (2026-11-18).

## Sources

- [Developer guide](https://decentsampler-developers-guide.readthedocs.io/en/latest/). Its source is [DecentSamples/DecentSampler-Developers-Guide](https://github.com/DecentSamples/DecentSampler-Developers-Guide).
- [Developer resources page](https://www.decentsamples.com/decent-sampler-developer-resources/)
- [Release notes](https://store.decentsamples.com/downloads/decent-sampler/versions)
- [DecentSampler-schema](https://github.com/DecentSamples/DecentSampler-schema) (MIT licence). It contains two useful files:
  - an XSD;
  - `engine-vocabulary.json`, which is generated from the engine source. It lists all valid binding parameters, levels, `seqMode` values and note names. **Use it to validate generated presets.**
- [Sample-Library-Examples](https://github.com/DecentSamples/DecentSampler-Sample-Library-Examples): 18 example libraries. One of them includes a Python generator (`example-017/Scripts/dx7_to_dspreset.py`).
- [DSLint](https://dslint.fly.dev/), a web linter. The plugin itself also has *File → Developer Tools → Validate Preset*, which checks XML, missing files and path case.

## Files and packaging

| File | What it is |
|---|---|
| `.dspreset` | One instrument as UTF-8 XML. Sample paths are relative to the preset. |
| `.dsbundle` | A plain folder holding presets, `Samples/`, `Images/` and `DSLibraryInfo.xml`. |
| `.dslibrary` | A zipped bundle, renamed. Dropping it on the plugin installs it into the library folder (by default `~/DecentSampler`). |
| `DSLibraryInfo.xml` | `<DecentSamplerLibraryInfo name version="x.y.z" coverArt>` with an optional `<presetMenu>`. `productId` is assigned by the store, so don't set it. |
| `.dssnapshot` | Saved control values only (1.39+). Controls are matched by index, then by type+label, so keep labels unique. |

## Gotchas that matter for a generator

- **Middle C is C3 = MIDI 60** ("as everywhere else in Decent Sampler"). Note attributes accept MIDI numbers or names. **The framework uses MIDI numbers in files and file names**, which avoids octave confusion between note-naming conventions (C3 = 60 here, C4 = 60 elsewhere).
- **Output gain.** A centred sample comes out at −5.3 dB under the default limiter. `volume="1.837"` on `<groups>` gives unity gain. The default limiter also compresses above −10 dBFS.
- **`volume` multiplies** across `<groups>`, `<group>` and `<sample>`. It accepts a linear value or `"-3dB"`.
- Groups are indexed from 0 in file order, and bindings use that index. Bindings that target `level="ui"` count *every* UI element. **Target by tag where possible.**
- **Round robins** need both `seqMode` and `seqPosition`. `seqLength` can be 0 (auto).
- **Paths.** Use forward slashes and exact case. A case mismatch breaks on Linux.
- **`<ui>`** allows only one `<tab>`. Fake tabs with buttons that toggle the `VISIBLE` of tagged controls, as the official Cavaquinho does. The recommended size is 812×375.
- A **typo in an attribute name is silently ignored.** Validating against the XSD and vocabulary catches this.

## Preset skeleton for a plucked instrument

```xml
<?xml version="1.0" encoding="UTF-8"?>
<DecentSampler minVersion="1.12.0">
  <ui width="812" height="375" bgImage="Images/background.png"> <tab name="main"> …controls… </tab> <keyboard>…</keyboard> </ui>
  <groups volume="1.837" attack="0.001" decay="25" sustain="1" release="0.4" ampVelTrack="0.3">
    <group name="mic" tags="mic,notes" seqMode="round_robin">
      <sample path="Samples/mic_v2_064_rr1.wav" rootNote="64" loNote="64" hiNote="64"
              loVel="51" hiVel="95" seqPosition="1" seqLength="4" tuning="-0.03"/>
    </group>
    <group name="di" tags="di,notes" seqMode="round_robin"> … </group>
    <group name="release-noise" trigger="release" releaseTriggerDecay="6dB"> … </group>
  </groups>
  <effects> <effect type="lowpass" frequency="22000"/> <effect type="chorus" mix="0"/> <effect type="reverb" wetLevel="0.15"/> </effects>
  <midi> <cc number="1"> <binding level="ui" type="control" position="0" parameter="VALUE"/> </cc> </midi>
  <tags> <tag name="mic" volume="0.707"/> <tag name="di" volume="0.707"/> </tags>
</DecentSampler>
```

## Sample attributes we'll use

| Area | Attributes |
|---|---|
| Mapping | `rootNote`, `loNote`/`hiNote`, `loVel`/`hiVel`, `tuning` (fractional semitones, for pitch correction) |
| Region | `start`/`end` (frames), `volume`, `pan` |
| Round robin | `seqMode="round_robin\|random\|true_random\|always"`, `seqPosition`, `seqLength` |
| Triggers | `trigger="attack\|release\|first\|legato"` |
| Release samples | `releaseTriggerDecay`, `loHoldTime`/`hiHoldTime` (1.41+) |
| Choking | `tags`, `silencedByTags`, `silencingMode="fast\|normal"`, `silencingDecay`. `<tag polyphony="1">` makes a tag monophonic. |
| Envelope | `attack`/`decay`/`sustain`/`release` and their `*Curve` (−100..100), `ampEnvEnabled="false"` for one-shots |
| Loops | `loopStart`, `loopEnd`, `loopCrossfade`, `loopCrossfadeMode`, `loopDuringRelease`. Not needed for plucks. |
| Misc | `glideTime`/`glideMode`, `pitchKeyTrack`, `globalTuning`, `globalTranspose` |

Supported sample formats: WAV, AIFF, FLAC, and REX (1.39+).

## UI and bindings

- **Controls:**
  - `labeled-knob`/`control`: `x y width height label parameterName min/max/value/defaultValue valueType style textColor tags`
  - `button` with `<state>` children
  - `menu` with `<option>` children. Its `value` is **1-based**.
  - Also `label`, `image`, `rectangle`, `line`, `xyPad`.
  - Custom knob styles via `<controlStyles>` (1.43+).
- **Binding translations:**
  - `linear` (`translationOutputMin/Max`)
  - `table` (`translationTable="0,33;0.5,1100;1,22000"`): good for tone knobs and equal-power crossfades
  - `fixed_value` (for buttons and keyswitches)
- **Common parameters:**
  - `amp`: `AMP_VOLUME`, `ENV_ATTACK|DECAY|SUSTAIN|RELEASE`, `PAN`, `AMP_VEL_TRACK`, `GLOBAL_TUNING`
  - `effect`: `FX_FILTER_FREQUENCY`, `FX_MIX`, `FX_REVERB_WET_LEVEL`, `FX_REVERB_ROOM_SIZE`
  - tags: `TAG_ENABLED`, `TAG_VOLUME`
  - `ui`: `VALUE`, `VISIBLE`
  - Wrong names to avoid: `GROUP_VOLUME`/`VOLUME` (use `AMP_VOLUME`) and `POLYPHONY` (use `TAG_POLYPHONY`).
- **Effects:**
  - Filters: `lowpass`, `lowpass_1pl`, `highpass`, `bandpass`, `notch`, `peak`, `low_shelf`, `high_shelf`
  - Gain and dynamics: `gain`, `compressor`
  - Time-based: `reverb`, `delay`, `convolution` (IR file)
  - Measured in the plug-in: `reverb` always passes its input at full level and adds the reverb (so it can't sit on a send bus alone); `convolution` scales every IR by a fixed 0.25 and `mix` crossfades input and convolution; samples route to buses with `output2Target="BUS_n"` (sample, group and tag volumes scale that send as they do the main output), and a knob sets a bus's level with `type="amp" level="bus" parameter="BUS_VOLUME"`. The round-robin position carries over between state loads.
  - Modulation: `chorus`, `phaser`
  - Other: `pitch_shift`, `wave_folder`, `wave_shaper`, `stereo_simulator`, `bit_crusher`, `stutter`

  Group-level effects are per voice and limited to filter, gain and chorus.
- **Modulators:** `<lfo>`, `<envelope>`, `<random>`, `<midiCC>`, `<midiVelocity>`. Each holds bindings with `modBehavior="add|modulate|multiply|set"`. A voice-scope `<random>` on `SAMPLE_START` adds variation cheaply.
- **MIDI:**
  - `<cc number>` bindings
  - `<note note="24-35" swallowNotes>`: keyswitches, plus triggers for strum note sequences
  - `<noteSequences>`: the strum engine, as used by the official Cavaquinho library: keyswitches trigger chord sequences with per-string offsets.

## Existing tools (checked 2026-11)

There is **no maintained Python library** for generating DecentSampler presets, so building our own is justified. Reusable pieces:

| Tool | Use for us |
|---|---|
| [DecentSampler-schema](https://github.com/DecentSamples/DecentSampler-schema) | Vendor the XSD and `engine-vocabulary.json` for validation. |
| [ConvertWithMoss](https://github.com/git-moss/ConvertWithMoss) (v20.3, mature) | Later exports of a finished instrument to SFZ, Kontakt, EXS and others. |
| [robotmitchum/sample_tools](https://github.com/robotmitchum/sample_tools) | Python. Worth reading for ideas on loop and pitch detection. |
| Plugin's built-in Auto Sampler (1.40.1+) | Samples MIDI-playable sources only, so it doesn't help with an acoustic ukulele. |
| Plugin's Preset Editor / UI Designer | Useful for hand-tweaking UI positions. Its output can be diffed back into our templates. |
