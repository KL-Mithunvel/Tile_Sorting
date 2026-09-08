# Acoustic Analysis Methods — Reference and Application to Tile Quality Classification

**Status:** design / reference note (2026-09-08). No code written from this yet.
**Source reviewed:** Crystal Instruments, *Acoustic Analysis*
— <https://www.crystalinstruments.com/acoustic-analysis> (fetched 2026-09-08).
**Companion docs:** `Acoustic_Sorting_Subsystem.md` (the tap station + pipeline this
plugs into), `project_charter.md` §6.2, `AI_Software_Novelty.md` (adaptive grading),
`documents/requirements/requirements.md` FR-21.

---

## 0. Why this document exists

The owner asked to "go over the Crystal Instruments acoustic analysis page, document it,
perform all of that processing in the noise system, and then classify tile quality from
the noise."

This document does three things:

1. **Part A** — documents every process, metric, and standard the Crystal Instruments
   page describes, in its own terms.
2. **Part B** — judges each one for relevance to *impact-acoustic non-destructive
   testing of a clay tile* (which is not what Crystal Instruments sells — they sell
   noise/vibration measurement instruments), and says where it lands in `acoustic_node`.
3. **Part C** — assembles the relevant methods into one concrete per-tile analysis +
   classification pipeline, and lists the code modules that would implement it.

> **Framing caveat.** Crystal Instruments' toolset is built for *noise emission*
> measurement — "how loud / how annoying is this machine or this room," measured on
> steady or slowly varying sound. Tile testing is the opposite regime: a single
> repeatable *impulse* whose *decaying resonance* carries the defect information. Many
> Crystal metrics (sound power, sound intensity, Noise Criterion curves, loudness in
> sones, closed-loop acoustic control) are either not applicable or only apply to
> qualifying the test *environment*, not grading a tile. Part B is explicit about which
> is which. The methods that transfer directly are **fractional-octave-band analysis**,
> **FFT spectral analysis**, **frequency weighting**, **time weighting / level metrics**,
> **the octave waterfall (spectrogram)**, and **statistical level analysis** — plus the
> hardware guidance on microphones, IEPE power, TEDS, and calibration.

---

# PART A — What the Crystal Instruments page describes

## A.1 Data acquisition systems

The page frames all analysis around Crystal Instruments' own hardware:

| Family | Channels | Role |
|---|---|---|
| Spider-20 | 4 | Portable dynamic signal analyzer |
| Spider-80X | 4–512 | High-channel dynamic measurement / vibration control |
| Spider-80Xi | up to 1024 | Very high channel count |
| Spider-81 / 81B | — | Vibration test controller |
| CoCo-80X / 90X | 2–16 | Handheld analyzers |
| GRS (Ground Recorder System) | — | Supersonic / remote / autonomous sound recording |

Hardware features called out: **onboard IEPE/ICP transducer power** (constant-current
supply so a microphone or accelerometer plugs in directly, no external conditioner),
and **TEDS** (Transducer Electronic Data Sheet — the sensor stores its own sensitivity
and calibration data on a chip, read automatically so the operator does not type sensor
parameters in).

Listed applications: portable acoustic measurement, supersonic acquisition,
high-channel measurement, **absorption measurement** using speaker-generated
white/pink noise, **sound power** determination in anechoic / hemi-anechoic chambers,
and **background-noise characterization** in buildings.

## A.2 Octave filters (fractional-octave-band analysis)

A **real-time bank of parallel band-pass filters** whose centre frequencies are spaced
logarithmically. Resolutions offered: **1/1, 1/3, 1/6, 1/12 octave**. Each filter band
outputs its own **RMS time history**, and the bank as a whole produces a **3-D waterfall**
plot: log frequency on the x-axis, time on the z-axis, level on the y-axis.

Standards compliance claimed:

| Standard | What it governs |
|---|---|
| **ANSI S1.11:2004**, Order 3 Type 1-D (also Order 7 Type 1-D) | Octave / fractional-octave digital filter accuracy class |
| **IEC 61260-1995** | Octave and fractional-octave band filters (international equivalent) |
| **IEC 225-1966** | Older octave-filter specification (superseded, still referenced) |
| **DIN 45651** | German octave-filter specification (legacy) |

"Order 3 / Order 7" = the Butterworth filter order of each band-pass; "Type 1-D" = the
precision class (Type 1) for a digital (D) implementation.

Why octave bands rather than raw FFT: constant *percentage* bandwidth matches how
hearing works, compresses a spectrum to ~30 numbers (1/3-octave, 20 Hz–20 kHz), and is
the format noise regulations are written in.

## A.3 Sound Level Meter (SLM)

A virtual SLM that takes one microphone channel and derives multiple standardised
level signals in parallel.

### Frequency weighting

| Weighting | Purpose |
|---|---|
| **A** | Approximates ear sensitivity at moderate levels; de-emphasises lows and highs. The default for almost all noise regulation. |
| **B** | Mid-level correction; largely obsolete. |
| **C** | Nearly flat 31.5 Hz–8 kHz; used for peak levels and high-level noise. |

(The page describes these as compensating for "how the human hearing system is more
sensitive to some frequencies than others." A fourth, **Z** = zero / flat weighting,
is the un-weighted case.)

### Time weighting (exponential averaging time constant)

| Name | Time constant |
|---|---|
| **Slow** | 1000 ms |
| **Fast** | 125 ms |
| **Impulse** | 35 ms rise / 1500 ms fall (fast attack, slow release — designed to hold the level of a transient) |

### Derived metrics

| Symbol | Definition (page wording) |
|---|---|
| **L_AF** | A-weighted, Fast-time-weighted sound level |
| **L_Aeq** | A-weighted equivalent-continuous sound level — the steady level with the same energy as the actual fluctuating signal over the measurement time |
| **L_peak** | Peak (instantaneous, not time-weighted) sound level, usually C- or Z-weighted |
| **Statistical levels L1, L5, L50, L95** | The level exceeded for 1 %, 5 %, 50 %, 95 % of the measurement time. L90/L95 ≈ background floor; L1/L5 ≈ loudest events; L50 = median. |

The output "can be normalized with a calibration value" — i.e. a measured
94 dB / 1 kHz pistonphone tone sets the Pa-per-count scale.

## A.4 Sound power

Determined per **ISO 3744:1994** (*Acoustics — Determination of sound power levels of
noise sources using sound pressure — Engineering method for an essentially free field
over a reflecting plane*) and **ISO 3745:2003** (*... Precision methods for anechoic
and hemi-anechoic rooms*).

Page workflow:

1. Choose the room: anechoic or hemi-anechoic chamber.
2. Define microphone positions and the measurement-surface geometry enclosing the
   source (hemisphere / box of known area).
3. Enter environmental conditions and room type.
4. Measure sound pressure at every position with the DAQ.
5. Record **two 1/3-octave spectra per point**: source running, and background alone.
6. Feed geometry + conditions into the **Post Analyzer** software.
7. Software returns the octave spectrum of **sound power** ("sound power flux") — the
   total acoustic watts radiated, independent of distance and room, so two products can
   be compared fairly.

Sound power ≠ sound pressure: pressure is what a mic hears at one point (depends on
distance and room); power is the source property derived by integrating pressure (or
intensity) over the enclosing surface.

## A.5 Loudness — sones and phons

Psychoacoustic loudness based on **equal-loudness contours**:

- **Phon** — a loudness *level*. A tone has a loudness level of *N* phon if it is
  perceived as equally loud as an *N* dB SPL tone at **1000 Hz**. By definition the
  phon and dB SPL scales coincide at 1 kHz.
- **Sone** — a linear loudness *ratio*. **40 phon ≡ 1 sone**; every **+10 phon doubles
  the sone value** (50 phon = 2 sone, 60 phon = 4 sone, …). Sones let you say "twice as
  loud," which decibels and phons cannot.

The page shows equal-loudness curves and defines the reference: "a sound is perceived
to be as loud as 40 dB at 1000 Hz" = 40 phon = 1 sone.

(The page stops at this classical definition. It does **not** mention **ISO 532-1
(Zwicker)** or **532-2 (Moore-Glasberg)** loudness models, nor the related
Zwicker **sharpness (acum)**, **roughness (asper)**, **fluctuation strength (vacil)**,
or **tonality** metrics. If full psychoacoustic sound-quality metrics are ever wanted
they are a separate implementation, not something this page covers.)

## A.6 Noise Criterion (NC) curves

A single-number room-noise rating. Measure the background-noise **octave-band spectrum
from 63 Hz to 8000 Hz** (1/1-octave), overlay it on the family of NC curves, and the
NC rating is the lowest curve the measured spectrum does not exceed in any band.
Used here for "measuring the background noise in a building" — e.g. is this room quiet
enough for its purpose.

## A.7 Acoustic control

Closed-loop generation, not analysis. **SISO** (single drive → single control mic) and
**MIMO** (multiple drives / mics) strategies drive a speaker or shaker so that the
measured sound/vibration **matches a user reference profile**, with **alarm thresholds**
and **abort limits** if the response strays. This is the vibration-control-lab use case
(reproduce a field noise spectrum in the lab), not a measurement of an unknown source.

## A.8 Microphones for acoustic acquisition

Selection parameters the page lists: **sensitivity** (mV/Pa — voltage produced per unit
sound pressure), **flat frequency response** over the band of interest, **high
signal-to-noise ratio** (low self-noise), **directionality** (omni vs directional),
and **environmental resilience** (temperature, humidity, dust).

Recommended: a **G.R.A.S. ½″ LEMO free-field microphone** for its wide temperature /
humidity tolerance. ("Free-field" = calibrated to be flat for sound arriving head-on,
after accounting for the mic body's own diffraction; the alternative is a
"pressure" or "random-incidence / diffuse-field" mic.)

Sensitivity trades against dynamic range — a more sensitive capsule resolves quieter
sound but clips sooner:

| Model | Sensitivity | Min SPL(A) | Max SPL(A) |
|---|---|---|---|
| PCB 378A12 | 0.25 mV/Pa | 60 dB | 182 dB |
| PCB 376A31 | 2 mV/Pa | 40 dB | 165 dB |
| PCB 378M12 | 10 mV/Pa | 26 dB | 159 dB |
| PCB 376A32 | 50 mV/Pa | 15.5 dB | 137 dB |
| GRAS 46AC | 12.5 mV/Pa | 20 dB | 164 dB |
| GRAS 46AZ | 50 mV/Pa | 17 dB | 138 dB |

All are **IEPE/ICP** (constant-current-powered) capsules and most support **TEDS**.

## A.9 Post-processing

- **Spectral analysis** — FFT (Fourier transform) of recorded time signals into the
  frequency domain.
- **Digital filtering** — for noise removal and isolating a frequency range of interest.
- **Statistical analysis** — "assess the variability of acoustic signals or identify
  anomalies in the data" (level distributions, percentile levels, outlier detection).
- **Time waveform recording** — raw time-domain capture for offline re-analysis
  (mentioned; little detail).

## A.10 Every standard named on the page

| Standard | Domain |
|---|---|
| ANSI S1.11:2004 | Octave / fractional-octave digital-filter specification |
| IEC 61260-1995 | Octave-band filters (international) |
| IEC 225-1966 | Octave-band filters (legacy) |
| DIN 45651 | Octave-band filters (legacy, German) |
| ISO 3744:1994 | Sound power — engineering method, free field over reflecting plane |
| ISO 3745:2003 | Sound power — precision method, anechoic / hemi-anechoic rooms |
| ISO 9001:2015 | Quality-management system (Crystal Instruments' own certification) |

---

# PART B — Relevance of each method to tile quality classification

`acoustic_node` today has only `signal_processing.py`
(`compute_rms`, `compute_fft`, `dominant_frequency`). The table below is the plan for
what to add and why.

Legend for **Verdict**:
**CORE** = implement, it directly discriminates good vs defective tiles ·
**SUPPORT** = implement, but for test-validity / environment qualification, not grading ·
**SKIP** = not applicable to this problem.

| Crystal method (Part A ref) | Verdict | How it applies to a tile ring / where it lands |
|---|---|---|
| **Fractional-octave-band analysis, 1/3 & 1/12 octave** (A.2) | **CORE** | The single best feature set. A ~30-value 1/3-octave vector (or ~120-value 1/12-octave) of the 5–250 ms ring window is a compact, mic-gain-tolerant spectral fingerprint. A crack shifts energy down, broadens peaks, and drains the high bands — all visible as a band-vector shift. New module `octave_bands.py`, ANSI S1.11 / IEC 61260 base-2 band edges. |
| **Octave / FFT waterfall** — level vs (log freq × time) (A.2, A.9) | **CORE** | This *is* the damping signature. Compute a spectrogram (or per-octave-band RMS decay) of the 0–500 ms window: a healthy tile's resonant bands decay slowly and evenly; a cracked tile has one or more bands that die within a few ms (energy leaks across the crack face). Per-band decay-rate becomes a feature. Module `decay.py` + reuse `octave_bands.py`. |
| **FFT spectral analysis** (A.9) | **CORE** | Already partly present. Extend: top-N resonant peaks + Q (sharpness) of each, spectral centroid / bandwidth / roll-off / flatness, inter-peak spacing. Module `features.py` built on existing `compute_fft`. |
| **Frequency weighting A / C / Z** (A.3) | **SUPPORT** | For NDT, analyse **Z (flat)** — defect energy is often >5 kHz where A-weighting attenuates 3–12 dB and would hide it. Keep **A-weighted level** only as one extra "does it sound duller to a human" scalar feature and for reporting. Module `weighting.py` (A/C/Z IIR per IEC 61672). |
| **Time weighting Fast / Impulse + L_AF, L_Aeq, L_peak** (A.3) | **CORE** | The **Impulse** time weighting (35 ms attack / 1500 ms release) is essentially a ring-energy-decay detector — a good-tile "ring" vs a dead "thud" separates cleanly on the Impulse-weighted level trace. `L_peak` catches the strike; `L_Aeq` over the ring window measures total radiated energy (a dull tile is quieter for the same strike). Module `sound_level.py`. |
| **Statistical levels L1 / L5 / L50 / L95 + statistical analysis** (A.3, A.9) | **SUPPORT** | Run on the **pre-hit** 100 ms noise window per tile: `L95` = conveyor/room floor, `L1`−`L95` spread = how bursty the background is. Feeds the existing SNR/RETEST gate in `Acoustic_Sorting_Subsystem.md` §9–10. Also use the "identify anomalies" idea across a *batch* of tiles to catch drift (striker wearing, mic moving). |
| **Loudness — phon / sone** (A.5) | **SUPPORT (optional)** | A cracked tile is quantifiably *less loud* for an identical strike. A single loudness-in-sones number is an intuitive, standardised "how dead does it sound" feature and reports well to non-experts. Classical equal-loudness-contour method only (page's level). Full **ISO 532-1 Zwicker loudness + sharpness/roughness** would be stronger but is out of this page's scope — log as a possible `AI_Software_Novelty.md` extension. |
| **Sound power — ISO 3744 / 3745** (A.4) | **SKIP** for grading | Needs a steady source, an enclosing measurement surface, and a (hemi-)anechoic room — none of which apply to a single impulse on a conveyor. *Marginal support use:* a one-time ISO-3744-style survey could characterise the finished test enclosure's own acoustic quality, but that is a commissioning task, not per-tile. |
| **Sound intensity / absorption (white-pink-noise)** (A.1) | **SKIP** | Two-mic intensity probes and speaker-driven absorption tests measure room/material acoustic properties, not tile integrity. Not relevant. |
| **Noise Criterion (NC) curves** (A.6) | **SUPPORT (one-time)** | Not a tile metric. Useful once, to rate the factory background and the enclosure interior (63 Hz–8 kHz octave spectrum → NC number) so the SNR budget in `Acoustic_Sorting_Subsystem.md` §5–6 is grounded in a real measured number. |
| **Acoustic control — SISO / MIMO closed loop** (A.7) | **SKIP** (borrow one idea) | We generate no sound, so closed-loop control does not apply. **Borrow the pattern:** Crystal's "**reference profile + alarm band + abort limit**" is exactly the reference-tile tolerance-band classifier in `Acoustic_Sorting_Subsystem.md` §19.4 — store a golden 1/3-octave + decay profile per tile type, flag deviations beyond an alarm band, reject beyond an abort band. |
| **Microphone selection: sensitivity, flat response, SNR, directionality, environment** (A.8) | **SUPPORT (hardware)** | Directly informs the mic decision in `Acoustic_Sorting_Subsystem.md` §3.2. The ring is impulsive and can be loud close-up → favour a **lower-sensitivity, higher-max-SPL** capsule (e.g. GRAS 46AZ-class) over a studio USB mic that clips. Want **flat to ≥16 kHz** and an **omni free-field** capsule fixed on-axis to the strike point. |
| **IEPE / ICP power + TEDS** (A.1, A.8) | **SUPPORT (hardware)** | The current plan (FIFINE K669B / miniDSP UMIK-1, both USB) is **not IEPE** and has no TEDS. Fine for the lab PoC. If the build moves to a measurement-grade ½″ capsule, the UNO Q / Mega has no IEPE input → needs a small IEPE conditioner or a USB audio interface with 200 V/4 mA CCP. Note in the wiring doc. |
| **Calibration — normalise to a reference value** (A.3) | **SUPPORT** | Adopt it: a **94 dB / 1 kHz pistonphone** (or 114 dB) sets counts→Pa once per session, so RMS/level features are in real dB SPL and portable across mics/machines — removes the "meaningless on any other mic" caveat currently on `trigger.rms_threshold`. Store the cal factor in `config.yaml`. |
| **TEDS auto-parameter read** (A.1) | **SKIP** (USB mic) | No TEDS path on a USB condenser. Revisit only with an IEPE capsule. |
| **Digital filtering for noise isolation** (A.9) | **CORE** | Already planned in `Acoustic_Sorting_Subsystem.md` §13 (300 Hz–18 kHz band-pass, optional notches). Keep. |
| **Time-waveform recording** (A.9) | **CORE (dataset)** | Persist every tile's raw WAV + feature row (currently deferred per TODO). Required before any reference profile or ML model can be built. |

---

# PART C — The tile acoustic analysis + classification pipeline

This merges the CORE / SUPPORT methods above with the mechanical sequence already in
`Acoustic_Sorting_Subsystem.md`. Steps 1–7 there (tile detect, conveyor stop, settle,
pre-hit record, ToF-triggered ball drop, ring record) are unchanged. This section is
everything from "we have a 0.5 s WAV" onward.

## C.1 Signal conditioning (per tile)

```text
raw mono clip, 48 kHz, 24-bit
  → apply calibration factor            (counts → Pa, from pistonphone; C-A.3 / A.8)
  → DC removal (x -= mean)
  → pre-hit window  = [-100 ms, 0]      → noise metrics, C.2
  → impact detect   = first |x| > 5·noise_RMS   → t0
  → ring window     = [t0+5 ms, t0+250 ms]
  → decay window    = [t0+5 ms, t0+500 ms]
  → band-pass 300 Hz–18 kHz (Butterworth, filtfilt)   (A.9)
  → optional notch at known conveyor tones
```

## C.2 Pre-hit noise / validity gate (SUPPORT methods)

Computed on the pre-hit window, drives RETEST not grade:

| Metric | Method (Part A ref) | RETEST if |
|---|---|---|
| Noise floor `L95`, `L50`, spread `L1−L95` | statistical levels (A.3) | `L95` above enclosure spec |
| Broadband noise RMS / SPL | RMS + calibration (A.3) | > configured ceiling |
| SNR = 20·log10(ring_RMS / noise_RMS) | (A.3) | < 10 dB |
| Impact found? single? not clipped? | peak level `L_peak` (A.3) | no impact / multi-impact / clipping |
| Dominant conveyor tones present in ring band | pre-hit FFT (A.9) | strong tone inside a tile resonance band |

## C.3 Feature extraction (CORE methods)

All computed on the conditioned ring / decay windows. This is the feature vector the
classifier consumes.

### C.3.1 Fractional-octave-band vector — `octave_bands.py`

- 1/3-octave band levels, nominal centres **25 Hz … 20 kHz** (IEC 61260 / ANSI S1.11
  base-2 edges), on the ring window → ~30 values in dB.
- Optionally 1/12-octave (~120 values) for finer resonance placement.
- Normalise by total band energy → **shape vector** (mic-gain-independent).
- Band ratios: `high(6–20k) / low(0.3–1k)`, `mid / low`, `high / total`.

### C.3.2 FFT spectral descriptors — `features.py`

| Feature | Meaning for a tile |
|---|---|
| `f_dominant` | fundamental ring mode; drops when cracked |
| `peaks[1..3]` freq + amplitude + **Q** (`f/Δf_-3dB`) | crack broadens peaks → lower Q |
| `spectral_centroid` | "brightness"; lower = duller = suspect |
| `spectral_bandwidth`, `spectral_flatness` | crack → broader, more noise-like |
| `spectral_rolloff_85%` | lower for a damped tile |
| `harmonic_ratio` / inter-peak regularity | irregular mode spacing = structural fault |

### C.3.3 Decay / damping — `decay.py` (the waterfall method, A.2)

| Feature | Method |
|---|---|
| `T20`, `T30` overall (time to −20 / −30 dB on the Hilbert envelope) | good tile rings long; crack damps fast |
| `decay_rate` (exp fit, dB/s) | single-number damping |
| **per-octave-band decay rate** | spectrogram / per-band RMS-vs-time slope — the discriminating feature: a crack kills specific bands fast |
| `energy_ratio_early/late` (0–50 ms vs 200–500 ms) | fast collapse ⇒ defect |

### C.3.4 Level / loudness scalars — `sound_level.py`, `weighting.py`

| Feature | Method (Part A ref) |
|---|---|
| `L_Zeq` ring, `L_Aeq` ring | equivalent level, Z and A weighted (A.3) |
| `L_peak` (C-weighted) | strike peak (A.3) |
| `L_AF_max`, `L_A_impulse_max` | Fast & Impulse time-weighted maxima (A.3) — "ring vs thud" |
| `loudness_sone` (optional) | equal-loudness-contour loudness (A.5) |
| `crest_factor` = peak / RMS | impulsiveness |

## C.4 Classification — "what quality does the noise show"

Three-stage, matching `Acoustic_Sorting_Subsystem.md` §19 and the charter's phased plan.

### Stage 1 — reference-profile tolerance bands (the "acoustic control" pattern, A.7)

Per **tile type** (size / thickness / body), build a golden profile from ≥30 known-good
tiles: mean ± σ of the normalised 1/3-octave vector, `f_dominant`, `T30`, per-band decay
rates, `spectral_centroid`, `L_Zeq`.

```text
for each new tile:
    if any C.2 validity gate fails            -> RETEST
    d = deviation(features, golden_profile)   # z-score / band-wise distance
    if d within ALARM band                    -> GRADE A   (sound, sonorous, matches)
    elif d within ABORT band                  -> GRADE B   (audible deviation, usable)
    else                                      -> REJECT    (dull / dead / wrong resonance)
```

Concrete first rules (tune on data — see `Acoustic_Sorting_Subsystem.md` §23):

| Rule | Outcome |
|---|---|
| `SNR < 10 dB` or impact invalid | RETEST |
| `f_dominant` outside `golden ± 15 %` | REJECT |
| `T30 < 0.6 × golden_T30` (damps too fast) | REJECT |
| `high/low band ratio < 0.5 × golden` | REJECT |
| any single octave band decays `> 3 ×` faster than golden | REJECT |
| `spectral_centroid` 1–2 σ low, everything else in band | GRADE B |
| all features within ALARM band | GRADE A |

### Stage 2 — feature-vector ML classifier

Once ≥ a few hundred labelled tiles (good + cracked + corner-broken) exist:
Random Forest / SVM / XGBoost on the C.3 feature vector → `{A, B, Reject}` +
probability. Keep the Stage-1 validity gate in front of it. Report feature importances
(which is the "why").

### Stage 3 — fusion with the camera grade

Per `project_charter.md` §5 the master fuses vision + acoustic. Simple policy:

| Camera | Acoustic | Final |
|---|---|---|
| A | A | **A** |
| A | B | **B** |
| A | Reject | **Reject** (acoustic catches hidden/internal cracks the camera can't see — this is the whole point of the acoustic station) |
| Reject | any | **Reject** |
| any | RETEST | **RETEST** (re-run acoustic; if it fails twice, grade on camera alone + flag) |

## C.5 Reporting / dashboard

Per tile, the acoustic node emits (extends `tile_record_to_dict()`-style dict):

```jsonc
{
  "seq": 42,
  "acoustic_grade": "B",
  "confidence": 0.78,
  "f_dominant_hz": 2180,
  "T30_ms": 190,
  "third_octave_db": [ ... ~30 values ... ],
  "band_decay_rate_db_s": { "500": -140, "1000": -95, ... },
  "L_Zeq_ring_db": 88.4,
  "loudness_sone": 12.1,
  "snr_db": 21.3,
  "deviation_from_golden": 1.9,
  "reasons": ["spectral_centroid 1.4σ low"],
  "wav_path": "data/acoustic_captures/tile_0042_...wav"
}
```

Live view: waveform + FFT (exists) **plus** the 1/3-octave bar chart with the golden
band overlaid, and the octave waterfall of the decay — the two plots that make a
"dull" tile obvious at a glance.

---

# PART D — Proposed code modules (`acoustic_node/python/acoustic/`)

All pure / synthetic-input-testable per Development Rule 1. Hardware stays in
`capture.py`.

| Module | Adds | Part A basis |
|---|---|---|
| `signal_processing.py` *(exists)* | keep `compute_rms/fft/dominant_frequency`; add `spectral_centroid`, `bandwidth`, `rolloff`, `flatness`, `peak_picking_with_Q` | A.9 |
| `octave_bands.py` *(new)* | `third_octave_levels()`, `n_octave_levels(n)`, IEC 61260 / ANSI S1.11 band edges, normalisation, band ratios | A.2 |
| `weighting.py` *(new)* | A / C / Z weighting IIR filters (IEC 61672), `apply_weighting()` | A.3 |
| `sound_level.py` *(new)* | `Leq`, `Lpeak`, Fast/Slow/Impulse exponential time weighting, statistical levels `Ln` | A.3 |
| `decay.py` *(new)* | Hilbert envelope, `T20/T30`, exp decay-rate fit, per-band decay rate from a spectrogram | A.2 waterfall |
| `calibration.py` *(new)* | pistonphone-tone → counts-per-Pa factor, load/store in `config.yaml`, `to_pascals()` / `to_db_spl()` | A.3, A.8 |
| `features.py` *(new)* | orchestrates C.3 → one flat feature dict per tile | C.3 |
| `reference_profile.py` *(new)* | build / load / compare golden profile per tile type; ALARM / ABORT bands | A.7 pattern |
| `classifier.py` *(new)* | Stage-1 rules now; Stage-2 sklearn model loader later; returns grade + reasons | C.4 |
| `config.yaml` *(extend)* | octave resolution, band range, weighting choice, calibration factor, golden-profile path, ALARM/ABORT thresholds, per-rule limits | — |

New dependencies: none required beyond `numpy` / `scipy` for Stages up to 1
(`scipy.signal` covers octave IIR filters, Hilbert, filter design).
`scikit-learn` only when Stage 2 starts.

---

# PART E — What to do first

1. **Calibration** — get a 94 dB/1 kHz pistonphone (or borrow one); implement
   `calibration.py`; put a real factor in `config.yaml`. Removes the biggest current
   caveat (every level threshold being mic-specific and meaningless elsewhere).
2. **`octave_bands.py` + `decay.py`** — the two CORE methods with the highest
   discrimination power. Unit-test with synthetic multi-tone + exponential-decay signals.
3. **Persist WAV + feature rows** (lift the TODO deferral) — nothing downstream
   (golden profile, ML) is possible without a dataset.
4. **Collect the calibration set** per `Acoustic_Sorting_Subsystem.md` §24.2 — needs
   real good *and* defective tiles from SMTW (also blocks the camera side; see TODO).
5. **`reference_profile.py` + Stage-1 `classifier.py`** — once ~30 good tiles of one
   type are recorded.
6. NC survey of the enclosure + factory (SUPPORT, one afternoon) to ground the SNR
   budget.
7. Defer: full ISO 532 loudness / sharpness / roughness, sound-power survey, any
   IEPE / TEDS hardware — none are on the critical path.

---

## Change log

| Date | Change |
|---|---|
| 2026-09-08 | Created. Reviewed the Crystal Instruments *Acoustic Analysis* page, documented every method + standard (Part A), assessed each for tile-NDT relevance (Part B), specified the analysis + classification pipeline (Part C) and the module plan (Part D). No code yet. |
