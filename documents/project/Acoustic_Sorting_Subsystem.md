# Acoustic Sorting Subsystem Documentation

## For Ceramic / Terracotta Tile Inspection Using Solenoid Impact and Microphone Capture

## 1. Purpose

The acoustic sorting subsystem is used to detect cracks, hidden defects, internal voids, weak structure, or abnormal tile behavior by striking each tile with a repeatable mechanical impact and analyzing the resulting ringing sound.

A good tile is expected to produce a clear, sonorous, repeatable acoustic response. A cracked or defective tile may produce a duller sound, lower energy response, faster decay, shifted resonance frequency, or abnormal spectral pattern.

The system consists of:

```text
Conveyor belt
Tile positioning station
Solenoid striker
Microphone / vibration sensor
Processing unit
Sorting decision logic
Reject / grade diverter
```

The main challenge is that the conveyor, motor, bearings, tile movement, solenoid body, and surrounding factory environment will introduce noise. Therefore, the system must use controlled timing, mechanical isolation, filtering, windowing, and repeatable impact conditions.

---

# 2. Acoustic Test Station Overview

## 2.1 Basic Mechanical Sequence

```text
1. Tile arrives on conveyor.
2. Tile is detected by photoelectric sensor / limit switch / ToF sensor.
3. Conveyor slows or stops at the acoustic test position.
4. Tile is aligned using side guides or stopper.
5. Tile rests on fixed support pads.
6. Microphone starts recording.
7. Solenoid striker hits the tile.
8. System records the ringing response.
9. Signal is filtered and analyzed.
10. Acoustic features are extracted.
11. Tile is classified as Good / Defective / Retest.
12. Conveyor moves tile to correct sorting path.
```

## 2.2 Recommended Physical Layout

```text
               Acoustic enclosure
        ┌──────────────────────────────┐
        │                              │
        │        Microphone             │
        │       50–150 mm from tile     │
        │              ↓               │
        │        Solenoid striker       │
        │              ↓               │
        │     ┌──────────────────┐     │
        │     │      Tile        │     │
        │     └──────────────────┘     │
        │      ▲                ▲      │
        │ Rubber pad       Rubber pad  │
        │                              │
        └──────────────────────────────┘
```

The microphone should be fixed in position. The tile must be struck at the same point each time. The tile support condition must also remain constant.

---

# 3. Recommended Hardware

## 3.1 Impact Mechanism

Recommended for prototype:

```text
24 V push-pull solenoid
Nylon / Delrin striker tip
Mechanical stroke adjustment
Spring return
MOSFET solenoid driver
Flyback diode or TVS diode
Separate 24 V power supply
```

The solenoid should not directly hit the tile with a bare metal plunger. A replaceable plastic striker tip is recommended to avoid damaging the tile and to make the impact more consistent.

## 3.2 Microphone

Possible microphones:

```text
FIFINE K669B USB condenser microphone
miniDSP UMIK-1 USB measurement microphone
I2S MEMS microphone
```

For prototype work, the FIFINE K669B can be used. For better repeatability and acoustic measurement quality, the miniDSP UMIK-1 is preferred.

## 3.3 Optional Additional Sensor

A microphone captures air sound, but conveyor noise can affect it. Therefore, adding a contact vibration sensor is strongly recommended.

Recommended second sensor:

```text
Piezo disc sensor
Contact microphone
Vibration accelerometer
Force sensor near striker
```

Best prototype combination:

```text
USB microphone + piezo contact sensor
```

The microphone gives the air sound. The piezo gives the tile/support vibration response. Comparing both can improve defect detection and noise rejection.

---

# 4. Capture Sequence

## 4.1 Timing Sequence

For each tile, record a short sound segment around the impact event.

Recommended sequence:

```text
t = -100 ms    Start recording background noise
t = 0 ms       Solenoid trigger signal
t = 0–10 ms    Solenoid motion and impact start
t = 5–250 ms   Main ringing analysis window
t = 250–500 ms Decay analysis window
t = 500 ms     Stop recording
```

Recommended recording duration:

```text
0.5 seconds per tile
```

Recommended sample rate:

```text
48 kHz minimum
```

Recommended bit depth:

```text
16-bit minimum
24-bit preferred if available
```

## 4.2 Why Record Before the Hit?

Recording should begin before the solenoid strike because the conveyor and environment already produce background noise. The pre-hit section is used to estimate the noise floor.

Example:

```text
Pre-hit noise window:    -100 ms to 0 ms
Post-hit signal window:  5 ms to 250 ms
Decay window:            250 ms to 500 ms
```

The pre-hit recording helps identify:

```text
conveyor motor noise
belt rubbing noise
bearing noise
fan noise
factory background noise
microphone self-noise
```

## 4.3 Actual Impact Detection

The electrical solenoid trigger is not always the same as the actual impact time. The solenoid may take a few milliseconds to move before the striker touches the tile.

Therefore, the system should detect the actual impact from the recorded waveform.

Method:

```text
1. Start recording before solenoid trigger.
2. Trigger solenoid.
3. Search for sudden rise in signal amplitude.
4. Mark that point as actual impact time.
5. Start analysis window slightly after that point.
```

This improves consistency.

---

# 5. Noise Sources

The main expected noise sources are:

```text
Conveyor motor hum
Belt friction
Roller bearing vibration
Tile sliding or rubbing
Solenoid electromagnetic click
Solenoid mechanical body vibration
Air compressor / pneumatic noise
Nearby machines
Room echo
Microphone stand vibration
Electrical noise from solenoid switching
```

Because the conveyor belt is present, noise removal is vital. The acoustic station must be designed so that the tile ringing signal is much stronger than the conveyor noise.

---

# 6. Noise Reduction Strategy

Noise reduction should not depend only on software. It should use both mechanical and signal-processing methods.

## 6.1 Mechanical Noise Reduction

Recommended mechanical actions:

```text
Use a small acoustic enclosure around the test station.
Stop or slow the conveyor during measurement.
Use rubber isolation mounts under the test station.
Physically separate the microphone stand from the solenoid mount.
Use fixed rubber/polyurethane tile support pads.
Avoid microphone contact with the conveyor frame.
Use foam lining inside the acoustic enclosure.
Use shielded cables for analog sensors.
Keep solenoid power wires away from microphone/data wires.
Use a separate 24 V solenoid power supply.
```

## 6.2 Conveyor Handling During Capture

Best option:

```text
Stop conveyor → hit tile → record sound → restart conveyor
```

This gives the cleanest signal.

Second-best option:

```text
Slow conveyor → mechanically isolate tile → hit → record
```

Avoid hitting while the tile is freely moving on the belt because the tile support condition changes and the belt noise contaminates the sound.

## 6.3 Acoustic Enclosure

A simple enclosure can greatly improve data quality.

Recommended features:

```text
MDF / plywood / acrylic / metal enclosure
Foam or acoustic absorber inside
Small opening for tile entry and exit
Microphone fixed inside enclosure
Solenoid mounted with vibration isolation
Access panel for maintenance
```

The enclosure does not need to be perfect. Even partial enclosure helps reduce factory noise.

## 6.4 Electrical Noise Reduction

Solenoid switching can introduce electrical noise into the microphone or processing board.

Use:

```text
Flyback diode across solenoid
TVS diode for faster suppression if needed
MOSFET gate resistor
Opto-isolated driver if needed
Separate power supply for solenoid
Common ground only at one controlled point
Shielded USB/audio cables
Ferrite beads on microphone USB cable if needed
```

---

# 7. Signal Processing Pipeline

The processing pipeline should be consistent for every tile.

## 7.1 Complete Signal Flow

```text
Raw microphone signal
        ↓
Pre-hit noise estimation
        ↓
Impact detection
        ↓
Cut analysis window
        ↓
DC offset removal
        ↓
Band-pass filtering
        ↓
Windowing
        ↓
FFT
        ↓
Spectral feature extraction
        ↓
Decay feature extraction
        ↓
Classification
        ↓
Good / Defective / Retest
```

---

# 8. Raw Audio Recording

## 8.1 Recommended Audio Settings

```text
Sample rate: 48,000 Hz
Channels: Mono
Duration: 0.5 s
Format: WAV
Bit depth: 16-bit or 24-bit
```

At 48 kHz, the highest theoretical measurable frequency is:

```text
48,000 / 2 = 24,000 Hz
```

Practical useful range:

```text
200 Hz to 20,000 Hz
```

For terracotta and ceramic tiles, the most useful audible ringing information is expected mainly in:

```text
300 Hz to 12,000 Hz
```

Higher-frequency information may still be useful up to 20 kHz.

---

# 9. Pre-Hit Noise Estimation

Before impact, use the first 100 ms as the noise reference.

Example:

```text
Noise window: -100 ms to 0 ms before impact
```

Calculate:

```text
Noise RMS
Noise spectrum
Noise energy in frequency bands
Dominant conveyor noise frequencies
```

This helps the system understand the noise condition before each tile.

## 9.1 Noise Floor Check

If the noise before impact is too high, the tile result should be marked as unreliable.

Example rule:

```text
If pre-hit noise RMS > allowed threshold:
    Mark tile as RETEST
```

This prevents wrong rejection due to sudden external noise.

---

# 10. Impact Detection

The actual impact is detected using a sudden rise in amplitude.

Basic method:

```text
1. Compute short-term energy of the signal.
2. Find first point where energy rises above noise threshold.
3. Mark this as impact time.
```

Threshold example:

```text
Impact threshold = noise RMS × 5
```

If the signal does not cross this threshold, the hit may have failed.

Possible causes:

```text
Solenoid did not hit correctly
Tile was not present
Microphone failed
Tile was too far
Background noise too high
```

In that case:

```text
Mark as RETEST
```

---

# 11. Signal Windowing

After detecting impact, do not analyze the entire recording. Analyze only the useful ringing part.

Recommended windows:

```text
Impact spike rejection: 0–5 ms after impact
Main FFT window:        5–250 ms after impact
Decay window:           5–500 ms after impact
```

The first few milliseconds may contain mechanical click, striker bounce, and harsh impact noise. The useful tile resonance is usually after the initial contact spike.

## 11.1 Window Selection

For FFT:

```text
Use 5 ms to 250 ms after impact
```

For decay:

```text
Use 5 ms to 500 ms after impact
```

If the tile sound is very short, reduce the window. If the tile rings longer, increase the decay window.

---

# 12. DC Offset Removal

Before filtering or FFT, remove DC offset:

```text
x = x - mean(x)
```

This centers the waveform around zero and avoids low-frequency distortion in the spectrum.

---

# 13. Filtering

## 13.1 Band-Pass Filter

Use a band-pass filter to remove low-frequency conveyor rumble and unwanted very high frequency noise.

Recommended first filter:

```text
200 Hz to 20,000 Hz band-pass
```

For very noisy conveyor systems:

```text
300 Hz to 18,000 Hz band-pass
```

Reason:

```text
Below 200–300 Hz:
    conveyor motor hum, belt rumble, frame vibration

Above 18–20 kHz:
    microphone noise, weak useful signal, aliasing risk
```

## 13.2 Notch Filters

If the conveyor motor produces strong fixed-frequency hum, use notch filters.

Common frequencies:

```text
50 Hz electrical hum
100 Hz harmonic
150 Hz harmonic
Motor-specific frequency peaks
```

In India, electrical mains frequency is 50 Hz, so useful notch filters may be:

```text
50 Hz
100 Hz
150 Hz
```

However, if your band-pass starts at 200 Hz, these are already removed.

If the conveyor has a strong whining noise at a fixed frequency, for example 1.2 kHz, add a narrow notch filter:

```text
Notch at conveyor noise frequency
```

But do not overuse notch filters. Too many notches may remove useful tile information.

---

# 14. Window Function Before FFT

Before FFT, multiply the selected signal by a window function. This reduces spectral leakage.

Recommended window:

```text
Hann window
```

Other possible windows:

```text
Hamming window
Blackman window
```

For initial testing, use Hann.

Signal flow:

```text
Cut ringing window
Remove mean
Band-pass filter
Apply Hann window
Perform FFT
```

---

# 15. FFT Analysis

FFT converts the time-domain sound into frequency-domain spectrum.

## 15.1 Frequency Resolution

Frequency resolution depends on window length.

Formula:

```text
Frequency resolution = sample rate / number of samples
```

Example:

```text
Sample rate = 48,000 Hz
Window length = 0.25 s
Number of samples = 12,000

Frequency resolution = 48,000 / 12,000
                     = 4 Hz
```

This is good enough for tile resonance analysis.

## 15.2 FFT Output

The FFT gives:

```text
Frequency bins
Magnitude at each frequency
```

From this, extract:

```text
dominant peak frequency
secondary peaks
energy in frequency bands
spectral centroid
band ratios
spectral roll-off
```

---

# 16. Spectral Feature Extraction

The classifier should not use raw audio directly at first. Extract simple measurable features.

## 16.1 Main Features

Recommended features:

```text
Dominant frequency
Dominant peak amplitude
Top 3 peak frequencies
Top 3 peak amplitudes
Total spectral energy
Low-frequency energy
Mid-frequency energy
High-frequency energy
Energy ratios
Spectral centroid
Spectral bandwidth
Spectral roll-off
Decay time
RMS amplitude
Crest factor
Signal-to-noise ratio
```

## 16.2 Frequency Bands

Start with these bands:

```text
Band 1: 300 Hz – 1 kHz
Band 2: 1 kHz – 3 kHz
Band 3: 3 kHz – 6 kHz
Band 4: 6 kHz – 10 kHz
Band 5: 10 kHz – 20 kHz
```

For each band, calculate energy:

```text
Band energy = sum of FFT magnitude squared inside that band
```

Then calculate ratios:

```text
High-to-low energy ratio
Mid-to-low energy ratio
High-to-total energy ratio
```

These are often more reliable than absolute amplitude because microphone gain may vary.

## 16.3 Dominant Frequency

The dominant frequency is the frequency with the highest spectral magnitude in the useful range.

Example:

```text
Search range: 300 Hz to 20 kHz
Dominant frequency = frequency of highest peak
```

A cracked tile may show:

```text
lower dominant frequency
reduced peak amplitude
broader peak
faster decay
less high-frequency content
```

## 16.4 Spectral Centroid

Spectral centroid describes the “brightness” of the sound.

Higher centroid:

```text
clearer / sharper / brighter ring
```

Lower centroid:

```text
duller / more damped sound
```

Formula:

```text
Spectral centroid = sum(frequency × magnitude) / sum(magnitude)
```

## 16.5 Spectral Bandwidth

Spectral bandwidth shows how spread out the frequency content is.

A clean ringing tile may have clear narrow peaks. A noisy or defective response may have broader, less defined energy distribution.

## 16.6 Spectral Roll-Off

Spectral roll-off is the frequency below which a chosen percentage of spectral energy is contained.

Common setting:

```text
85% roll-off frequency
```

A dull tile may have lower roll-off frequency.

---

# 17. Time-Domain Feature Extraction

Frequency features are important, but time-domain features are also useful.

## 17.1 RMS Amplitude

RMS measures overall signal energy.

```text
RMS = root mean square of waveform
```

Low RMS may mean:

```text
weak impact
bad microphone placement
dull tile
cracked tile
bad recording
```

## 17.2 Peak Amplitude

Peak amplitude detects maximum signal level.

Use this to identify:

```text
overloaded/clipped recording
failed impact
unusually loud hit
```

## 17.3 Crest Factor

Crest factor:

```text
Peak amplitude / RMS amplitude
```

It gives information about how impulsive the signal is.

## 17.4 Decay Time

Decay time is very important for tile testing.

A good tile usually rings longer.
A cracked tile often damps faster.

Simple method:

```text
1. Calculate envelope of waveform.
2. Find maximum envelope after impact.
3. Measure time required to fall by 20 dB or 30 dB.
```

Possible decay features:

```text
Time to -10 dB
Time to -20 dB
Time to -30 dB
Exponential decay rate
```

## 17.5 Signal-to-Noise Ratio

Use pre-hit noise and post-hit signal.

```text
SNR = 20 × log10(signal RMS / noise RMS)
```

If SNR is too low, the result is unreliable.

Example:

```text
If SNR < 10 dB:
    Mark as RETEST
```

---

# 18. Conveyor Noise Removal

Because a conveyor belt is present, noise handling must be part of both mechanical and software design.

## 18.1 Best Conveyor Strategy

Best method:

```text
Stop conveyor during acoustic measurement.
```

Sequence:

```text
Tile reaches test station
Conveyor stops
Tile settles for 100–200 ms
Microphone records background noise
Solenoid hits tile
Microphone records ringing
Conveyor restarts
```

This is far better than trying to filter out moving conveyor noise.

## 18.2 Tile Settling Delay

After the conveyor stops, wait briefly before hitting.

Recommended delay:

```text
100 ms to 300 ms
```

This allows belt vibration and tile sliding noise to reduce.

## 18.3 Background Subtraction

Use pre-hit noise spectrum to estimate conveyor noise.

Method:

```text
1. Record pre-hit noise.
2. Compute FFT of noise window.
3. Compute FFT of tile ringing window.
4. Subtract or compensate for noise spectrum.
```

Simple method:

```text
Clean spectrum = signal spectrum - noise spectrum
```

Better method:

```text
Use noise spectrum only to calculate SNR and reliability.
```

Avoid aggressive subtraction initially because it may create artificial features.

## 18.4 Noise Gating

Before analysis, reject low-level sections below a threshold.

Example:

```text
If signal amplitude < noise RMS × 2:
    Ignore that region
```

This helps remove background noise after the ringing has died away.

## 18.5 Frequency Masking

If a conveyor produces constant noise at known frequencies, ignore those frequency bins.

Example:

```text
Conveyor noise at 740 Hz and 1480 Hz
Ignore narrow bands around those frequencies
```

But this must be done carefully, because tile resonance may also occur near those frequencies.

## 18.6 Multi-Microphone Option

If conveyor noise is severe, use two microphones:

```text
Mic 1 near tile
Mic 2 away from tile, capturing background noise
```

Then subtract or compare:

```text
Tile mic signal - background mic signal
```

This is more advanced and may be useful in production.

## 18.7 Contact Sensor Option

A piezo/contact sensor can reduce dependence on air sound.

Recommended approach:

```text
Microphone detects acoustic ring
Piezo detects structural vibration
Only classify when both agree
```

This is very useful in a noisy conveyor environment.

---

# 19. Classification Logic

Start with rule-based classification before using machine learning.

## 19.1 Result Categories

Use three acoustic result categories:

```text
GOOD
DEFECTIVE
RETEST
```

Do not classify every uncertain tile as defective. Use RETEST for unreliable cases.

## 19.2 Retest Conditions

Mark tile as RETEST if:

```text
Pre-hit noise too high
Impact amplitude too low
Impact amplitude too high
Audio clipped
SNR too low
Dominant frequency missing
Multiple hits detected
Tile not properly positioned
Conveyor vibration too high
```

## 19.3 Simple Rule-Based Classification

Example:

```text
If SNR < threshold:
    RETEST

Else if decay time is too short:
    DEFECTIVE

Else if high-frequency energy ratio is too low:
    DEFECTIVE

Else if dominant frequency is outside expected range:
    DEFECTIVE

Else:
    GOOD
```

## 19.4 Reference-Based Classification

For each tile size/type, collect reference data from known good tiles.

Create baseline values:

```text
Average dominant frequency
Allowed frequency range
Average band energy ratios
Average decay time
Allowed decay range
Average spectral centroid
```

Then compare each new tile against the reference.

Example:

```text
If dominant frequency deviates by more than allowed limit:
    possible defect

If decay time is below allowed limit:
    possible defect

If high-frequency energy is below allowed limit:
    possible defect
```

## 19.5 Machine Learning Classification

After collecting enough data, use machine learning.

Recommended first ML models:

```text
Random Forest
Support Vector Machine
Logistic Regression
XGBoost
```

Input features:

```text
dominant frequency
top 3 peaks
band energies
band ratios
spectral centroid
spectral bandwidth
roll-off frequency
RMS
decay time
SNR
```

Avoid deep learning at the beginning. First build a strong feature-based dataset.

---

# 20. Dataset Collection Plan

## 20.1 Tile Classes

Collect data for:

```text
Known good tiles
Clearly cracked tiles
Corner-broken tiles
Tiles with internal defects if available
Different thicknesses
Different tile sizes
Different moisture/temperature conditions if relevant
```

## 20.2 Repeated Hits

For each tile, collect multiple hits.

Recommended:

```text
5 to 10 hits per tile
```

This helps measure repeatability.

## 20.3 Metadata to Store

For every recording, store:

```text
Tile ID
Tile size
Tile type
Tile thickness
Known condition
Impact force setting
Solenoid voltage
Microphone type
Microphone distance
Support type
Conveyor state
Date and time
Raw WAV file path
Extracted features
Final label
```

## 20.4 File Naming

Example:

```text
tile_0001_good_hit_01.wav
tile_0001_good_hit_02.wav
tile_0002_crack_hit_01.wav
tile_0003_cornerbreak_hit_01.wav
```

Also keep a CSV file:

```text
tile_id,label,hit_no,dominant_freq,decay_time,snr,band1_energy,band2_energy,...
```

---

# 21. Recommended First Test Algorithm

## 21.1 Basic Algorithm

```text
For each tile:

1. Stop conveyor.
2. Wait 200 ms for vibration to settle.
3. Start recording.
4. Record 100 ms of background noise.
5. Trigger solenoid.
6. Record 400 ms after trigger.
7. Detect actual impact point.
8. Extract signal from 5 ms to 250 ms after impact.
9. Remove DC offset.
10. Apply band-pass filter from 300 Hz to 18 kHz.
11. Apply Hann window.
12. Perform FFT.
13. Extract frequency features.
14. Extract decay features.
15. Calculate SNR.
16. Classify tile.
17. Log result.
18. Send decision to conveyor sorting system.
```

## 21.2 Recommended Initial Parameters

```text
Sample rate: 48 kHz
Recording duration: 0.5 s
Pre-hit noise: 100 ms
Settling delay after conveyor stop: 200 ms
Analysis start: 5 ms after impact
FFT analysis end: 250 ms after impact
Decay analysis end: 500 ms after impact
Band-pass filter: 300 Hz to 18 kHz
Window function: Hann
Minimum acceptable SNR: 10 dB
```

These values should be tuned after experiments.

---

# 22. Example Python Processing Logic

This is the expected software flow.

```python
import numpy as np
from scipy.signal import butter, filtfilt, windows, hilbert
from scipy.fft import rfft, rfftfreq

def bandpass_filter(x, fs, lowcut=300, highcut=18000, order=4):
    nyq = fs / 2
    low = lowcut / nyq
    high = highcut / nyq
    b, a = butter(order, [low, high], btype="band")
    return filtfilt(b, a, x)

def calculate_rms(x):
    return np.sqrt(np.mean(x ** 2))

def detect_impact(audio, fs, noise_window_samples):
    noise = audio[:noise_window_samples]
    noise_rms = calculate_rms(noise)

    threshold = noise_rms * 5

    for i in range(noise_window_samples, len(audio)):
        if abs(audio[i]) > threshold:
            return i, noise_rms

    return None, noise_rms

def extract_fft_features(x, fs):
    x = x - np.mean(x)

    win = windows.hann(len(x))
    xw = x * win

    spectrum = np.abs(rfft(xw))
    freqs = rfftfreq(len(xw), 1 / fs)

    valid = (freqs >= 300) & (freqs <= 18000)
    freqs_v = freqs[valid]
    spec_v = spectrum[valid]

    dominant_index = np.argmax(spec_v)
    dominant_freq = freqs_v[dominant_index]
    dominant_amp = spec_v[dominant_index]

    total_energy = np.sum(spec_v ** 2)

    def band_energy(f1, f2):
        mask = (freqs_v >= f1) & (freqs_v < f2)
        return np.sum(spec_v[mask] ** 2)

    band1 = band_energy(300, 1000)
    band2 = band_energy(1000, 3000)
    band3 = band_energy(3000, 6000)
    band4 = band_energy(6000, 10000)
    band5 = band_energy(10000, 18000)

    spectral_centroid = np.sum(freqs_v * spec_v) / (np.sum(spec_v) + 1e-12)

    cumulative_energy = np.cumsum(spec_v ** 2)
    rolloff_point = 0.85 * cumulative_energy[-1]
    rolloff_freq = freqs_v[np.searchsorted(cumulative_energy, rolloff_point)]

    return {
        "dominant_freq": dominant_freq,
        "dominant_amp": dominant_amp,
        "total_energy": total_energy,
        "band1_energy": band1,
        "band2_energy": band2,
        "band3_energy": band3,
        "band4_energy": band4,
        "band5_energy": band5,
        "high_to_low_ratio": (band4 + band5) / (band1 + band2 + 1e-12),
        "spectral_centroid": spectral_centroid,
        "rolloff_freq": rolloff_freq,
    }

def extract_decay_features(x, fs):
    envelope = np.abs(hilbert(x))
    envelope = envelope / (np.max(envelope) + 1e-12)

    envelope_db = 20 * np.log10(envelope + 1e-12)

    def time_to_drop(db_drop):
        target = -db_drop
        indices = np.where(envelope_db <= target)[0]
        if len(indices) == 0:
            return None
        return indices[0] / fs

    return {
        "time_to_10db": time_to_drop(10),
        "time_to_20db": time_to_drop(20),
        "time_to_30db": time_to_drop(30),
    }

def process_tile_audio(audio, fs):
    audio = np.asarray(audio)

    if audio.ndim > 1:
        audio = audio[:, 0]

    audio = audio - np.mean(audio)

    noise_window_samples = int(0.100 * fs)
    impact_index, noise_rms = detect_impact(audio, fs, noise_window_samples)

    if impact_index is None:
        return {
            "status": "RETEST",
            "reason": "Impact not detected"
        }

    analysis_start = impact_index + int(0.005 * fs)
    analysis_end = impact_index + int(0.250 * fs)
    decay_end = impact_index + int(0.500 * fs)

    if decay_end > len(audio):
        return {
            "status": "RETEST",
            "reason": "Recording too short"
        }

    signal_window = audio[analysis_start:analysis_end]
    decay_window = audio[analysis_start:decay_end]

    signal_rms = calculate_rms(signal_window)
    snr_db = 20 * np.log10((signal_rms + 1e-12) / (noise_rms + 1e-12))

    if snr_db < 10:
        return {
            "status": "RETEST",
            "reason": "Low SNR",
            "snr_db": snr_db
        }

    filtered_signal = bandpass_filter(signal_window, fs)
    filtered_decay = bandpass_filter(decay_window, fs)

    fft_features = extract_fft_features(filtered_signal, fs)
    decay_features = extract_decay_features(filtered_decay, fs)

    features = {
        **fft_features,
        **decay_features,
        "snr_db": snr_db,
        "signal_rms": signal_rms,
        "noise_rms": noise_rms,
    }

    return {
        "status": "OK",
        "features": features
    }
```

---

# 23. Initial Rule-Based Decision Example

This is only an example. Actual thresholds must be learned from real tile data.

```python
def classify_tile(features, reference):
    if features["snr_db"] < 10:
        return "RETEST"

    if features["dominant_freq"] < reference["min_dominant_freq"]:
        return "DEFECTIVE"

    if features["dominant_freq"] > reference["max_dominant_freq"]:
        return "DEFECTIVE"

    if features["time_to_20db"] is not None:
        if features["time_to_20db"] < reference["min_time_to_20db"]:
            return "DEFECTIVE"

    if features["high_to_low_ratio"] < reference["min_high_to_low_ratio"]:
        return "DEFECTIVE"

    return "GOOD"
```

The reference should be created from known good tiles of the same size and material.

---

# 24. Practical Calibration Procedure

## 24.1 Mechanical Calibration

Before collecting data:

```text
Set striker position.
Set striker stroke.
Set solenoid voltage.
Fix tile support pads.
Fix microphone distance.
Fix microphone angle.
Fix enclosure position.
```

## 24.2 Acoustic Calibration

Collect baseline recordings:

```text
10 recordings with no tile and conveyor stopped
10 recordings with no tile and conveyor running
10 recordings with tile but no hit
10 recordings with known good tile and hit
10 recordings with known defective tile and hit
```

This helps identify the effect of conveyor and environment.

## 24.3 Repeatability Test

For one known good tile:

```text
Hit same tile 20 times.
Calculate variation in dominant frequency.
Calculate variation in energy bands.
Calculate variation in decay time.
```

If variation is too high, fix the mechanical setup before improving the software.

---

# 25. Important Engineering Rules

The acoustic result is only meaningful if the test condition is repeatable.

Control these:

```text
Same impact location
Same impact force
Same striker tip
Same tile support points
Same microphone position
Same conveyor state
Same analysis window
Same filtering settings
Same tile type reference
```

Do not mix different tile sizes or thicknesses into one acoustic threshold. Each tile type needs its own reference profile.

---

# 26. Recommended Development Roadmap

## Stage 1: Manual Data Study

```text
Use solenoid striker.
Use microphone.
Stop conveyor during test.
Record good and cracked tiles.
Plot waveform and FFT.
Check if clear differences exist.
```

## Stage 2: Feature Extraction

```text
Extract dominant frequency.
Extract band energies.
Extract decay time.
Extract SNR.
Create CSV dataset.
```

## Stage 3: Rule-Based Sorting

```text
Use thresholds from known good tiles.
Classify as GOOD / DEFECTIVE / RETEST.
Test repeatability.
```

## Stage 4: Add Conveyor Noise Handling

```text
Record conveyor noise.
Add enclosure.
Add settling delay.
Add SNR-based retest.
Add notch filters if needed.
```

## Stage 5: Add Piezo / Contact Sensor

```text
Compare microphone and piezo response.
Use both for classification.
Improve reliability in noisy environment.
```

## Stage 6: Machine Learning

```text
Train Random Forest or SVM using extracted features.
Validate on unseen tiles.
Deploy model on UNO Q / Raspberry Pi / industrial PC.
```

---

# 27. Final Recommended Prototype Setup

```text
Controller / compute:
Arduino UNO Q or Raspberry Pi 5

Microphone:
FIFINE K669B for early prototype
miniDSP UMIK-1 for better measurement

Impact:
24 V push-pull solenoid
Nylon/Delrin striker tip
MOSFET driver
Flyback protection

Mechanical:
Small acoustic enclosure
Fixed tile stopper
Rubber support pads
Conveyor stop during test
200 ms settling delay

Signal processing:
48 kHz recording
300 Hz to 18 kHz band-pass filter
Impact detection
5–250 ms FFT window
Hann window
Spectral feature extraction
Decay-time measurement
SNR-based retest logic
```

The first goal is not perfect classification. The first goal is to prove that good and defective tiles produce repeatably different acoustic signatures under controlled impact conditions.

