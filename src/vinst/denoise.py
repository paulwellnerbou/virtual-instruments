"""Optional spectral noise reduction, using the noise-floor recording as the noise profile.

Off by default (Processing.denoise). Only the sliced samples are processed, never the takes.
"""

import librosa
import numpy as np
from scipy.ndimage import uniform_filter

N_FFT = 2048
HOP = 512
OVER_SUBTRACTION = 2.0  # remove a bit more than the average noise; noise frames vary around it
SMOOTH_BINS, SMOOTH_FRAMES = 3, 5  # smoothing the gain avoids "musical noise" artefacts


def noise_profile(noise: np.ndarray) -> np.ndarray:
    """Mean noise power per frequency bin."""
    spec = np.abs(librosa.stft(noise, n_fft=N_FFT, hop_length=HOP)) ** 2
    return spec.mean(axis=1)


def reduce(audio: np.ndarray, profile: np.ndarray, reduction_db: float) -> np.ndarray:
    """Spectral subtraction with a gain floor: noise is lowered by at most `reduction_db`."""
    if len(audio) < N_FFT:
        return audio.copy()
    spec = librosa.stft(audio, n_fft=N_FFT, hop_length=HOP)
    power = np.abs(spec) ** 2
    floor = 10 ** (-reduction_db / 20)
    gain = np.sqrt(np.clip(1 - OVER_SUBTRACTION * profile[:, None] / np.maximum(power, 1e-20), 0, 1))
    gain = np.maximum(uniform_filter(gain, size=(SMOOTH_BINS, SMOOTH_FRAMES), mode="nearest"), floor)
    return librosa.istft(spec * gain, hop_length=HOP, n_fft=N_FFT, length=len(audio))
