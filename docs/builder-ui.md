# Instrument builder UI

A local app (`vinst ui`, opened in the browser) that walks through creating an instrument of any type.

- Every answer is saved to `instruments/<name>/instrument.toml`. The file stays readable and hand-editable. The UI is only an editor for it plus the processing steps.
- An instrument can be duplicated as the starting point for the next one, e.g. a second ukulele from the first.

## Steps

### 1. Instrument
- Name, author, version, description.
- **Type**, which fills in sensible defaults for everything after it:

| Type | Examples | Defaults |
|---|---|---|
| Plucked / struck string | ukulele, harp, mandolin | decaying, no loop, 3 velocity layers, 4 round robins, release noise, strings + tuning, strum option |
| Pitched percussion | metallophone, sansula, kalimba | decaying, no loop, 3 velocity layers, 3 round robins |
| Sustained | violin, clarinet, flute, found-sound pads | looped, attack and release knobs, 1–2 velocity layers |
| Unpitched percussion | body knocks, drums | one-shot, one sample per key, many round robins |
| Single sample | a found sound | one sample stretched across a key range |

### 2. Signals
- The recorded sources, e.g. a pickup, an external mic, a DI. Each one becomes a group in the instrument.
- **Together:** recorded at the same time as the other “together” signals. They come from the same performance, are cut at identical positions, and a mix knob blends them.
- **Own pass:** recorded in a separate run through all takes, e.g. piezo and internal mic that share one output and are switched inside the instrument. Each pass is sliced on its own, with its own noise floor. In the instrument, passes either **switch** via a menu (the default) or **layer**: played together, which sounds like two players in unison. This is set in step 7.

### 3. Range
- **String instruments:** the strings with their tuning and fret count, e.g. G C E A, re-entrant or low G.
  - The range and the string/fret for each note are derived from this.
- **Everything else:** lowest and highest note.
- **Sampling density:** chromatic, every 2nd note, or every 3rd note. Unsampled notes are filled by stretching neighbours.

### 4. Dynamics and variations
- Number of velocity layers and round robins.
- Extras: release noises, FX/noise samples, loops (sustained types).

### 5. Recording sheet
- A generated, printable take list: take number, note, string/fret, what to play, and the expected file name.
- Shows the total hit count and estimated recording time.

### 6. Import and review (implemented)
- **Slicing:**
  - Onsets are detected on a normalised mix of all signals, so every signal of a hit is cut at the same samples.
  - Each hit gets 5 ms of pre-roll, and its tail is trimmed 6 dB above the noise floor. The noise floor comes from the noise-floor recording (silence with everything connected), or is estimated from the take. Optional denoising (off by default) uses the same recording as the noise profile. It is spectral subtraction with a maximum reduction in dB, and it only changes the sliced samples, never the takes.
  - Each hit is DC-removed and gets a 50 ms fade-out.
  - Hits are assigned to layers and round robins in the recorded order. If the hit count doesn't match the plan, the take is flagged and nothing is assigned.
- **Pitch:**
  - YIN, restricted to ±5 semitones around the expected note, finds the note. A spectral peak with parabolic interpolation then refines it, because YIN reads about 4 cents sharp on decaying notes.
  - This is accurate to well under 1 cent on test tones.
- **Flags:** wrong note (>50 cents), clipped, no pitch, layer mismatch, short, out of tune (>20 cents; this is info only, because the preset corrects tuning).
- **Rejections** are stored in `hits.json` and survive re-slicing, unless the take files changed (re-recorded).
- **Loops** (optional; on by default for sustained and single-sample instruments):
  - Loop markers in the take files (a WAV `smpl` chunk, e.g. saved by WaveLab) are matched to the hit they fall into and used as is.
  - Otherwise the app searches the steady part of each note, between the attack and the fade into silence. It looks for the loop whose last 20 ms best match the 20 ms before its start, preferring long loops.
  - Loops scoring below 0.9 are flagged.
  - The hit dialog plays a preview of the loop repeating, and a "Use loop" switch turns a single hit's loop off; that choice survives re-slicing.
  - The preset gets `loopStart`/`loopEnd` and an equal-power `loopCrossfade` (30 ms by default).
- **Review UI:**
  - Each take shows a waveform with the cut regions. Hit chips are laid out per layer and round robin, with cents, coloured by severity.
  - Clicking a hit opens its details, a player for each signal, and a reject switch.
  - An "Only problems" filter hides takes that need no attention.
- **Not yet built:** a keyboard view of key and velocity coverage after rejections. It fits better in milestone 3, where the mapping is made.

### 7. Sound and look (implemented)
- **Tuning correction:** off, partial (a share of the measured cents) or full. This writes each sample's `tuning`.
- **Mapping:**
  - Every recorded note is stretched to the midpoint with its neighbours; the upper one is pitched down.
  - Gaps from unrecorded or unusable takes are covered the same way.
  - The lowest and highest samples can be stretched further.
- **Neighbour round robins** (off, ±1 or ±2 semitones): each key also cycles through nearby notes' hits, pitched to fit.
- **Velocity:** a layer whose hits were all rejected gives its range to the next lower layer.
- **Round robins** are renumbered per key and velocity cell, so rejected hits leave no silent gaps.
- **Extras:** played on key release (a `trigger="release"` group), on their own key (with random round robins), or not used.
- **Even out loudness** (optional): per velocity layer, each note's level is compared with a running median over its five neighbouring notes, and the difference (at most ±6 dB by default) goes into the sample's `volume`. Only notes that stick out change; the shape across the range and the gaps between layers stay as recorded, and the audio files are untouched.
- **Envelope:** attack, release and velocity sensitivity.
- **Knobs:** signal mix (an equal-power crossfade for two signals, one volume per signal for more), tone (lowpass), attack, release, reverb, chorus, release-noise volume. CC1 drives the tone knob.
- **Look:** background image (812 × 375), background and text colours, and a live preview.

### 8. Build (implemented)
- Writes `build/<slug>/<Name>.dsbundle`: the preset, `Samples/` (only used, non-rejected hits, copied incrementally), `Images/` and `DSLibraryInfo.xml`.
- **Validation:**
  - the vendored DecentSampler XSD and the engine's vocabulary of binding parameters, types and levels
  - sample files, with exact case
  - key and velocity coverage of every note group
  - round-robin positions
- Installs into the DecentSampler library folder, refusing to replace bundles this tool didn't create.
- Packages `<Name>-<version>.dslibrary`, a zip with the bundle folder at the top.

## Technology

The proposal is **Python only**. The backend does the audio work (slicing, pitch detection) anyway.

- **Candidate:** [NiceGUI](https://nicegui.io), which provides wizard steps, file pickers, audio playback and charts for waveforms, without a separate frontend build.
- **Alternative if NiceGUI feels limiting:** a FastAPI backend with a small HTML frontend.

## Build order

1. Spec format and core: notes, signals, range, layers. Then the recording sheet (steps 1–5). This is enough to go and record.
2. Slicing, analysis and the review grid (step 6), tested against the first real takes.
3. Preset generation, validation and install (steps 7–8). This produces the first playable ukulele.
4. Strum page (chord sequences from the single notes, re-entrant or low-G voicing) and the other instrument types, including loops for sustained instruments. Import an existing sample library as a test.
