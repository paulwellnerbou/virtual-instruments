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
- **Own pass:** recorded in a separate run through all takes, e.g. piezo and internal mic that share one output and are switched inside the instrument. Each pass is sliced on its own, with its own noise floor. In the instrument, passes either **switch** via a menu (the default) or **layer**: played together, which sounds like two players in unison. This is set in step 6.

### 3. Range
- **String instruments:** the strings with their tuning and fret count, e.g. G C E A, re-entrant or low G.
  - The range and the string/fret for each note are derived from this.
- **Everything else:** lowest and highest note.
- **Sampling density:** chromatic, every 2nd note, or every 3rd note. Unsampled notes are filled by stretching neighbours.
- **Beyond the recorded notes:** the lowest and highest samples can be stretched further down and up, by up to 24 semitones; the step shows the resulting playable range. This only changes the built instrument, so nothing needs recording or slicing again.

### 4. Dynamics and extras
- **Velocity lanes:** the dynamics every note is recorded at, quietest first, e.g. p and f, each in its own take. A slider per lane sets the velocity where it starts ("f starts at velocity 80"), with a bar showing each lane's range; the default is an even split.
- **Variations per lane:** how often each note is played per lane; DecentSampler alternates between them.
- These are **defaults**: in the worksheet, single notes can get their own lanes and velocity ranges. Changing the defaults never needs new slicing, because a hit's lane comes from its take.
- Ring time per hit, noise-floor length, and **extras**: release noises, chucks, body knocks, each its own take.

### 5. Worksheet
Recording, slicing and review in one place, replacing the earlier recording sheet and review steps.
- **At the top:** the takes folder, sample rate and bit depth, the audio interface and which input each signal is plugged into ("Detect" finds it), where the sliced samples go, denoising and loops, and "Slice all" for takes exported from a DAW.
- **The noise floor** and every **extra** get a row with ● to record it and its hits.
- **One card per note, one row per lane:**
  - the lane's velocity range for this note; changing where a lane starts (Enter or leaving the field) gives the note its own lanes;
  - ● records the lane (a count-in, then one hit per bar with a visual metronome), + records more variations to compare with the ones there, ▶ plays the kept hits with their attacks 0.75 s apart;
  - the hits as chips, coloured by their flags. **Drag a chip to another lane** to move it; it keeps that lane when slicing again;
  - without a recording, the file name to export from a DAW, e.g. `060_C-0_p_pickup.wav`.
- **"+ lane"** adds a lane to one note only (e.g. an ff for the low strings), × removes an empty lane from it, and "Use default lanes" goes back to the defaults.
- **Takes:** one per note and lane, `<note>_<lane>_<signal>.wav`. More variations recorded later are `<note>_<lane>-more<n>_<signal>.wav`. Every hit in a lane take is a variation of that lane, however many there are; the planned number is only a guide.
- **Takes from before lanes,** which held all dynamics in one file (`<note>_<signal>.wav`), still count: their hits start in the note's first lane, to be dragged into the right ones.
- **Recording in the app** slices that recording right away. A take is written to a temporary file and only replaces the existing one when it finishes; Cancel, a failed write, the interface stopping mid-take or leaving the page all keep the old take. The count-in isn't recorded, apart from half a second of pre-roll. The app refuses to record when the interface runs at another sample rate than the instrument, or when the chosen interface isn't connected.
- **The hit dialog:** details, a player for each signal, Rejected, Use loop, "Move to lane", and "Record a new one instead", which records one more variation and rejects this hit once it's saved.
- **Slicing:**
  - Onsets are detected on a normalised mix of all signals, so every signal of a hit is cut at the same samples.
  - Each hit gets 5 ms of pre-roll, and its tail is trimmed 6 dB above the noise floor. The noise floor comes from the noise-floor recording, or is estimated from the take. Optional denoising (off by default) uses the same recording as the noise profile; it only changes the sliced samples, never the takes.
  - Each hit is DC-removed and gets a 50 ms fade-out.
  - Takes recorded in the app keep their click grid: each hit is the strongest onset in its bar, so handling noise (a piezo hears every touch) never becomes a hit. A noise during the ring ends the hit before it, so it isn't in the sample.
- **Pitch:** YIN, restricted to ±5 semitones around the expected note, refined by a spectral peak with parabolic interpolation (YIN reads about 4 cents sharp on decaying notes); accurate to well under 1 cent on test tones.
- **Flags:** wrong note (>50 cents), clipped, no pitch, cut off, out of tune (>20 cents; info only, the instrument corrects tuning), and loudness that fits another lane of the note better. A note whose lanes don't get louder in order is reported.
- **Decisions** (rejections, moved lanes, loops turned off) are stored in `hits.json` and survive slicing again, as long as that recording is unchanged.
- **Changed recordings:** takes recorded, changed or deleted since slicing are listed, and the build refuses until they're sliced again.
- **Loops** (optional; on by default for sustained and single-sample instruments): loop markers in the takes (a WAV `smpl` chunk, e.g. saved by WaveLab) are used as is; otherwise the steady part of each note is searched for the loop whose end best matches its start. Weak loops are flagged, the hit dialog previews the loop repeating, and "Use loop" turns a single hit's loop off.
- An **"Only problems"** filter hides notes that need no attention.

### Importing existing samples (implemented)
- **Choose it in step 1**, under "Samples": "Import existing samples" instead of "Record new takes". Step 3 isn't needed then, and step 4 only uses the lane names.
- **The worksheet reads a folder of already-cut samples**, one note per file, subfolders included (WAV, AIFF, FLAC).
- **Notes:**
  - A file's note comes from its name, either a note name ("C3", "F#4", "Eb2") or a MIDI number ("055").
  - Each candidate is confirmed by measuring the pitch from the attack, so the octave convention of a name (middle C = C3 or C4) is settled by the audio. The setting only matters when the pitch can't be measured.
  - Files without a usable name get their note from the pitch alone. Their periodicity is checked, so noise isn't mistaken for a low note.
  - Names that disagree with the pitch are flagged. Files that end up with no note are listed as "Not imported".
- **Velocity lanes:**
  - a lane name in the file name ("_mf_"),
  - otherwise a velocity number ("v2"), ranked,
  - otherwise the samples of each note are spread over the lanes by loudness.

  Several samples in one lane become variations, ordered by "rr"/"take" numbers or the file name.
- **Signals:** files whose names contain a signal id ("_piezo", "_mic") are paired into one hit. A hit missing a signal's file is flagged and left out.
- **Loops:** markers stored in the file are used; otherwise the loop search applies.
- **Files** are copied under uniform names into `instruments/<slug>/imported-samples`; the imported folder is only read.
- **Afterwards** everything works as for recorded takes: review, rejections, loudness balancing and building. Changing the folder's contents asks you to import again.

### 6. Sound and look (implemented)
- **Tuning correction:** off, partial (a share of the measured cents) or full. This writes each sample's `tuning`.
- **Mapping:**
  - Every recorded note is stretched to the midpoint with its neighbours; the upper one is pitched down.
  - Gaps from unrecorded or unusable takes are covered the same way.
  - The lowest and highest samples can be stretched further (set in step 3).
- **Neighbour round robins** (off, ±1 or ±2 semitones): each key also cycles through nearby notes' hits, pitched to fit.
- **Velocity:** each note plays its own lanes with their velocity ranges. A lane without usable hits gives its range to the next lower lane.
- **Round robins** are renumbered per key and velocity cell, so rejected hits leave no silent gaps.
- **Extras:** played on key release (a `trigger="release"` group), on their own key (with random round robins), or not used.
- **Even out loudness** (optional): per velocity layer, each note's level is compared with a running median over its five neighbouring notes, and the difference (at most ±6 dB by default) goes into the sample's `volume`. Only notes that stick out change; the shape across the range and the gaps between layers stay as recorded, and the audio files are untouched.
- **Envelope:** attack, release and velocity sensitivity.
- **Knobs:** signal mix (an equal-power crossfade for two signals, one volume per signal for more), tone (lowpass), attack, release, reverb, chorus, release-noise volume. CC1 drives the tone knob.
- **Look:** background image (812 × 375), background and text colours, and a live preview.

### 7. Build (implemented)
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
