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

## Reference

- [builder-ui.md](builder-ui.md): what each step of the wizard does, in detail
- [decentsampler-reference.md](decentsampler-reference.md): the DecentSampler format, its gotchas and the tool landscape
