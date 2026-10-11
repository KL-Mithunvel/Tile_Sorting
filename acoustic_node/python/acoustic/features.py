"""Runtime features of one tap clip, and an optional rule-based sound grade.

Pure functions (numpy only): feed a clip, get numbers back. The point of this
module is the station's live decision, so it stays deliberately small — peak
frequency, spectral centroid, loudness, decay time, signal-to-noise — not the
full DSP suite that lives in the Acoustic-Analysis workbench
(documents/project/Acoustic_Analysis_Methods.md). When that workbench has a
trained/validated reference from real good and defective tiles, copy the
result into config `station.reference` (or replace `grade_from_reference`);
nothing here imports from it.

NOTHING HERE IS CALIBRATED against real tiles. `valid` only says "this looks
like a real strike, not silence"; the grade is `None` (abstain) until a
reference band is configured, and even then the station keeps the sound vote
off by default (`final.vote_enabled`).
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from acoustic.signal_processing import compute_fft, compute_rms, dominant_frequency


def _envelope(x: np.ndarray, sample_rate: int, window_ms: float = 2.0) -> np.ndarray:
    """Short-time RMS of the clip, one value per window (non-overlapping)."""
    n = max(1, int(sample_rate * window_ms / 1000.0))
    usable = (len(x) // n) * n
    if usable == 0:
        return np.abs(x)
    frames = x[:usable].reshape(-1, n)
    return np.sqrt(np.mean(frames * frames, axis=1))


def analyze_clip(
    clip: np.ndarray,
    sample_rate: int,
    pre_trigger_ms: float = 100.0,
    *,
    min_peak: float = 0.02,
    min_snr_db: float = 20.0,
    min_freq_hz: float = 100.0,
    max_freq_hz: float = 12000.0,
) -> dict:
    """Features of one clip. Never raises on a bad clip: an empty, silent or
    clipped-to-nothing clip comes back with `valid: False` and a reason, so one
    bad tap cannot take the station down."""
    out: dict = {"valid": False, "reason_invalid": None}
    x = np.asarray(clip, dtype=np.float64).ravel()
    if x.size < 64 or not np.isfinite(x).all():
        out["reason_invalid"] = "clip is empty or not finite"
        return out

    x = x - float(np.mean(x))  # remove DC offset
    peak = float(np.max(np.abs(x)))
    pre_n = int(sample_rate * pre_trigger_ms / 1000.0)
    noise_rms = compute_rms(x[: max(pre_n, 32)]) if x.size > 32 else 0.0
    snr_db = 20.0 * np.log10(max(peak, 1e-12) / max(noise_rms, 1e-9))

    out.update(
        peak_amplitude=round(peak, 5),
        rms=round(compute_rms(x), 5),
        noise_rms=round(noise_rms, 6),
        snr_db=round(float(snr_db), 1),
        duration_s=round(x.size / sample_rate, 3),
    )

    if peak < min_peak:
        out["reason_invalid"] = f"too quiet (peak {peak:.4f} < {min_peak})"
        return out
    if snr_db < min_snr_db:
        out["reason_invalid"] = f"not above the noise (SNR {snr_db:.1f} dB < {min_snr_db})"
        return out

    # Onset: first sample at 25 % of the peak. Everything the spectrum and the
    # decay describe happens after the strike, not in the pre-trigger noise.
    onset = int(np.argmax(np.abs(x) >= 0.25 * peak))
    window = x[onset:]
    if window.size < 256:
        out["reason_invalid"] = "the strike is at the very end of the clip"
        return out
    out["onset_ms"] = round(1000.0 * onset / sample_rate, 1)

    freqs, mag = compute_fft(window, sample_rate)
    band = (freqs >= min_freq_hz) & (freqs <= max_freq_hz)
    if not band.any() or float(mag[band].sum()) <= 0.0:
        out["reason_invalid"] = "no energy in the analysis band"
        return out
    out["peak_frequency_hz"] = round(float(dominant_frequency(freqs[band], mag[band], min_freq_hz=0.0)), 1)
    out["centroid_hz"] = round(float(np.sum(freqs[band] * mag[band]) / np.sum(mag[band])), 1)

    # Decay: time from the envelope's peak until it has fallen by 20 dB.
    env = _envelope(window, sample_rate)
    ipeak = int(np.argmax(env))
    below = np.nonzero(env[ipeak:] <= env[ipeak] * 0.1)[0]
    step_s = 0.002
    out["decay_t20_s"] = round(float(below[0]) * step_s, 4) if below.size else None

    out["valid"] = True
    return out


def grade_from_reference(features: dict, reference: Optional[dict]) -> tuple[Optional[str], Optional[float]]:
    """(grade, confidence) from a configured reference band, or (None, None) —
    abstain — when there is no reference or the tap was not valid.

    `reference` (config station.reference):
        peak_hz_min / peak_hz_max  the band a sound tile rings in
        max_decay_t20_s            optional: a cracked tile rings flatter/shorter
                                   or duller; set the direction that applies
        rule_confidence            fixed confidence reported for a rule hit
                                   (a rule has no probability; this is a stand-in)
    Anything outside the band is "Reject"; inside is "Grade A". A single band
    cannot tell Grade B from Grade A, so no other grade is produced.
    """
    if not reference or not features.get("valid"):
        return None, None
    lo, hi = reference.get("peak_hz_min"), reference.get("peak_hz_max")
    if lo is None or hi is None:
        return None, None
    conf = float(reference.get("rule_confidence", 0.7))
    peak = features.get("peak_frequency_hz")
    if peak is None:
        return None, None
    ok = lo <= peak <= hi
    max_decay = reference.get("max_decay_t20_s")
    decay = features.get("decay_t20_s")
    if ok and max_decay is not None and decay is not None and decay > max_decay:
        ok = False
    return ("Grade A" if ok else "Reject"), conf


def waveform_preview(clip: np.ndarray, points: int = 600) -> list[float]:
    """The clip reduced to about `points` values for the GUI (peak per bucket,
    keeping the sign of the larger excursion so a strike still looks like one)."""
    x = np.asarray(clip, dtype=np.float64).ravel()
    if x.size == 0:
        return []
    n = max(1, x.size // points)
    usable = (x.size // n) * n
    frames = x[:usable].reshape(-1, n)
    idx = np.argmax(np.abs(frames), axis=1)
    return [round(float(v), 4) for v in frames[np.arange(frames.shape[0]), idx]]


def spectrum_preview(
    clip: np.ndarray, sample_rate: int, points: int = 200, max_freq_hz: float = 10000.0
) -> dict:
    """Magnitude spectrum for the GUI, log-spaced between 50 Hz and max_freq_hz,
    scaled to its own maximum (0..1)."""
    x = np.asarray(clip, dtype=np.float64).ravel()
    if x.size < 64:
        return {"freq_hz": [], "mag": []}
    freqs, mag = compute_fft(x - float(np.mean(x)), sample_rate)
    edges = np.geomspace(50.0, min(max_freq_hz, sample_rate / 2.0), points + 1)
    out_f, out_m = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (freqs >= lo) & (freqs < hi)
        out_f.append(round(float(np.sqrt(lo * hi)), 1))
        out_m.append(float(mag[sel].max()) if sel.any() else 0.0)
    top = max(out_m) or 1.0
    return {"freq_hz": out_f, "mag": [round(m / top, 4) for m in out_m]}
