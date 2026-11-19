# DecentSampler Instrument Builder

A framework for building [DecentSampler](https://www.decentsamples.com/product/decent-sampler-plugin/) instruments from recordings. It takes raw takes and produces a validated `.dsbundle` / `.dslibrary`.

## Usage

```bash
uv run vinst ui
```

Other commands:

- `uv run vinst list`
- `uv run vinst new "Name" --type plucked`
- `uv run vinst sheet <slug>`: writes the recording sheet as HTML and CSV next to the spec
- `uv run vinst slice <slug>`: cuts the recorded takes into hits, analyses them and writes `hits.json`
- `uv run vinst build <slug> [--install] [--package]`: generates and validates the `.dsbundle`; optionally installs it into `~/DecentSampler` and writes a `.dslibrary`
- `uv run vinst synth-takes <slug> <folder>`: writes fake takes, with a few deliberate mistakes, for trying out slicing and review before recording
- `uv run pytest`

Each instrument lives in `instruments/<slug>/`:

- `instrument.toml`: the wizard's answers
- `hits.json`: slicing and analysis results, plus your reject decisions

Denoising is optional and off by default. Turn it on in step 6 to use the noise-floor recording as the noise profile; it only changes the sliced samples, never the takes. Leave it off if you clean the takes in WaveLab or Cubase.

Audio is kept outside the repo. Takes go in the takes folder set in step 5; sliced samples go in `<takes folder>/sliced` unless you set another folder.

Status: milestones 1–3 are done. The whole wizard works, from the spec and recording sheet through slicing and review to building, validating, installing and packaging. Milestone 4 adds strumming and the other instrument types.

Environment variables:

- `VINST_HOME`: where instruments live (default `./instruments`)
- `VINST_BUILD`: build output (default `./build`)
- `VINST_DS_LIBRARY`: DecentSampler library folder (default `~/DecentSampler`)

Installing only ever replaces bundles this tool created.

## Research notes

- [docs/decentsampler-reference.md](docs/decentsampler-reference.md): the format, gotchas and the tool landscape
- [docs/builder-ui.md](docs/builder-ui.md): the wizard UI for creating instruments of any type

## Why build our own

- No maintained library for generating DecentSampler presets exists. The available tools either convert from SFZ/EXS or are small one-off scripts.
- Writing the earlier instruments by hand caused real bugs: invalid XML, misspelled attributes that were silently ignored, octave-shifted mappings, and backslash paths.
- The tedious part is getting from raw takes to mapped samples. A preset editor doesn't help with that, and the steps are easy to automate.

## Proposed design (Python, managed with `uv`)

```
src/vinst/
  notes.py      MIDI ↔ names. DecentSampler names middle C "C3" (= 60); MIDI numbers are used everywhere internally.
  sheet.py      spec → recording sheet (take order, string/fret, dynamics), plus a CSV
  slice.py      takes → hits: onset detection on the mic track, with the same cut points applied to the DI;
                pre-roll, tail trim at the noise floor, fade-out, DC removal
  analyze.py    per hit: pitch (pYIN) → check against the sheet, cents → `tuning`;
                loudness → layer sanity check and outliers; writes a review report
  mapping.py    zones (chromatic or spread), velocity layers, round-robin positions, release groups
  model.py      typed preset model (groups/samples/ui/effects/midi/tags) → XML
  ui.py         reusable layouts: knob row, Mic↔DI equal-power crossfade, Tone table, strum page
  validate.py   vendored XSD + engine-vocabulary.json, file existence and case, key/velocity coverage gaps
  package.py    .dsbundle + DSLibraryInfo.xml, .dslibrary zip, symlink into ~/DecentSampler for audition
  cli.py        vinst sheet | slice | analyze | build | validate | install | package
instruments/<name>/instrument.toml   declarative spec: range, strings, layers, RR, voices, UI, effects
instruments/<name>/artwork/
vendor/decentsampler-schema/          XSD + engine-vocabulary.json (MIT)
```

- **Audio stays out of the repo.** Raw takes live wherever you record them (e.g. an external audio drive), set per instrument in its spec, and builds go to `build/` (gitignored).
- **Planned dependencies:** `numpy`, `soundfile`, `scipy`, `librosa` (onsets and pYIN), `lxml` (XSD validation) and `typer`.
- **Possible later additions:**
  - render MIDI test phrases through the DecentSampler VST3 with `pedalboard`, for automated listening checks (it's unverified whether a preset can be loaded headlessly);
  - export to SFZ/Kontakt with ConvertWithMoss.
- **Importing existing sample libraries** into specs is a good regression test for the generator.

## Licence

MIT, see [LICENSE](LICENSE). The DecentSampler schema and engine vocabulary in
`src/vinst/vendor/decentsampler_schema/` come from
[DecentSamples/DecentSampler-schema](https://github.com/DecentSamples/DecentSampler-schema) and are MIT-licensed by their authors
(see the LICENSE file there).
