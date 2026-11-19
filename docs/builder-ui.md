# User guide

The builder walks you through seven steps, from describing your instrument to a finished DecentSampler instrument. Everything is saved as you go.

On the start page you can create, open, **duplicate** and **delete** instruments. Duplicating is a quick start for the next instrument of the same kind, e.g. a second ukulele. Delete moves the instrument's settings, review decisions and builds to the Trash; your recordings, sliced samples and the copy installed in DecentSampler stay.

## 1. Instrument

Name, author, version and description, and the **type**, which fills in sensible defaults for everything after it:

| Type | Examples | Defaults |
|---|---|---|
| Plucked / struck string | ukulele, harp, mandolin | strings and tuning, 3 velocities, 4 variations per note, release noise |
| Pitched percussion | metallophone, sansula, kalimba | 3 velocities, 3 variations |
| Sustained | violin, clarinet, flute, pads | looped notes, attack and release knobs, 1–2 velocities |
| Unpitched percussion | body knocks, drums | one sound per key, many variations |
| Single sample | a found sound | one sample stretched across a key range |

Under **Samples**, choose whether to record new takes or [import existing samples](#importing-existing-samples).

## 2. Signals

What you record, e.g. a pickup, a microphone or a DI. Each becomes its own sound in the instrument.

- **Together:** recorded at the same time as the other "together" signals, e.g. pickup and mic on two inputs. They're cut at the same points, and a knob blends them.
- **Own pass:** recorded in a separate run through all notes, e.g. a piezo and an internal mic that share one output. In the instrument, passes either **switch** via a menu or **layer** (play together, like two players in unison); set in step 6.

## 3. Range

- **String instruments:** the strings with their tuning and number of frets, e.g. G C E A, re-entrant or low G. Each note is recorded once, on the string where it's played at the lowest fret.
- **Everything else:** the lowest and highest note.
- **Sampling density:** every note, every 2nd or every 3rd. Notes in between are played by stretching their neighbours.
- **Beyond the recorded notes:** stretch the lowest and highest samples further down and up. This only changes the built instrument; nothing needs recording again. More than 2–3 semitones starts to sound unnatural.

Note names follow DecentSampler: middle C is C3 (MIDI 60).

## 4. Dynamics and extras

- **Velocities:** the dynamics every note is recorded at, quietest first, e.g. p and f, each in its own take. A slider sets where each one starts ("f starts at velocity 80").
- **Variations per velocity:** how many hits of each note and velocity to record; DecentSampler alternates between them, so repeated notes don't sound mechanical.
- These are **defaults**: in the worksheet, a single note can get its own velocities and ranges.
- How long each hit rings, the length of the noise-floor recording, and **extras**: release noises, chucks, body knocks, each recorded as one take.

## 5. Worksheet

Record, check and fix everything in one place.

**Beside the notes** are the settings for recording: the takes folder, sample rate and bit depth, your audio interface and which input each signal is plugged into ("Detect" finds it while you play), and where the sliced samples go, with optional denoising and loops.

**The noise floor** comes first: 30 seconds of silence, which tells the builder what is noise and what is your instrument. Below it is a card for each note and one row for each **extra**.

![The worksheet](images/worksheet.png)

### Recording

- **● on a row** records it: a count-in, then one hit per bar, guided by a visual metronome. Choose how many hits before you press Record.
- Once a velocity is recorded, ● records **more hits** beside the ones you have, so you can compare them all and reject the weak ones. The noise floor and extras are single takes, so ● records them again.
- A recording only replaces the old one when it finishes: cancelling or a problem with the interface keeps what you had. The app won't record when the interface runs at another sample rate than the instrument.
- **Recorded elsewhere?** Drop an audio file (WAV, AIFF, FLAC) from your DAW onto a row. It becomes that velocity's take, or more hits if the velocity is already recorded, so nothing is overwritten. With several signals per take, put the signal's name in the file name (e.g. `…_piezo.wav`).
- Files in the takes folder are named `<note>_<velocity>_<signal>.wav`, e.g. `060_C-0_p_pickup.wav`; more hits recorded later are `…_p-more1_pickup.wav`. Files you put there yourself are picked up with **Slice them**.

### Checking

- Each recording is **cut into hits** right away. A row shows its hits side by side, as wide as they are long, with each hit's **tuning** and **attack loudness** below it.
- **Flags** in colour: wrong note (more than 50 cents off), clipped, no pitch found, cut off, out of tune (more than 20 cents; just for information, as the instrument corrects tuning), and a hit whose loudness fits another velocity better. A note whose velocities don't get louder in order is reported too.
- **▶** plays a row's kept hits one after another, their attacks 0.75 s apart, so you can compare them.
- **Show only notes with problems** hides the notes that need no attention.

### Fixing

Click a hit to open it:

![Reviewing a hit](images/hit-review.png)

- **Rejected** leaves it out of the instrument. **Record a new one instead** records one more hit and rejects this one once the new one is saved.
- **Move to velocity**, or drag the hit onto another row of the same note.
- **Drag the blue lines** to change where the hit starts and ends. The cuts you set are kept, also when slicing again; "Find hits automatically" goes back.
- **Re-slice** finds the hits of just this recording again.
- **Move this recording…** if you played a take on the wrong note: its files go to the right note and velocity.
- A recording in which no hit was found shows a **no hit found** tile; open it to mark the hit by hand.

Your decisions are kept when you slice again, as long as the recording hasn't changed. If recordings were added, changed or deleted since slicing, the worksheet lists them, and the build waits until they're sliced again. After an update that finds hits better, the worksheet suggests slicing again; that keeps your decisions and the cuts you set.

### Velocities per note

- Drag the dots on the slider beside a note to set where its next velocity starts.
- **+ velocity** adds a velocity to this note only, e.g. an ff for the low strings; × removes an empty one; **Use default velocities** goes back to step 4's.
- Hits of a removed velocity stay with the note under "No velocity" until you drag them into one.

### Loops

For sustained sounds (on by default for sustained and single-sample instruments): notes keep playing while the key is held. Loop markers saved in the file (e.g. by WaveLab) are used as they are; otherwise the builder searches each note for a loop. Weak loops are flagged, the hit dialog lets you hear the loop repeat, and "Use loop" turns it off for one hit.

## Importing existing samples

Choose **Import existing samples** in step 1 to build from a folder of already-cut samples, one note per file (WAV, AIFF, FLAC, subfolders included). Step 3 isn't needed then, and step 4 only uses the velocity names. The folder is only read; the files are copied into the instrument.

- **Notes** come from the file name, as a note name ("C3", "F#4", "Eb2") or a MIDI number ("055"). The builder checks each against the measured pitch, which also settles whether the names use middle C = C3 or C4. Files without a usable name get their note from the pitch alone; names that disagree with the pitch are flagged, and files without a note are listed as "Not imported".
- **Velocities** come from a velocity name in the file name ("_mf_"), otherwise a velocity number ("v2"), otherwise each note's samples are spread over the velocities by loudness. Several samples for one velocity become variations, ordered by "rr" or "take" numbers or by file name.
- **Signals:** files whose names contain a signal name ("_piezo", "_mic") are paired into one hit.

After importing, the worksheet works as for recorded takes. Change the folder's contents and it asks you to import again.

## 6. Sound and look

- **Tuning correction:** off, partial (keeps some natural detune) or full.
- **Mapping:** keys between recorded notes play the recorded note above them, pitched down, as pitching down sounds more natural than pitching up. Unrecorded or unusable notes are covered the same way. **Neighbour round robins** (±1 or ±2 semitones) also cycle each key through nearby notes' hits, for more variation.
- **Even out loudness** (optional): brings every sample of a velocity to that velocity's typical loudness, so notes play alike while soft velocities stay softer than loud ones.
- **Low cut** (optional): lowers what lies below each note's own pitch, e.g. the thump of a pluck on a ukulele's body. It follows each note, so it never cuts into the low notes. Only pitched notes are affected, never extras or unpitched sounds. It's applied when building, so you can change it, build and listen without slicing again. With the **Low cut** knob (**Thump cut** on plucked and struck instruments) you can lessen it while playing: it blends between the samples as recorded (0 %) and with the cut set here (100 %, where it starts). For that, the instrument also carries what the cut took out, as extra samples that play along with each note. Loudness is evened out for the full cut, so notes with much thump play louder with the knob turned down.
- **Level:** recordings often sit well below full scale; the build makes the instrument as loud as it needs to be and keeps the peaks from clipping.
- **Extras:** play on key release, on their own key, or not at all. On their own key, each press plays the next of their hits in turn, in the order you recorded them (e.g. chucks down, up, down…). **Stops ringing notes** (on for a chuck): playing it fades out the notes still ringing, as a hand on the strings does.
- **Envelope:** attack, release and velocity sensitivity.
- **Knobs:** signal mix, tone, low cut, attack, release, reverb, chorus, release-noise volume. The mod wheel drives the tone knob.
- **Look:** background image (812 × 375 px), background and text colours, with a live preview.

Only the built instrument changes here; your recordings and sliced samples stay as they are.

## 7. Build

**Build** creates the instrument and checks it: the preset against DecentSampler's format, that every sample file exists, that every key and velocity is covered, and the variations. Warnings and errors are listed. You can build before everything is recorded, e.g. to try the first few notes.

- **DecentSampler:** **Install into DecentSampler** copies it into your DecentSampler library folder (`~/DecentSampler`), replacing only instruments this builder created. **Package .dslibrary** makes a single file to share. **Show in Finder** shows the built instrument.
- **Soundfont:** the same instrument as an SF2 file, for MuseScore and other SF2 players, which play it without DecentSampler. SF2 has no variations, so each key and velocity always plays one sample (its own note's, the best in tune), and release noises are left out. **Download** saves it; **Copy to MuseScore** puts it into `~/Documents/MuseScore4/SoundFonts` (never over a soundfont made elsewhere): restart MuseScore and choose it as a staff's sound in the Mixer. **Show in Finder** shows the file.
