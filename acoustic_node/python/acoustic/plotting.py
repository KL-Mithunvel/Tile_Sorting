"""Waveform + frequency-domain plotting for a captured audio clip."""

from __future__ import annotations

import numpy as np
from matplotlib import pyplot as plt

from acoustic.signal_processing import compute_fft, dominant_frequency


def plot_waveform_and_spectrum(clip: np.ndarray, sample_rate: int, block: bool = True) -> None:
    """Show a two-panel plot: time-domain waveform and FFT magnitude spectrum.

    Blocks (via plt.show()) until the window is closed, unless block=False.
    """
    frequencies, magnitude = compute_fft(clip, sample_rate)
    peak_freq = dominant_frequency(frequencies, magnitude)

    duration = clip.size / sample_rate
    time_axis = np.linspace(0, duration, clip.size, endpoint=False)

    fig, (ax_time, ax_freq) = plt.subplots(2, 1, figsize=(9, 6))

    ax_time.plot(time_axis, clip, linewidth=0.8)
    ax_time.set_title("Captured waveform")
    ax_time.set_xlabel("Time (s)")
    ax_time.set_ylabel("Amplitude")

    ax_freq.plot(frequencies, magnitude, linewidth=0.8)
    ax_freq.axvline(peak_freq, color="r", linestyle="--", linewidth=0.8, label=f"peak {peak_freq:.0f} Hz")
    ax_freq.set_title("Frequency spectrum")
    ax_freq.set_xlabel("Frequency (Hz)")
    ax_freq.set_ylabel("Magnitude")
    ax_freq.set_xlim(0, sample_rate / 2)
    ax_freq.legend()

    fig.tight_layout()
    plt.show(block=block)
