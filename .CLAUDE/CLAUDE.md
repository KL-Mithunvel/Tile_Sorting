# CLAUDE.md

> **IMPORTANT:** Read `CLAUDE-COMMON.md` first — it contains general must-follow instructions (companion files, deployment model, workflow, template structure). This file contains repo-specific instructions. Anything here overrides `CLAUDE-COMMON.md`.
>
> **Also read `PROJ_STARTER.md`** — it contains the owner's personal preferences (interaction rules, coding standards, tech stack choices, commit style). Copy its sections into any new project's `CLAUDE.md` User Rules alongside the rules from `CLAUDE-COMMON.md`.

---

## Project Overview

Automated ceramic/terracotta tile inspection, grading, sorting, and packing system. Combines camera-based visual inspection, acoustic tap-testing (solenoid hammer + microphone + FFT analysis), and dimensional measurement (ToF/laser sensors, or possibly the camera station itself) to grade tiles and route them to sorting/packing stations via a Cartesian gantry pick-and-place mechanism. Full system design lives in `documents/project/project_charter.md` and `documents/project/Automation_Architecture.md` — those documents are the source of truth for scope, hardware options, distributed compute architecture, and phased build plan; this file tracks what's actually built and how to run it.

- Owner: kl mithunvel (klm@smtw.in)
- Academic context: VIT Chennai BMEE497J/BMHA497J Project 1 (School of Mechanical Engineering, Mechatronics and Automation). 3-student team: Mithunvel KL (23BMH1029), Kanigalpula Teja Pavan Sai Adinarayana Gupta (23BMH1007), Madhumitha Vemula (23BMH1019). Guide: Dr. Jegadeeshwaran R (50204). See `documents/deliverables/` for the assessment schedule.
- Real-world basis: proposed for Sree Murugan Tile Works, a clay tile manufacturer currently doing inspection/grading/sorting/packing manually.
- License: see `LICENSE`
- Runtime: Python 3.13 (venv at repo root)
- Current build phase: Phase 1 (Laboratory Proof of Concept, charter §12.1) — acoustic capture only. Camera, dimensional, control, sorting, and database layers are not started.
- Entry point (acoustic module): `python -m acoustic.live_monitor`

---

## Documentation Architecture

Non-code project artifacts live under `documents/` at the repo root (added 2026-07-10).
Full conventions are in `documents/README.md` and `documents/deliverables/README.md` —
summary:

| Location | Contents |
|---|---|
| `documents/mech/` | CAD source files, drafted mechanical sheets (PDF), BOMs |
| `documents/electrical/` | Wiring/connection schematics, component datasheets, panel layout |
| `documents/programming/` | Flowcharts, pseudocode, dataset/model notes, test reports |
| `documents/project/` | System design source of truth: `project_charter.md`, `Automation_Architecture.md`, `Acoustic_Sorting_Subsystem.md`, `Camera_Sorting_Subsystem.md` |
| `documents/requirements/` | `requirements.md` — formal FR-xx/NFR-xx spec, traceable to `documents/project/` |
| `documents/deliverables/` | College-mandated coursework (VIT Chennai BMEE497J/BMHA497J) — reviews, guide evaluation, final report, daily logbook. See `documents/deliverables/README.md` for the assessment schedule and due dates. |

Rule: file new artifacts where they belong as part of the same commit that produces
them — a new schematic goes in `documents/electrical/schematics/`, not left loose at the
repo root; a new college submission goes under its review folder in
`documents/deliverables/`, not mixed into the engineering subfolders.

---

## Running the System

```bash
# Activate the venv first, every session (Windows/PowerShell)
venv\Scripts\activate

# List available microphone input devices (find the right index if the
# system default isn't the one you want — see Platform Constraints below)
python -m acoustic.live_monitor --list-devices

# Calibrate: prints live RMS level so you can set trigger.rms_threshold
# in acoustic/config.yaml. Run this first on any new machine/mic.
python -m acoustic.live_monitor --calibrate

# Continuous listen + auto-trigger + FFT plot on each detected sound
python -m acoustic.live_monitor

# Run tests
python -m pytest tests/ -v
```

There is no hardware/production mode yet — everything above runs on the dev laptop's built-in mic. No seed data or one-time setup beyond the venv.

---

## Architecture

Only the acoustic capture module exists so far. Camera, dimensional, control, decision/grading, sorting, and database layers from the full charter (`documents/project/project_charter.md` §5) are not started.

| File | Role |
|---|---|
| `acoustic/config.yaml` | All tunable audio/trigger parameters (device, sample rate, RMS threshold, timing) |
| `acoustic/signal_processing.py` | Pure functions: `compute_rms`, `compute_fft`, `dominant_frequency`. No I/O — synthetic-signal testable. |
| `acoustic/capture.py` | `TriggerDetector` (pure trigger-state logic, testable without a mic) + `AudioCapture` (thin `sounddevice` wrapper that feeds real audio blocks into it) |
| `acoustic/plotting.py` | `plot_waveform_and_spectrum()` — matplotlib waveform + FFT display |
| `acoustic/live_monitor.py` | CLI entry point: `--list-devices`, `--calibrate`, or continuous monitor mode |

### Data flow (current)

```text
Mic (sounddevice InputStream, background thread)
   |
   v
AudioCapture._callback()  — one block at a time
   |
   v
TriggerDetector.process_block()  — rolling RMS vs threshold,
   |                                 pre-trigger ring buffer, cooldown
   v
clip_queue (thread-safe)
   |
   v
live_monitor.run_monitor()  — main thread, blocks on queue.get()
   |
   v
plot_waveform_and_spectrum()  — blocks until plot window closed,
                                  audio capture continues in background
```

### Threading model

`sounddevice.InputStream` runs its callback on PortAudio's own background thread. `TriggerDetector` state is only ever touched from that one callback thread, so no locking is needed there. Completed clips cross into the main thread via a `queue.Queue`, which is thread-safe by design. The main thread blocks on `get_clip()` and then blocks again on `plt.show()` — while a plot window is open, the audio stream keeps running and can queue up further clips, which display one after another as windows are closed.

### Simulation vs real mode

Not applicable yet — this runs entirely on the dev laptop's built-in mic, no embedded/hardware target involved. `TriggerDetector` is hardware-independent by construction (see Development Rules), so once camera/solenoid/PLC pieces are added, the same dev-machine-first split should be repeated for each.

---

## Key Modules

### `acoustic/signal_processing.py`

- `compute_rms(samples: np.ndarray) -> float` — linear RMS (not dBFS). Raises `ValueError` on empty input.
- `compute_fft(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]` — Hann-windowed single-sided magnitude spectrum, returns `(frequencies_hz, magnitude)`. Raises `ValueError` on empty input.
- `dominant_frequency(frequencies, magnitude, min_freq_hz=20.0) -> float` — peak-magnitude frequency, ignoring bins below `min_freq_hz` (DC/near-DC leakage through the Hann window otherwise dominates). Raises `ValueError` if fewer than 2 bins or none above `min_freq_hz`.

No I/O, no side effects. Fully covered by `tests/test_signal_processing.py`.

### `acoustic/capture.py`

- `load_config(path=None) -> dict` — reads `acoustic/config.yaml` (or a given path).
- `list_input_devices() -> list[dict]` — wraps `sounddevice.query_devices()`, filtered to input-capable devices.
- `TriggerDetector(sample_rate, block_size, rms_threshold, pre_trigger_ms, capture_duration_s, cooldown_s)` — pure stateful trigger logic. `.process_block(block: np.ndarray) -> np.ndarray | None` returns a finished clip when a capture completes. No hardware I/O; covered by `tests/test_capture.py` using synthetic blocks.
- `AudioCapture(config: dict)` — opens the mic via `sounddevice.InputStream`, feeds blocks into an internal `TriggerDetector`, pushes finished clips onto `self.clip_queue`. `.start()` / `.stop()` / context-manager support. `.get_clip(timeout=None)` blocks on the queue. Real hardware I/O — not unit-tested, only smoke-tested manually.

### `acoustic/plotting.py`

- `plot_waveform_and_spectrum(clip: np.ndarray, sample_rate: int, block: bool = True) -> None` — two-panel matplotlib figure (waveform + FFT magnitude with dominant-frequency marker). Side effect: opens a GUI window, blocks on `plt.show()` by default.

### `acoustic/live_monitor.py`

CLI entry point (`argparse`). `--list-devices` prints devices and exits. `--calibrate` prints live RMS in a loop (Ctrl+C to stop) for threshold tuning. No flags: continuous listen-and-plot mode.

---

## Data Files

Nothing is persisted yet — captures are shown live and discarded (per explicit decision this session: save-to-disk is deferred until later). `acoustic/config.yaml` is the only data file, and it's git-tracked (it's configuration, not runtime output).

Planned, not yet built: a `data/` directory for saved WAV clips + extracted features once dataset collection starts (charter §22.1). Already excluded in `.gitignore` (`data/`, `*.wav`) so it's ready when that lands — audio/image datasets should never be committed to git.

---

## Platform Constraints

- Current dev target: Windows 11. `sounddevice`/PortAudio work cross-platform without code changes, but device names/indices in `acoustic/config.yaml`'s `device: null` (system default) are Windows-specific in practice right now — re-verify `--list-devices` output if this ever runs on Linux/Raspberry Pi.
- This machine enumerates several "Microphone Array" input devices (Realtek, 2ch/4ch variants, one reporting a 16000 Hz default sample rate). `device: null` picks whatever Windows considers the default; if `AudioCapture.start()` fails to open at `sample_rate: 44100`, pin an explicit device index from `--list-devices` in `config.yaml` rather than debugging the default.
- **Windows Microphone Enhancements** (AGC / noise suppression) must be manually disabled in Sound Control Panel → input device → Properties → Enhancements tab. This cannot be controlled from Python/PortAudio. Until confirmed disabled, RMS threshold and FFT shape are not trustworthy for calibration — fine for pipeline smoke-testing, not for real threshold tuning.
- No hardware dependencies yet (no GPIO/I2C/serial/solenoid/PLC in this repo). Those land with the acoustic tapping station, dimensional sensors, and control layer per the charter — each will need the dev-machine simulation split required by `CLAUDE-COMMON.md` when it's built.

---

## Deployment Notes

Not applicable yet — no hardware or remote target in active use. The charter's long-term plan (§12, §13) moves to Raspberry Pi for early integration and eventually an industrial PC + PLC; fill this section in when that transition actually starts, with a real dev-vs-target environment table rather than a speculative one.

---

## Known Technical Debt

- `acoustic/config.yaml`'s `trigger.rms_threshold: 0.02` is a placeholder, not a calibrated value. It has not been tuned against this machine's actual noise floor (run `--calibrate` to do that) and is meaningless on any other mic. Do not treat it as a real setting.
- Windows Microphone Enhancements have not been confirmed disabled as of this session (see Platform Constraints). Any RMS/FFT numbers collected before that's done should be treated as pipeline smoke-test data only, not real signal characterization.
- `acoustic/capture.py`'s `AudioCapture` (the real hardware path) has only been smoke-tested via `--list-devices`; `--calibrate` and full trigger-to-plot flow have not yet been run against a live mic in this session — needs a manual run to confirm end-to-end.
- `documents/requirements/requirements.md` was newly formalized (2026-07-10) from `documents/project/project_charter.md` and `Automation_Architecture.md` — it has not yet been reviewed section-by-section against the full charter for completeness, so treat it as a first pass, not an exhaustive spec.

---

## Development Rules

1. **Hardware I/O stays separate from pure logic.** Any module touching real hardware (mic, camera, sensors, actuators) must isolate the hardware-facing code into a thin wrapper around pure, synthetic-input-testable logic — see `acoustic/capture.py`'s `TriggerDetector` (pure) vs `AudioCapture` (hardware wrapper) as the pattern to repeat for camera/dimensional/control modules.
2. **No hardcoded device/sensor parameters.** Sample rate, device selection, thresholds, timing — all of it goes in a module-local `config.yaml`, never inline in source (extends the `CLAUDE-COMMON.md` hardware-address rule to audio params).
3. **Any threshold calibrated on prototype/substitute hardware (e.g. laptop mic instead of the measurement mic + solenoid tapper) is provisional** and must be explicitly called out as such in code/docs until recalibrated on the real acoustic station (charter §6.2, §15.2, §18.1).
4. **New non-code artifacts get filed under the matching `documents/` subfolder in the same commit as the work that produced them** — see Documentation Architecture above. Don't leave CAD exports, schematics, or college submissions loose at the repo root or bundled into an unrelated folder.

---

## Project TODO List

Tracked in `TODO.md` (per `CLAUDE-COMMON.md` companion-file rule) rather than duplicated here, to avoid the two files drifting out of sync.

---

## User Rules

> Copy **Standard User Rules** from `CLAUDE-COMMON.md` verbatim here, then copy all sections from `PROJ_STARTER.md` below those. Add project-specific overrides at the bottom, clearly labelled.

See `CLAUDE-COMMON.md` → Standard User Rules and `PROJ_STARTER.md` for the full rule set.

### Project-Specific Overrides

_None — add below as needed._
