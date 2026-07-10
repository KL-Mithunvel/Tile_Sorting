import numpy as np
import pytest

from acoustic.signal_processing import compute_fft, compute_rms, dominant_frequency


def make_sine(freq_hz: float, sample_rate: int = 44100, duration_s: float = 1.0, amplitude: float = 1.0) -> np.ndarray:
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    return (amplitude * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)


def test_compute_rms_of_full_scale_sine_is_about_0_707():
    sine = make_sine(freq_hz=440, amplitude=1.0)
    rms = compute_rms(sine)
    assert rms == pytest.approx(0.707, abs=0.01)


def test_compute_rms_scales_with_amplitude():
    quiet = make_sine(freq_hz=440, amplitude=0.1)
    loud = make_sine(freq_hz=440, amplitude=0.5)
    assert compute_rms(loud) == pytest.approx(5 * compute_rms(quiet), rel=0.05)


def test_compute_rms_rejects_empty_array():
    with pytest.raises(ValueError):
        compute_rms(np.array([]))


def test_compute_fft_finds_known_frequency_peak():
    sample_rate = 44100
    sine = make_sine(freq_hz=1000, sample_rate=sample_rate, duration_s=0.5)
    frequencies, magnitude = compute_fft(sine, sample_rate)
    peak = dominant_frequency(frequencies, magnitude)
    assert peak == pytest.approx(1000, abs=5)


def test_compute_fft_frequency_axis_length_matches_magnitude():
    sample_rate = 44100
    sine = make_sine(freq_hz=2000, sample_rate=sample_rate, duration_s=0.25)
    frequencies, magnitude = compute_fft(sine, sample_rate)
    assert frequencies.shape == magnitude.shape


def test_compute_fft_rejects_empty_array():
    with pytest.raises(ValueError):
        compute_fft(np.array([]), 44100)


def test_dominant_frequency_ignores_dc_offset():
    sample_rate = 44100
    sine = make_sine(freq_hz=500, sample_rate=sample_rate, duration_s=0.5, amplitude=0.3)
    sine_with_dc = sine + 0.9  # large DC offset, would dominate bin 0
    frequencies, magnitude = compute_fft(sine_with_dc, sample_rate)
    peak = dominant_frequency(frequencies, magnitude)
    assert peak == pytest.approx(500, abs=5)
