import numpy as np

from acoustic.features import analyze_clip, grade_from_reference, spectrum_preview, waveform_preview

SR = 44100


def strike(freq=2800.0, decay_s=0.05, dur=0.5, onset_s=0.1, amp=0.5, noise=0.001, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * SR)) / SR
    x = rng.normal(0, noise, t.size)
    k = t >= onset_s
    x[k] += amp * np.exp(-(t[k] - onset_s) / decay_s) * np.sin(2 * np.pi * freq * (t[k] - onset_s))
    return x.astype(np.float32)


def test_peak_frequency_and_validity_of_a_clean_strike():
    f = analyze_clip(strike(freq=2800.0), SR, pre_trigger_ms=80)
    assert f["valid"]
    assert abs(f["peak_frequency_hz"] - 2800.0) < 40.0
    assert 50 < f["centroid_hz"] < 6000
    assert f["snr_db"] > 20
    assert 60 <= f["onset_ms"] <= 120


def test_longer_ringing_gives_longer_decay():
    short = analyze_clip(strike(decay_s=0.01), SR, pre_trigger_ms=80)
    long = analyze_clip(strike(decay_s=0.08), SR, pre_trigger_ms=80)
    assert short["valid"] and long["valid"]
    assert long["decay_t20_s"] > short["decay_t20_s"]


def test_silence_is_invalid_not_an_exception():
    f = analyze_clip(np.zeros(SR // 2, dtype=np.float32), SR)
    assert f["valid"] is False and f["reason_invalid"]


def test_noise_only_clip_is_invalid():
    rng = np.random.default_rng(1)
    f = analyze_clip(rng.normal(0, 0.05, SR // 2).astype(np.float32), SR, pre_trigger_ms=80)
    assert f["valid"] is False


def test_empty_and_nan_clips_do_not_raise():
    assert analyze_clip(np.array([], dtype=np.float32), SR)["valid"] is False
    bad = strike()
    bad[10] = np.nan
    assert analyze_clip(bad, SR)["valid"] is False


def test_reference_grading_abstains_without_a_reference_or_valid_tap():
    f = analyze_clip(strike(), SR, pre_trigger_ms=80)
    assert grade_from_reference(f, None) == (None, None)
    assert grade_from_reference(f, {}) == (None, None)
    assert grade_from_reference({"valid": False}, {"peak_hz_min": 1, "peak_hz_max": 2}) == (None, None)


def test_reference_band_grades_in_and_out():
    f = analyze_clip(strike(freq=2800.0), SR, pre_trigger_ms=80)
    ref = {"peak_hz_min": 2500, "peak_hz_max": 3100, "rule_confidence": 0.8}
    assert grade_from_reference(f, ref) == ("Grade A", 0.8)
    ref_off = {"peak_hz_min": 3500, "peak_hz_max": 4500}
    assert grade_from_reference(f, ref_off)[0] == "Reject"


def test_previews_are_bounded():
    x = strike()
    assert len(waveform_preview(x, 600)) <= 700
    s = spectrum_preview(x, SR, points=100)
    assert len(s["freq_hz"]) == len(s["mag"]) == 100 and max(s["mag"]) == 1.0
