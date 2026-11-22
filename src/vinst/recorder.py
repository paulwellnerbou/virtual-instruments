"""Recording takes straight from the audio interface.

PortAudio reads the interface through CoreAudio, the same path a DAW uses, so the samples are the
converter's own. Its audio thread only copies blocks into a queue; a writer thread puts them on
disk, so slow disk access never drops audio. Driver overflows are counted and reported, so a take
with a dropout gets recorded again instead of hiding a click in a sample.
"""

import math
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from vinst.plan import recording_passes
from vinst.slicing import SUBTYPES
from vinst.spec import Instrument

PART_SUFFIX = ".part"  # written here first: an existing take is replaced only by a finished one
CLIP_LEVEL = 0.999


@dataclass
class Device:
    index: int
    name: str
    channels: int
    sample_rate: int


def _sd() -> Any:
    import sounddevice

    return sounddevice


class RecorderError(RuntimeError):
    """A take can't be started or written; the message is for the user."""


def input_devices(rescan: bool = False) -> list[Device]:
    """Audio inputs; empty if PortAudio isn't available.

    PortAudio reads the device list, including each device's sample rate, once when it starts.
    rescan re-reads it, e.g. after plugging in an interface or changing its rate in Audio MIDI
    Setup; it is skipped while a stream is open, because re-initialising would break that stream.
    """
    try:
        sd = _sd()
        if rescan and not Recorder.open_count:
            sd._terminate()
            sd._initialize()
        return [Device(i, d["name"], int(d["max_input_channels"]), int(d["default_samplerate"]))
                for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    except (ImportError, OSError):
        return []


def find_device(name: str, devices: list[Device]) -> Device | None:
    """The named device; with no name, the system's default input. None if it isn't connected."""
    if name or not devices:
        return next((d for d in devices if d.name == name), None)
    try:
        default = _sd().default.device[0]
    except (ImportError, OSError, IndexError, TypeError):
        default = None
    return next((d for d in devices if d.index == default), devices[0])


def channels_for(inst: Instrument) -> dict[str, int]:
    """Signal id -> input number; signals recorded together default to inputs 1, 2, …"""
    out = {}
    for p in recording_passes(inst):
        for n, s in enumerate(p.signals, 1):
            out[s.id] = inst.recording.input_channels.get(s.id, n)
    return out


def problem(inst: Instrument, device: Device | None, signal_ids: list[str]) -> str | None:
    """Why recording can't start with this device, or None."""
    if device is None:
        return "The audio interface isn't connected. Connect it and press ⟳, or choose another input."
    if device.sample_rate != inst.recording.sample_rate:
        return (f"{device.name} runs at {device.sample_rate} Hz, this instrument records at "
                f"{inst.recording.sample_rate} Hz. Change one of them (Audio MIDI Setup sets the "
                "interface's rate), so nothing gets resampled.")
    channels = channels_for(inst)
    too_high = [s for s in signal_ids if channels[s] > device.channels]
    if too_high:
        return f"{device.name} has {device.channels} inputs; choose another input for {', '.join(too_high)}."
    return None


@dataclass
class Result:
    seconds: float
    overflows: int
    clipped: list[str] = field(default_factory=list)  # signal ids
    files: list[Path] = field(default_factory=list)  # empty if nothing was kept
    error: str | None = None  # why nothing was kept although it should have been


class Recorder:
    """An open input stream: levels while armed, files while recording."""

    open_count = 0

    def __init__(self, device: Device, channels: dict[str, int], sample_rate: int,
                 stream_factory: Callable[..., Any] | None = None) -> None:
        self.signals = list(channels)
        self.columns = [channels[s] - 1 for s in self.signals]
        self.sample_rate = sample_rate
        self.overflows = 0
        self.frames = 0
        self.heard = False  # anything but digital silence since the stream opened
        self._peaks = np.zeros(len(self.signals))
        self._sumsq = np.zeros(len(self.signals))
        self._count = 0
        self._clipped: set[str] = set()
        self._queue: queue.Queue[np.ndarray | None] = queue.Queue()
        self._writing = False
        self._files: dict[str, sf.SoundFile] = {}
        self._paths: dict[str, Path] = {}
        self._writer: threading.Thread | None = None
        self._write_error: Exception | None = None
        self._skip = 0
        self._lock = threading.Lock()
        self.closed = False
        factory = stream_factory or _sd().InputStream
        self.stream = factory(device=device.index, channels=max(self.columns) + 1, samplerate=sample_rate,
                              dtype="float32", latency="high", callback=self._callback)
        Recorder.open_count += 1
        try:
            self.stream.start()
        except Exception:
            self.close()
            raise

    def _callback(self, indata: np.ndarray, frames: int, _time: Any, status: Any) -> None:
        if getattr(status, "input_overflow", False):
            self.overflows += 1
        block = indata[:, self.columns]
        peaks = np.max(np.abs(block), axis=0) if len(block) else np.zeros(len(self.columns))
        with self._lock:
            self._peaks = np.maximum(self._peaks, peaks)
            self._sumsq += np.sum(np.square(block, dtype=np.float64), axis=0)
            self._count += len(block)
        if peaks.any():
            self.heard = True
        if self._writing and self._skip:
            n = min(self._skip, len(block))
            self._skip -= n
            block = block[n:]
            peaks = np.max(np.abs(block), axis=0) if len(block) else np.zeros(len(self.columns))
        if self._writing and len(block):
            for s, peak in zip(self.signals, peaks):
                if peak >= CLIP_LEVEL:
                    self._clipped.add(s)
            self._queue.put(block.copy())

    def levels(self) -> dict[str, float]:
        """Peak level per signal in dBFS since the last call."""
        with self._lock:
            peaks, self._peaks = self._peaks, np.zeros(len(self.signals))
        return {s: 20 * math.log10(p) if p > 0 else -math.inf for s, p in zip(self.signals, peaks)}

    def rms(self) -> dict[str, float]:
        """RMS level per signal in dBFS since the last call: with nothing played, the noise."""
        with self._lock:
            sumsq, count = self._sumsq, self._count
            self._sumsq, self._count = np.zeros(len(self.signals)), 0
        return {s: 10 * math.log10(v / count) if count and v > 0 else -math.inf
                for s, v in zip(self.signals, sumsq)}

    @property
    def recording(self) -> bool:
        return self._writing

    @property
    def clipped(self) -> set[str]:
        """Signals that clipped in the current recording."""
        return set(self._clipped) if self._writing else set()

    def start(self, paths: dict[str, Path], bit_depth: int, skip_seconds: float = 0.0) -> None:
        """Write each signal to its file (signal id -> final path) until stop().

        skip_seconds: not written at the start, e.g. most of the count-in, so that handling noise
        while getting ready doesn't end up in the take.
        """
        subtype = SUBTYPES.get(bit_depth, "PCM_24")
        self._paths = paths
        try:
            for s in self.signals:
                paths[s].parent.mkdir(parents=True, exist_ok=True)
                self._files[s] = sf.SoundFile(part_path(paths[s]), "w", samplerate=self.sample_rate, channels=1,
                                              subtype=subtype, format="WAV")
        except (OSError, sf.SoundFileError) as e:
            self._discard()
            raise RecorderError(f"Can't write the take in {paths[self.signals[0]].parent}: {e}") from e
        self.overflows, self.frames, self._write_error = 0, 0, None
        self._skip = int(skip_seconds * self.sample_rate)
        self._clipped = set()
        # A fresh queue: a block the audio thread queued after the last stop must not start this take.
        self._queue = queue.Queue()
        self._writer = threading.Thread(target=self._write, daemon=True)
        self._writer.start()
        self._writing = True

    def _write(self) -> None:
        while (block := self._queue.get()) is not None:
            if self._write_error:
                continue  # keep draining, so the queue doesn't grow while the take is failing
            try:
                for i, s in enumerate(self.signals):
                    self._files[s].write(block[:, i])
                self.frames += len(block)
            except Exception as e:  # disk full, drive gone: reported by stop()
                self._write_error = e

    def stop(self, keep: bool, min_seconds: float = 0.0) -> Result:
        """Finish the files; if kept they replace the takes, otherwise they are discarded.

        A take that failed to write, or got less audio than min_seconds (the interface stopped
        delivering), is discarded even when keep is set; Result.error says why.
        """
        self._writing = False
        self._queue.put(None)
        if self._writer:
            self._writer.join()
        seconds = self.frames / self.sample_rate
        error = None
        if keep and self._write_error:
            error = f"Writing the take failed: {self._write_error}"
        elif keep and seconds < min_seconds:
            error = (f"The interface stopped delivering audio after {seconds:.0f} s (cable, power or "
                     "driver?).")
        elif keep and not self.frames:  # stopped during the count-in, which isn't kept
            error = "It was stopped before anything was recorded."
        files = []
        try:
            for f in self._files.values():
                f.close()
            if keep and not error:
                for path in self._paths.values():
                    part_path(path).replace(path)
                    files.append(path)
        except (OSError, sf.SoundFileError) as e:
            error, files = f"Saving the take failed: {e}", []
        if not files:
            self._discard()
        result = Result(seconds, self.overflows, sorted(self._clipped), files, error)
        self._files, self._paths, self._writer = {}, {}, None
        return result

    def _discard(self) -> None:
        for s, f in self._files.items():
            try:
                f.close()
            except (OSError, sf.SoundFileError):
                pass
            part_path(self._paths[s]).unlink(missing_ok=True)  # only files this take created
        self._files = {}

    def close(self) -> None:
        """Stop and release the interface; safe to call more than once."""
        if self.closed:
            return
        self.closed = True
        Recorder.open_count -= 1
        if self._writing:
            self.stop(keep=False)
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:  # the device may be gone already; it is released either way
            pass


def part_path(path: Path) -> Path:
    return path.with_name(path.name + PART_SUFFIX)


LOUD_PEAK_DB = -6.0  # where the loudest playing should peak: headroom for a harder hit
QUIET_PEAK_DB = -12.0  # below this, the loudest velocity was recorded too low
NOTHING_PLAYED_DB = -45.0  # below this, the take holds no hit at all, only the room


def level_advice(peak_db: float) -> str | None:
    """For a take of the loudest velocity: how to set the input gain, if it was too low. Soft takes
    are quiet on purpose, so they aren't judged."""
    # Nothing played (stopped early, or the wrong input): no gain to suggest from it.
    if peak_db >= QUIET_PEAK_DB or peak_db < NOTHING_PLAYED_DB:
        return None
    up = round(LOUD_PEAK_DB - peak_db)
    return (f"Quite quiet: the loudest hit peaks at {peak_db:.0f} dBFS. Turning the input gain up by about "
            f"{up} dB (to peaks around {LOUD_PEAK_DB:.0f} dBFS) records less noise. Keep the gain the same for "
            "all takes of the instrument, so soft and loud stay in proportion.")
