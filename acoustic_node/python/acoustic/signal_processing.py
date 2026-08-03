"""Pure signal-processing functions for the acoustic module.

No hardware I/O here on purpose — everything in this file operates on plain
numpy arrays so it can be unit-tested with synthetic signals on any machine,
per the dev-machine-first rule in CLAUDE-COMMON.md.
"""

from __future__ import annotations

import numpy as np


def compute_rms(samples: np.ndarray) -> float:
    """Root-mean-square level of a block of audio samples.

    Returns a linear value, not dBFS. For float32 samples in [-1, 1],
    a full-scale sine wave has RMS ~0.707.
    """
    if samples.size == 0:
        raise ValueError("compute_rms: samples array is empty")
    return float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))


def compute_fft(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Single-sided magnitude spectrum of a real-valued audio clip.

    Returns (frequencies_hz, magnitude), where magnitude is normalized by
    clip length so amplitude doesn't scale with clip duration.
    """
    if samples.size == 0:
        raise ValueError("compute_fft: samples array is empty")

    n = samples.size
    window = np.hanning(n)
    windowed = samples * window

    spectrum = np.fft.rfft(windowed)
    frequencies = np.fft.rfftfreq(n, d=1.0 / sample_rate)

    # Normalize by the window's own gain so peak magnitude is comparable
    # across clips of different lengths.
    magnitude = np.abs(spectrum) / (window.sum() / 2.0)

    return frequencies, magnitude


def dominant_frequency(frequencies: np.ndarray, magnitude: np.ndarray, min_freq_hz: float = 20.0) -> float:
    """Frequency bin with the highest magnitude, ignoring sub-audible bins.

    DC and near-DC content (mic bias, handling noise, hum) can leak across
    several low bins even after windowing and would otherwise dominate the
    peak search. min_freq_hz=20 matches the low end of the audible range
    this project cares about.
    """
    if frequencies.size <= 1:
        raise ValueError("dominant_frequency: need more than one frequency bin")
    audible = frequencies >= min_freq_hz
    if not np.any(audible):
        raise ValueError("dominant_frequency: no frequency bins at or above min_freq_hz")
    peak_index = np.argmax(np.where(audible, magnitude, -np.inf))
    return float(frequencies[peak_index])
