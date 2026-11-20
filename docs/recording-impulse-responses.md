# Recording your own impulse response

An **impulse response** (IR) is a recording of how a room, or a reverb, answers one short click. A convolution reverb plays every sound through that recording, so the IR *is* the reverb: capture a room you like, and your instrument sounds as if it were played there.

This guide covers two ways to get one:

- **[Capturing a reverb plug-in](#capturing-a-reverb-plug-in)**, e.g. a Valhalla reverb. Quick, and always clean.
- **[Recording a real room](#recording-a-real-room)**, e.g. your living room, a hall or a church. More work, and the result is only as good as the recording.
  - **[On the road](#on-the-road-a-clap-and-a-recorder)**, with no computer: a clap or a balloon, and a handheld recorder or your phone.

Then **[preparing the file](#preparing-the-file)**, which matters in both cases, and **[using it](#using-it)**.

> **In the builder,** an impulse response of your own plays the reverb of *Without the attack's hiss* (Sound & look → Reverb), in place of DecentSampler's own reverb, with the same pitch-following filtering.

## Capturing a reverb plug-in

A plug-in reverb gives you its IR from a single click: no noise, no speaker, no room to keep quiet.

1. **Make the click:** a new mono or stereo track at **48 kHz**, a few seconds of silence, then **one sample at full scale** (0 dBFS) and silence again. In WaveLab, draw it with the pencil at maximum zoom; in Cubase, use any one-sample file. Leave a second of silence before the click, so nothing gets cut off at the start.
2. **Set up the reverb:**
   - **100 % wet, no dry signal.** For use in the builder's send buses the IR must contain *only* the reverb (see [why](#the-direct-sound)).
   - **Modulation off,** or as low as it goes. Many reverbs (Valhalla's included) slowly vary their delays for a lively shimmer. An IR is a frozen snapshot: modulation turns into a fixed colour, or into a faint chorus that sounds wrong when frozen.
   - **No "freeze", no ducking, no gate:** they don't survive as an IR.
   - **Pre-delay as you want it,** since it ends up in the IR.
3. **Render** the track through the reverb, offline, at 48 kHz, 32-bit float, **long enough for the whole tail** (if in doubt, 10 s more than you think).
4. Continue with [preparing the file](#preparing-the-file).

**Stereo:** render in stereo; a reverb's width lives in the difference between left and right. A mono click into a stereo reverb is fine.

**Licence:** for your own instruments this is fine. Before publishing an instrument with an IR captured from a commercial plug-in, check that plug-in's licence.

## Recording a real room

The standard method plays a **sine sweep** through a loudspeaker, records it with a microphone, and turns that recording back into an impulse response (**deconvolution**). A sweep puts far more energy into the room than a click, so the IR comes out much cleaner, and the speaker's distortion lands outside the IR instead of in it.

### What you need

- **A loudspeaker** that goes reasonably low and high: a studio monitor is ideal. A small speaker gives an IR without lows; for a ukulele that matters little, for a cello it does.
- **A microphone:** an omni measurement microphone is the most neutral. The microphone you would record the instrument's room sound with works too, and gives its own colour. **Two microphones** (a spaced pair or an XY pair) give a stereo IR.
- **One audio interface for playing and recording,** so playback and recording run on the same clock. Two devices drift apart, and the deconvolution smears.
- **Software for the sweep and the deconvolution.** [Room EQ Wizard (REW)](https://www.roomeqwizard.com/), free for macOS and Windows, generates the sweep, records through your interface and exports the impulse response as a WAV. Logic Pro users have Apple's *Impulse Response Utility*; Voxengo's *Deconvolver* is another option.

### The room

- **Quiet beats everything.** The tail can only be as long as it stays above the room's noise. Switch off fans, fridges, heating and computers you don't need; record at night or at the weekend.
- **Nothing moves** during a sweep: no people walking, no doors, no traffic you can avoid.
- Close windows; leave the room as you want it to sound (curtains, furniture, open doors all count).

### Placement

- **The speaker stands where the instrument would be,** at its height, facing where the instrument would face.
- **The microphones stand where a listener or a room microphone would be.** Close to the speaker you get a drier, more direct IR; further away, more room. A few metres is a good start.
- Keep the speaker and microphones away from walls and corners (at least a metre if you can), or you'll capture boomy early reflections more than the room.
- Try two or three positions; rooms differ a lot from place to place.

### The sweep

- **Range:** 20 Hz to 20 kHz, or from about 40 Hz with a small speaker.
- **Length:** 10–15 s. Longer sweeps give a cleaner IR; REW's longer sweeps are fine.
- **Silence after it,** longer than the room's tail: in a church, 8 s or more.
- **48 kHz, 24-bit,** the same as the instrument.
- **Level:** as loud as the speaker plays cleanly, with the recording peaking around **−6 dBFS**. No clipping anywhere: not in the interface, not in the speaker. A distorting speaker makes a dirty IR.
- **Record two or three sweeps** per position and keep the cleanest. Listen to each for a car, a footstep, a creaking floor.

### Turning the recording into an IR

REW does this as part of the measurement; with other tools you deconvolve the recording with the sweep. **For a stereo IR, both channels must keep their timing to each other:** record both microphones in one go (REW: measure each channel with a timing reference, so left and right line up), and never align them by hand.

Continue with preparing the file.

### On the road: a clap and a recorder

A church on holiday, the space under a bridge: no speaker, no computer. A short, loud sound and a recorder do it. Record what the room does with the sound, and at home keep only the room's answer. It's less clean than a sweep, but for the builder's send buses it's often enough: the builder low-passes the IR at 5 kHz and cuts the upper partials per band anyway, so the highs a clap lacks don't matter, and it sets the level itself. What matters is **noise** and the **[direct sound](#the-direct-sound)**.

**The sound**, from best to worst:
1. **A balloon pop:** loud, and more lows than a clap. In a church, ask first.
2. **Two hardcover books slapped flat together,** or two pieces of wood: louder than hands, and more alike from hit to hit.
3. **A clap with cupped hands:** more body than a flat clap, which is mostly 1–3 kHz.

**The recorder** (a handheld one, e.g. a Zoom H4n; or your phone, see below):
- **WAV, 48 kHz, 24-bit,** stereo with the built-in mics. An XY pair that can be turned goes to its wider setting (120°): reverb wants width.
- **Limiter and compressor off:** they would pump the tail. **Low cut off,** except outdoors in wind, where it goes on with the windscreen.
- **Put it down** on a ledge, a pew, a wall or a tripod; held in the hand, it records your hands. It stands where a listener would be; you stand **3–5 m away**, where you'd play, facing it.
- **Level:** test with one clap; peaks around −6 to −10 dBFS, since a clap is faster than the meter. If the tail drowns in noise, more gain is fine even if **the clap itself** just clips: that first peak gets cut at home. Nothing after it may clip.

**Recording:**
1. Press Record, walk calmly to your place, and wait until your footsteps' echo is gone.
2. **Say what this is** ("Church, position one, recorder by the font"): at home you won't remember.
3. Clap, then **stand still and stay quiet** until the tail is gone, and a few seconds more: in a church often 5–10 s.
4. **5–10 claps** per position: you'll keep the cleanest, with no car, cough or pigeon in it.
5. **Two or three positions,** away from walls (boomy) and, under a bridge, off-centre between parallel walls (flutter). A photo of each setup helps later.

**With only a phone:** the same, with the phone put down instead of the recorder. Use an app that records **uncompressed or lossless** (WAV, not AAC or MP3) and switch off **noise reduction, voice isolation or "enhance", and automatic level**: to them, a reverb tail is noise, and they eat it. Phone microphones are mostly mono: it works (the builder makes it stereo), but the reverb then sits in the middle instead of spreading wide.

**At home,** [prepare the file](#preparing-the-file) as for any room: pick the cleanest clap (a quiet room before it, a tail that sinks evenly), cut just after the clap itself, fade out before the noise. Then **make up for the clap's missing lows:** a low shelf of about +6 dB below about 500 Hz, set by ear on your lowest notes. Without it, low notes get a thinner reverb and sound quieter at the same Reverb knob.

## Preparing the file

### The direct sound

A room recording, or a reverb rendered with some dry signal, starts with the **direct sound**: the click (or the speaker) heard straight, before any reflection. For the builder's send buses the dry sound already comes from the samples, so a direct sound in the IR would play the instrument **a second time**, coloured by speaker and microphone. That's exactly what *Without the attack's hiss* avoids.

So: **remove the direct sound.** Cut the IR just after it, or fade it in over a few milliseconds from there, keeping the first reflections. The direct sound is the first sharp peak; the reflections follow a few milliseconds later. Plug-in captures at 100 % wet have no direct sound; nothing to do.

(If you want to use the IR as a full room in a DAW, with the instrument recorded dry and played *only* through the IR, keep the direct sound.)

### Start and end

- **Start:** cut the silence before the first sound, or keep a few milliseconds of it as pre-delay if you want one.
- **End:** find where the tail sinks into the noise (in a spectrum or level view, where the decay stops falling and turns into a flat hiss), and **fade out** over the last part before it, then cut. A tail that ends in noise adds that noise to every note.
- **Length:** only as long as it's audible. Longer IRs cost more CPU and make the instrument bigger, for nothing.

### Level and format

- **Normalize** to about −1 dBFS. The builder sets the level anyway: it makes the IR as loud as DecentSampler's reverb with the instrument's room size and damping.
- **WAV, 48 kHz, 24-bit** (or 32-bit float), **stereo** if you recorded or rendered stereo, otherwise mono.
- **Name** it for what it is, e.g. `living-room-3m.wav`, `valhalla-supermassive-hall.wav`.

## Using it

### In the builder

1. **Sound & look → Reverb:** switch on **Without the attack's hiss**.
2. Under **Impulse response of your own**, drop your file (WAV, AIFF or FLAC) or click + to choose it. It's copied into the instrument's folder; **Remove** goes back to DecentSampler's reverb.
3. **Build** and play it. The build summary names the IR the reverbs were made from.

When building, the IR is:
- **made stereo** if it's mono, and converted to 48 kHz if it isn't;
- **filtered per few semitones** like DecentSampler's reverb is: a high shelf from about three times the pitch up and a low-pass at 5 kHz;
- **made as loud, after that filtering, as DecentSampler's reverb** with the instrument's room size and damping, so the Reverb knob sets about the same amount either way (room size and damping don't otherwise change your IR). An IR with a different colour still sounds different in level on some notes: one with fewer lows than DecentSampler's reverb is quieter on low notes. Set the knob by ear.

With the switch off, the IR is ignored and the instrument plays DecentSampler's own reverb, as before.

### Listening for faults

Play a few notes, low and high, with the Reverb knob turned up, or try the IR in your DAW first (any convolution reverb that loads WAV files, e.g. REVerence in Cubase Pro). Listen for:

- **a second, early copy of the note:** the direct sound is still in the IR;
- **hiss at the end of the tail:** cut it earlier, or fade out sooner;
- **a boomy or ringing low note:** the speaker or microphone stood too close to a wall;
- **a metallic flutter:** two parallel walls bouncing the sound back and forth; try another position.

## Checklist

- [ ] Quiet room, nothing moving; or a plug-in at 100 % wet with modulation off
- [ ] One interface for playing and recording; 48 kHz, 24-bit
- [ ] Sweep 20 Hz–20 kHz, 10–15 s, silence longer than the tail; recording peaks around −6 dBFS, no clipping
- [ ] Or on the road: balloon, books or cupped claps; recorder put down 3–5 m away, limiter off; 5–10 claps per position, silence until each tail is gone
- [ ] Two or three takes per position; the cleanest kept
- [ ] Direct sound removed (for the send buses)
- [ ] Tail faded out before the noise; no longer than audible
- [ ] Normalized to about −1 dBFS; WAV 48 kHz, 24-bit, stereo if recorded in stereo
