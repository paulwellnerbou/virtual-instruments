# Development

The builder is a Python package (`vinst`, managed with [uv](https://docs.astral.sh/uv/)) with a [NiceGUI](https://nicegui.io) web UI and a command line.

## Running it

```bash
uv run vinst ui
```

```bash
uv run pytest
```

Other commands:

- `uv run vinst open`: what the app launcher runs: starts the server if needed and opens the browser
- `uv run vinst install-app`: installs the launcher into `~/Applications`; it always runs the builder from this checkout
- `uv run vinst list`
- `uv run vinst new "Name" --type plucked`
- `uv run vinst slice <slug>`: cuts the recorded takes into hits (or, for an instrument that imports existing samples, imports its folder), analyses them and writes `hits.json`
- `uv run vinst build <slug> [--install] [--package]`: generates and validates the `.dsbundle`; optionally installs it into `~/DecentSampler` and writes a `.dslibrary`
- `uv run vinst synth-takes <slug> <folder>`: writes fake takes, with a few deliberate mistakes, for trying out slicing and review without recording

The launcher's server listens on http://127.0.0.1:4780 and stops about two minutes after the last page closes. If it can't start (e.g. the port is taken), the launcher shows an alert. The log is `~/Library/Logs/vinst.log`, trimmed when it passes 1 MB.

## Environment variables

- `VINST_HOME`: where instruments live (default `./instruments`)
- `VINST_BUILD`: build output (default `./build`)
- `VINST_DS_LIBRARY`: DecentSampler library folder (default `~/DecentSampler`)

## Files

Each instrument lives in `instruments/<slug>/`:

- `instrument.toml`: the wizard's answers, readable and hand-editable
- `hits.json`: slicing and analysis results, plus your decisions (rejections, moved hits)
- `recorded-in-app.json`: the click grid and hand-set cuts of takes recorded in the app

Audio is kept outside the repo: takes in the folder set in the worksheet, sliced samples in `<takes folder>/sliced` unless set otherwise, builds in `build/`.

## Slicing and analysis

- Onsets are detected on a normalised mix of all signals recorded together, so every signal of a hit is cut at the same samples.
- Takes recorded in the app keep their click grid (`recorded-in-app.json`): each hit is the strongest onset in its bar, so handling noise (a piezo hears every touch) never becomes a hit. A noise during the ring ends the hit before it.
- Each hit starts 2 ms before its attack (the first sample within 20 dB of the attack's peak, so a touch of the string before the pluck is left out), its tail is trimmed 6 dB above the noise floor (from the noise-floor recording, or estimated from the take), it's DC-removed and gets a 50 ms fade-out. Optional denoising uses the noise-floor recording as the profile and only changes the sliced samples.
- Pitch: YIN, restricted to ±5 semitones around the expected note, refined by a spectral peak with parabolic interpolation (YIN reads about 4 cents sharp on decaying notes); well under 1 cent on test tones.
- Loudness is the RMS of the first 0.3 s (the attack) for plucked and struck sounds, the held part for sustained ones.
- A take is written to a `.part` file and only replaces the existing one when it finishes; the count-in isn't recorded, apart from half a second of pre-roll.
- Decisions in `hits.json` are carried over when slicing again while the recording's size and modification time are unchanged. The slicing setup fingerprint covers passes, signals and loops, so velocity changes don't need re-slicing.
- Loops: a WAV `smpl` chunk is used as is; otherwise the steady part of each note is searched for the loop whose end best matches its start.
- Level, evening out and low cut are applied at build time to the bundle's copies of the samples. The build aims the loudest velocity's median loudness at −16 dBFS, and a 2 ms limiter keeps peaks under −1 dBFS. With the Low cut knob, what the cut took out is written beside each sample (`-low-part`), raised and limited with the same gain curve (held under the ceiling for the cut alone and for both together), so the two add up to the uncut sample. A group of its own, in step with the notes, plays it at the volume the knob gives: all of it at 0 %, none at 100 % (where the knob starts), so the knob blends linearly between the uncut and the cut sample.
- Mapping writes each sample's `tuning`, pitches samples down to fill keys between recorded notes (up only above the highest note), renumbers round robins per key and velocity cell so rejected hits leave no gaps, and plays release extras from a `trigger="release"` group. Extras on their own key get a group each (`seqMode="round_robin"`, so recorded sequences stay in order); a muting one carries the `mutes` tag, and the notes' groups get `silencedByTags="mutes"` with `silencingDecay` 0.06 s.

## Reference

- [builder-ui.md](builder-ui.md): what each step of the wizard does, in detail
- [decentsampler-reference.md](decentsampler-reference.md): the DecentSampler format, its gotchas and the tool landscape
