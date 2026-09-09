# CLAUDE.md

> **IMPORTANT:** Read `CLAUDE-COMMON.md` first — it contains general must-follow instructions (companion files, deployment model, workflow, template structure). This file contains repo-specific instructions. Anything here overrides `CLAUDE-COMMON.md`.
>
> **Also read `PROJ_STARTER.md`** — it contains the owner's personal preferences (interaction rules, coding standards, tech stack choices, commit style). Copy its sections into any new project's `CLAUDE.md` User Rules alongside the rules from `CLAUDE-COMMON.md`.

---

## Project Overview

Automated ceramic/terracotta tile inspection, grading, sorting, and packing system. Combines camera-based visual inspection, acoustic tap-testing (currently planned as a laser/ToF-triggered ball-drop impactor + microphone + FFT analysis — see charter §6.2 Decision; a push-pull solenoid striker was the original concept and is kept as background), and dimensional measurement (ToF/laser sensors, or possibly the camera station itself) to grade tiles and route them to sorting/packing stations via a Cartesian gantry pick-and-place mechanism. Full system design lives in `documents/project/project_charter.md` and `documents/project/Automation_Architecture.md` — those documents are the source of truth for scope, hardware options, distributed compute architecture, and phased build plan; this file tracks what's actually built and how to run it.

- Owner: kl mithunvel (klm@smtw.in)
- Academic context: VIT Chennai BMEE497J/BMHA497J Project 1 (School of Mechanical Engineering, Mechatronics and Automation). 3-student team: Mithunvel KL (23BMH1029), Kanigalpula Teja Pavan Sai Adinarayana Gupta (23BMH1007), Madhumitha Vemula (23BMH1019). Guide: Dr. Jegadeeshwaran R (50204). See `documents/deliverables/` for the assessment schedule.
- Real-world basis: proposed for Sree Murugan Tile Works (SMTW), a clay tile manufacturer currently doing inspection/grading/sorting/packing manually. Tile size/weight range not yet obtained from SMTW — see Known Technical Debt.
- License: see `LICENSE`
- Runtime: Python 3.13 (venv at repo root)
- Current build phase: Phase 1 (Laboratory Proof of Concept, charter §12.1) — acoustic capture and camera vision pipeline exist, plus a standalone acoustic analysis/labelling workbench (`Acoustic-Analysis/` submodule, v0.1); dimensional, control, sorting, and database layers are not started. None of it has run against real tiles or the real measurement hardware yet.
- Entry point (acoustic module): `python -m acoustic.live_monitor`, run from `acoustic_node/python/` (see Architecture below — the module moved out of a repo-root `acoustic/` package into this App Bricks-shaped node folder on 2026-08-03).
- Entry point (camera module): `python -m camera.live_dashboard`, run from `camera_node/python/` — opens a WiFi-reachable Flask dashboard (added 2026-08-07, see Architecture below).
- Every UNO Q station (`camera_node/`, `acoustic_node/`, `pick_place_node/`) is organized as an **Arduino App Bricks** project (`app.yaml` + `sketch/` + `python/`), matching Arduino's own `app-bricks-examples` convention, so each can be opened in Arduino App Lab. All three now have real Python-side code (`pick_place_node/`'s is a digital-twin HMI prototype + bench bring-up tooling — no gantry firmware yet). Every `sketch/` is a stub except `acoustic_node/sketch/sketch.ino` (real FSM, uncompiled). The App Lab deploy path (`arduino-app-cli`) was verified on the lab board 2026-09-01 — see Deployment Notes. Only one physical UNO Q board exists.

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
| `documents/project/` | System design source of truth: `project_charter.md`, `Automation_Architecture.md`, `Acoustic_Sorting_Subsystem.md`, `Acoustic_Analysis_Methods.md`, `Camera_Sorting_Subsystem.md` |
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

# The acoustic module's CLI runs from inside acoustic_node/python/ (its App
# Bricks node folder) — see Architecture below for why.
cd acoustic_node\python

# List available microphone input devices (find the right index if the
# system default isn't the one you want — see Platform Constraints below)
python -m acoustic.live_monitor --list-devices

# Calibrate: prints live RMS level so you can set trigger.rms_threshold
# in acoustic_node/python/acoustic/config.yaml. Run this first on any new
# machine/mic.
python -m acoustic.live_monitor --calibrate

# Continuous listen + auto-trigger + FFT plot on each detected sound
python -m acoustic.live_monitor

# Run tests (from the repo root — pytest.ini adds acoustic_node/python to
# sys.path, so `from acoustic.capture import ...` in tests/ still works)
cd ..\..
python -m pytest tests/ -v
```

```bash
# The camera module's CLI runs from inside camera_node/python/, same pattern.
cd camera_node\python

# Opens a Flask dashboard at http://0.0.0.0:5000/ — reachable from any
# browser on the same WiFi network, not just localhost.
python -m camera.live_dashboard

# Offline: process a saved video file instead of a live webcam — prints the
# total tile flow count + a numbered per-tile crack/corner result for each
# tile that crossed. --report writes the same as JSON.
python -m camera.process_video path\to\footage.mp4 --report report.json
```

```bash
# Calibrating camera_node's vision thresholds against real tile photos in
# data/ — see development/README.md. Run from the repo root.
venv\Scripts\activate
python development\tile_param_tuner.py    # interactive GUI, one photo at a time
python development\analyze_dataset.py     # batch: recommends values from all of data/
```

There is no hardware/production mode yet — everything above runs on the dev laptop's built-in mic / webcam. No seed data or one-time setup beyond the venv.

---

## Architecture

Acoustic capture, the camera vision pipeline, and a pick-and-place digital-twin HMI prototype exist so far. The pick-and-place gantry control layer is fully *designed* (`documents/programming/pick_place_control_protocol.md` — custom firmware + ASCII line protocol) but no firmware or motion-control code is written. Dimensional, conveyor, decision/grading fusion, sorting execution, and database layers from the full charter (`documents/project/project_charter.md` §5) are not started.

**Repo layout (since 2026-08-03):** every planned UNO Q station gets its own top-level
node folder, following the Arduino **App Bricks** convention (`app.yaml` + `sketch/` +
`python/` — see Arduino's `app-bricks-examples` repo) so each can be opened as an app in
Arduino App Lab:

| Folder | Status |
|---|---|
| `acoustic_node/` | Real code — `python/acoustic/` is the working, tested module (below), migrated unchanged from the old repo-root `acoustic/`. `sketch/` (laser/ToF trigger + ball-drop release) and `python/main.py`'s App Lab wiring are stubs, unverified on hardware. |
| `camera_node/` | Real code (added 2026-08-07) — `python/camera/` is a working, tested vision pipeline + WiFi dashboard (below). `sketch/` and `python/main.py`'s App Lab wiring are still stubs. |
| `pick_place_node/` | Partial (2026-08-17 / 09-01) — `python/pick_place/` is a working **digital-twin HMI prototype** (mock gantry simulator + Three.js browser twin + matplotlib desktop twin; `MockGantrySimulator` unit-tested). `bench_tests/stepper_smoke_test/` is a standalone `arduino-app-cli` app to confirm the 4 CNC-shield steppers move (checklist task 0.5). Gantry control **architecture** is fully designed (`documents/programming/pick_place_control_protocol.md` — custom firmware + ASCII line protocol; motor/driver/PSU hardware all chosen) but no real firmware or motion-control Python exists. `sketch/` + `python/main.py` still stubs. |

Conveyor stays on the Arduino Mega (not App-Lab-class, not part of this convention).

`development/` (added 2026-08-11) is a separate, dev-only top-level folder — **not** a
node, not part of the App Bricks convention above. It holds tooling for calibrating
`camera_node`'s vision thresholds against real tile photos (an interactive GUI tuner and
a batch analysis script) before those values get copied into
`camera_node/python/camera/config.yaml`. See `development/README.md`.

`camera_models/` (added 2026-08-20, reorganized into this shared parent folder
2026-08-26 — previously `cam_yolo/`/`cam_vit/` sat loose at the repo root) is another
dev-only top-level folder, **not** an App Bricks node and **not** wired into
`camera_node`'s live pipeline — model training/evaluation experiments only, grouped here
so new tile-grade-classification architectures don't keep littering the repo root.
Whether/how any of these models' weights get deployed into `camera_node` (or onto the
UNO Q directly, e.g. via Edge Impulse — see Deployment Notes) is a separate, later
decision.

`Acoustic-Analysis/` (added 2026-09-08) is a **git submodule** —
<https://github.com/KL-Mithunvel/Acoustic-Analysis>, MIT-licensed, its own repo /
history / releases. It is a standalone Windows desktop app (Tkinter) for **acoustic
data collection, analysis, and labelling**: record/import tile-tap clips, run the full
DSP suite from `documents/project/Acoustic_Analysis_Methods.md` (octave bands, decay,
spectral descriptors, level metrics), visualise and compare them, assign a quality
class, and export a labelled feature dataset. That dataset later trains a tile-grade
model; only the trained model — not this app — is copied into `acoustic_node`. It is
**not** an App Bricks node, **not** on the App Bricks convention, and **not** imported
by `acoustic_node` — code moves the other way, by hand, copy-as-needed (mirrors the
`camera_models/` relationship to `camera_node`). To work on it: `cd Acoustic-Analysis`
and use its own git; Tile_Sorting only records the pinned commit. After pulling
Tile_Sorting fresh, run `git submodule update --init` to populate the folder.

**Status (as of 2026-09-09): v0.1 built and working end-to-end** — full pure-DSP suite
(`dsp/`: conditioning, spectrum, octave bands, Schroeder decay, IEC 61672 weighting,
sound level, filter chain, spectral-subtraction denoise, NC/Ln environment), feature
extraction + validity gate, `classify/` reference profile + rule-based grader, SQLite
dataset store + WAV I/O, a headless CLI (`python -m acoustic_analysis.cli`), and a
12-screen Tkinter GUI (dark instrument-look shell, left sidebar nav, opens on a Home
launcher/session-summary screen). 133 tests. **Not done / not trusted:** never run
against a real microphone; no pistonphone calibration measured (levels are relative, not
dB SPL); no real good-vs-defective tile recordings so the grader and every `config.yaml`
threshold are unvalidated; `dsp/loudness.py` (phon/sone) deferred; no PyInstaller build.
See `Acoustic-Analysis/TODO.md` and its `.CLAUDE/CLAUDE.md` Known Technical Debt.

| Folder | Status |
|---|---|
| `Acoustic-Analysis/` | Submodule — standalone acoustic analysis + data-labelling desktop app (independent repo). **v0.1 built** (DSP suite, feature extraction, rule-based grader, SQLite dataset, CLI, 12-screen Tkinter GUI; 133 tests) as of 2026-09-09. Pending: real-mic run, pistonphone calibration, real-tile threshold tuning. |
| `camera_models/cam_yolo/` | Real code — fine-tunes a YOLO26 classification model (`yolo26s-cls`, Ultralytics) on the tile-grade dataset. 85.5% top1 on its own val split. See `camera_models/cam_yolo/README.md`. |
| `camera_models/cam_vit/` | Real code — fine-tunes `google/vit-base-patch16-224-in21k` (HuggingFace `transformers`) on the same dataset, matching the augmentation config of the Roboflow-hosted `tile-grade-classification` model but with real exportable weights (Roboflow's hosted ViT training doesn't export weights). 93.4% top1 (76-image val split) trained on the original dataset; retrained 2026-08-26 on an offline-augmented copy (`development/augment_dataset.py`) with no measurable improvement (92.1% on the same val split, apples-to-apples) — see `camera_models/cam_vit/README.md`. |

| File | Role |
|---|---|
| `acoustic_node/python/acoustic/config.yaml` | All tunable audio/trigger parameters (device, sample rate, RMS threshold, timing, `trigger.mode`, `hardware_trigger`, `tap_mechanism`) |
| `acoustic_node/python/acoustic/signal_processing.py` | Pure functions: `compute_rms`, `compute_fft`, `dominant_frequency`. No I/O — synthetic-signal testable. |
| `acoustic_node/python/acoustic/capture.py` | `TriggerDetector` (pure RMS-crossing trigger logic) + `AudioCapture` (thin `sounddevice` wrapper; picks `TriggerDetector` or `HardwareTapDetector` based on `config["trigger"]["mode"]`, exposes `notify_tap()`) |
| `acoustic_node/python/acoustic/hardware_trigger.py` | `HardwareTapDetector` — same pre-trigger-ring-buffer shape as `TriggerDetector`, but the capture window starts on an external `notify_tap()` call instead of an RMS crossing. Pure logic (thread-safe via an internal lock), testable with synthetic blocks. |
| `acoustic_node/python/acoustic/tap_sequencer.py` | `TapSequencer` — pure FSM (`WAIT_TILE → SETTLE → ARM → ARM_SETTLE → RELEASE → COOLDOWN`) mirroring `sketch.ino`'s ToF-triggered dual-solenoid (ARM+LOCK) ball-drop timing. Testable with synthetic `dt`/tile-presence steps; also used by `live_monitor.py` to simulate tap events with no hardware attached. |
| `acoustic_node/python/acoustic/plotting.py` | `plot_waveform_and_spectrum()` — matplotlib waveform + FFT display |
| `acoustic_node/python/acoustic/live_monitor.py` | CLI entry point: `--list-devices`, `--calibrate`, or continuous monitor mode. When `config.yaml`'s `trigger.mode` is `"simulated"`, also runs a background `TapSequencer`-driven fake tile arrival that calls `AudioCapture.notify_tap()`. |
| `acoustic_node/python/main.py` | Arduino App Lab entry point (`arduino.app_utils.App.run()`) — unverified stub. Has a `poll_bridge_and_capture()` function shaped to drive `AudioCapture.notify_tap()` from the sketch's `tapCount` bridge variable, but it's not wired into `App.run()` yet since the bridge-read API (`read_tap_count()`) is itself unverified. |
| `acoustic_node/sketch/sketch.ino` | MCU-side ToF-triggered dual-solenoid (ARM+LOCK) tap-sequencer state machine, e-stop handling. Real logic now (not an empty stub), but never compiled/run on hardware — see `documents/electrical/schematics/acoustic_station_wiring.md`. |
| `camera_node/python/camera/config.yaml` | All tunable vision parameters (device index, HSV segmentation range, Canny thresholds, crack/corner thresholds, dashboard host/port) — see camera_node/README.md for the full list |
| `camera_node/python/camera/segmentation.py` | `segment_tile()` — isolate the tile from the background via HSV threshold. No I/O — synthetic-image testable. |
| `camera_node/python/camera/crack_detection.py` | `detect_cracks()` — grayscale → blur → Canny → keep long/thin contours → measure length + severity. No I/O — synthetic-image testable. |
| `camera_node/python/camera/corner_detection.py` | `detect_broken_corner()` — contour area vs. its own bounding rect area (`fill_ratio`) *plus* a distance-transform-based check on how deep any gap reaches (`missing_extent_fraction`) — the latter catches diagonal chips the former misses (see Key Modules below). Converts missing area/depth to real inches given the tile's known size. No I/O — synthetic-contour testable. |
| `camera_node/python/camera/tile_tracker.py` | `TileTracker` — debounced presence/absence state machine, counts tiles crossing the frame. No I/O — synthetic-sequence testable. |
| `camera_node/python/camera/pipeline.py` | `process_tile()` — wires the three detectors above into one `TileRecord` + a first-pass rule-based grade. `tile_record_to_dict()` — shared JSON serialization, used by both the dashboard and `process_video.py`. |
| `camera_node/python/camera/snapshot.py` | `save_tile_snapshot()` — persists a departed tile's photo to `data/camera_captures/` (config: `capture_snapshots`), named by sequence number. I/O only, not unit-tested. |
| `camera_node/python/camera/capture.py` | `WebcamCapture` — thin OpenCV `VideoCapture` wrapper for a live device. `VideoFileCapture` — same shape, reads a saved video file instead (for `process_video.py`). Real hardware/file I/O, not unit-tested. |
| `camera_node/python/camera/worker.py` | `CameraWorker` + `SharedState` — background thread wiring capture → segmentation → tracker → pipeline (+ snapshot save), thread-safe latest-value store for the dashboard. Tracks the *largest-area* sighting of each tile while it's crossing, not just the last frame before departure. |
| `camera_node/python/camera/process_video.py` | `process_video_file()` + CLI — offline counterpart to `worker.py`'s loop: runs the same pipeline against a saved video file, reports total tile flow count + a numbered per-tile crack/corner result for each tile that crossed. Not unit-tested; smoke-tested against a synthetic video (see `development/`). |
| `camera_node/python/camera/dashboard.py` + `templates/dashboard.html` | Flask app: `/video_feed` (MJPEG stream), `/api/status` (JSON), `/` (dashboard page). Serves on `0.0.0.0` so it's reachable over WiFi. |
| `camera_node/python/camera/live_dashboard.py` | CLI entry point, mirrors `acoustic_node`'s `live_monitor.py` |
| `pick_place_node/python/pick_place/config.yaml` | Twin/workspace parameters — gantry envelope, patrol path, poll interval, dashboard host/port |
| `pick_place_node/python/pick_place/mock_state.py` | `MockGantrySimulator` — pure deterministic gantry-motion simulator, `.advance(dt_s)` given elapsed time (stand-in for a real `GantryWorker`). No I/O; unit-tested (`tests/test_pick_place_mock_state.py`) with synthetic time steps, same pattern as `TriggerDetector`. |
| `pick_place_node/python/pick_place/worker.py` | `TwinWorker` + `SharedState` — background thread advancing the simulator on a wall-clock tick into a thread-safe latest-value store. Thin timing wrapper, not unit-tested (same reasoning as `CameraWorker`). |
| `pick_place_node/python/pick_place/dashboard.py` + `templates/twin.html` | Flask app: `/` (Three.js digital-twin page), `/api/pose` (JSON live pose + vacuum state). Serves on `0.0.0.0`. Three.js vendored under `static/vendor/three/`. |
| `pick_place_node/python/pick_place/twin_prototype.py` | CLI entry point — mock gantry + WiFi twin dashboard, no real command routing. Mirrors the other nodes' `live_*.py` shape. |
| `pick_place_node/python/pick_place/desktop_twin.py` | Same mock motion rendered in a local matplotlib 3D window instead of a browser — no Flask, no network. |
| `pick_place_node/sketch/sketch.ino` | Real gantry firmware — **empty stub**. Design in `documents/programming/pick_place_control_protocol.md`. |
| `pick_place_node/bench_tests/stepper_smoke_test/` | Standalone `arduino-app-cli` bench app (sketch + `python/main.py` + README) that pulses X, Y+A(clone), Z to confirm all 4 CNC Shield V3.10 steppers move. Not the real firmware; sits outside the node's App Bricks project on purpose. Push with `tools/uno_q/push-stepper-test.bat`. |
| `pytest.ini` | `pythonpath = acoustic_node/python, camera_node/python, pick_place_node/python`, so `tests/` can `import acoustic` / `import camera` / `import pick_place` unchanged |

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

### Camera vision pipeline (data flow)

```text
Webcam (cv2.VideoCapture, CameraWorker's own background thread)
   |
   v
segment_tile()  — HSV threshold isolates the tile from the background
   |
   v
TileTracker.process_frame()  — debounced presence state machine;
   |                            fires once when a tile finishes crossing
   v
process_tile()  — on tile departure only: detect_cracks() + detect_broken_corner()
   |               on the largest-area isolated tile region seen while it was
   |               present, grade_tile()
   v
save_tile_snapshot()  — (if capture_snapshots.enabled) writes that region's
   |                      photo to data/camera_captures/, tagged with seq
   v
SharedState  — thread-safe: latest annotated JPEG frame + tile_count + recent TileRecords
   |
   v
Flask dashboard  — /video_feed (MJPEG, every frame) and /api/status (JSON,
                     polled by the page every second), served on 0.0.0.0
```

Every frame gets segmented and re-encoded to JPEG for the live stream, but the
crack/corner pipeline only runs once per tile — on the frame where `TileTracker` confirms
the tile has departed, using the *largest-area* region seen while it was present (not
just the last frame before departure, which can be partially exited/motion-blurred at the
frame edge).

`camera/process_video.py` runs this same segment → track → process_tile → snapshot chain
synchronously against a saved video file instead of a live device + Flask dashboard —
see its section in Key Modules below.

### Threading model (camera)

Unlike `sounddevice`'s callback-driven model, OpenCV's `VideoCapture` is read
synchronously, so `CameraWorker` spins its own background thread (`threading.Thread`,
daemon) that owns the whole per-frame pipeline. `SharedState` is the only thing shared
across threads (the worker thread writes, Flask's request-handling threads read), guarded
by a single `threading.Lock` — reads/writes are "latest value wins," not a queue, since
the dashboard only ever wants the most recent frame/status, not a backlog.

### Simulation vs real mode

Not applicable yet — the acoustic module runs on the dev laptop's built-in mic, the camera module on a dev laptop webcam, and `pick_place_node/`'s twin on a `MockGantrySimulator`; no embedded/hardware target is involved for any of them. `TriggerDetector`, the `camera/` pure modules, and `MockGantrySimulator` are hardware-independent by construction (see Development Rules) — the same pure-logic / thin-wrapper split repeats as `acoustic_node/sketch/` (laser/ToF + ball-drop), `camera_node/sketch/` (tile-presence trigger, if any), and `pick_place_node/`'s real firmware + `gantry_backend.py` are built.

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
- `TriggerDetector(sample_rate, block_size, rms_threshold, pre_trigger_ms, capture_duration_s, cooldown_s)` — pure stateful RMS-crossing trigger logic. `.process_block(block: np.ndarray) -> np.ndarray | None` returns a finished clip when a capture completes. No hardware I/O; covered by `tests/test_capture.py` using synthetic blocks.
- `AudioCapture(config: dict)` — opens the mic via `sounddevice.InputStream`, feeds blocks into an internal detector, pushes finished clips onto `self.clip_queue`. Which detector depends on `config["trigger"]["mode"]`: `"rms"` (default) uses `TriggerDetector`; `"hardware"`/`"simulated"` use `HardwareTapDetector` (below), and `.notify_tap()` forwards to it (raises `RuntimeError` if called in `"rms"` mode). `.start()` / `.stop()` / context-manager support. `.get_clip(timeout=None)` blocks on the queue. Real hardware I/O — not unit-tested, only smoke-tested manually.

### `acoustic/hardware_trigger.py`

- `HardwareTapDetector(sample_rate, block_size, pre_trigger_ms, capture_duration_s, cooldown_s)` — same pre-trigger-ring-buffer/`process_block()` shape as `TriggerDetector`, but the capture window starts when `.notify_tap()` is called instead of on an RMS crossing. `.notify_tap()` is expected to be called from a different thread than `.process_block()` (e.g. a bridge-polling loop vs. the audio callback thread) — guarded by an internal `threading.Lock`. Pure logic otherwise; covered by `tests/test_acoustic_hardware_trigger.py` using synthetic blocks.

### `acoustic/tap_sequencer.py`

- `TapSequencer(settle_s, arm_s, arm_settle_s, lock_pulse_s, cooldown_s)` — pure FSM mirroring `acoustic_node/sketch/sketch.ino`'s ToF-triggered dual-solenoid (ARM+LOCK) ball-drop timing: `WAIT_TILE → SETTLE → ARM → ARM_SETTLE → RELEASE → COOLDOWN → WAIT_TILE`. `.advance(dt_s, tile_present) -> TapState` is the only entry point (mirrors `pick_place/mock_state.py`'s `MockGantrySimulator.advance()`); `tile_present` must go False then True again (an edge) to arm a new cycle. `TapState.tap_fired` is `True` for exactly one call, the instant the LOCK solenoid opens (entering `RELEASE`) — that's the real impact moment, not the earlier ToF detection. No I/O; covered by `tests/test_acoustic_tap_sequencer.py` with synthetic time steps. Also used by `live_monitor.py` to simulate tap events with no ToF/solenoid hardware attached (`config.yaml`'s `trigger.mode: "simulated"`).

### `acoustic/plotting.py`

- `plot_waveform_and_spectrum(clip: np.ndarray, sample_rate: int, block: bool = True) -> None` — two-panel matplotlib figure (waveform + FFT magnitude with dominant-frequency marker). Side effect: opens a GUI window, blocks on `plt.show()` by default.

### `acoustic/live_monitor.py`

CLI entry point (`argparse`). `--list-devices` prints devices and exits. `--calibrate` prints live RMS in a loop (Ctrl+C to stop) for threshold tuning. No flags: continuous listen-and-plot mode. When `config.yaml`'s `trigger.mode` is `"simulated"`, `run_monitor()` also starts a background thread (`run_simulated_tap_driver()`) that drives a `TapSequencer` against a fake periodic tile arrival and calls `AudioCapture.notify_tap()` on each release.

### `camera/segmentation.py`

- `segment_tile(frame_bgr, hsv_lower, hsv_upper, min_area_px, morph_kernel_size=5) -> TileRegion | None` — HSV color-range threshold + largest-contour selection. Returns `None` if nothing large enough matches. `TileRegion` carries the bbox, area, contour, mask, and cropped image. No I/O; covered by `tests/test_camera_segmentation.py` with synthetic colored rectangles.

### `camera/crack_detection.py`

- `detect_cracks(tile_bgr, canny_low, canny_high, min_crack_length_px, min_aspect_ratio, minor_severity_max_length_px, blur_kernel_size=5) -> CrackResult` — grayscale → Gaussian blur → Canny → keeps only contours that are both long enough and elongated enough (aspect ratio) to be a crack rather than texture noise or a compact defect. Measures length from the winning contour's `minAreaRect`, grades severity against a length threshold. Raises `ValueError` on an empty image. No I/O; covered by `tests/test_camera_crack_detection.py` with synthetic drawn lines.

### `camera/corner_detection.py`

- `detect_broken_corner(contour, min_fill_ratio, max_missing_extent_fraction=1.0, tile_size_inches=None) -> CornerResult` — two independent broken-corner checks, OR'd together: (1) `fill_ratio` (tile's actual contour area / its own `minAreaRect` area) — a low ratio means area is missing; and (2) `missing_extent_fraction` (added 2026-08-11, fixing a real gap) — how far the deepest point of the gap between the actual contour and the ideal rectangle reaches, as a fraction of the tile's side length, via a distance transform. **Why both are needed**: `fill_ratio` alone under-catches diagonal/triangular corner chips — the realistic way ceramic actually breaks — because a triangular chip removes much less area than a square notch reaching the same distance into the tile. Confirmed empirically: a chip whose two legs each reach halfway across the tile's edge only dropped `fill_ratio` to ~0.87, above the production `min_fill_ratio` (0.83), so it would not have been flagged; `missing_extent_fraction` catches it (measures ~0.35 there, well above `config.yaml`'s `max_missing_extent_fraction: 0.22`). If `tile_size_inches` is given (config: 9.0, these tiles are 9x9in), `CornerResult` also reports `missing_area_sq_inches`/`missing_depth_inches` — real physical measurements of the break, using the tile's own detected pixel size as the scale reference (no separate calibration needed). Does not localize *which* corner is broken. Raises `ValueError` on a degenerate (zero-area) contour. No I/O; covered by `tests/test_camera_corner_detection.py`, including the diagonal-chip case above and the tile-size conversion.

### `camera/tile_tracker.py`

- `TileTracker(min_present_frames, min_absent_frames)` — pure debounced state machine. `.process_frame(tile_detected: bool) -> bool` returns `True` exactly once per tile, on the frame its presence is confirmed to have ended. `.tile_count` / `.confirmed_present` properties. No I/O; covered by `tests/test_camera_tile_tracker.py` with synthetic presence sequences.

### `camera/pipeline.py`

- `process_tile(seq, region, ...) -> TileRecord` — runs `detect_cracks()` + `detect_broken_corner()` on one `TileRegion` and grades it (`grade_tile()`: any broken corner or major crack → Reject, minor crack → Grade B, else Grade A — a first-pass rule, not the master's fused final grade). `TileRecord.seq` is the tile's sequence number in this run's flow (1st, 2nd, ... tile to cross), and doubles as its identifier — carried alongside that tile's crack/corner results in the same record. `TileRecord.snapshot_path` is set by callers after `camera/snapshot.py` persists the tile's photo (`None` if snapshot saving is off). Pure glue, not separately unit-tested itself (exercised via the smoke test below and indirectly by the modules it calls) — `grade_tile()` and `tile_record_to_dict()` are unit-tested directly (`tests/test_camera_pipeline.py`).
- `tile_record_to_dict(record) -> dict` — the JSON/API-friendly view of a `TileRecord` (everything except the raw pixel array). Shared by `dashboard.py`'s `/api/status` and `process_video.py`'s `--report`, so both report a tile the same way.

### `camera/snapshot.py`

- `save_tile_snapshot(record, output_dir) -> Path` — writes `record.tile_bgr` to `<output_dir>/tile_<seq>_<timestamp>.jpg`. I/O only, not unit-tested (mirrors `capture.py`'s hardware-wrapper modules). `resolve_output_dir(config)` anchors `capture_snapshots.output_dir` (config-relative, e.g. `"data/camera_captures"`) to the repo root regardless of current working directory.

### `camera/capture.py` / `camera/worker.py`

- `WebcamCapture(config)` — thin `cv2.VideoCapture` wrapper around a live device (`start()`/`read_frame()`/`stop()`, context-manager support). Real hardware I/O, not unit-tested.
- `VideoFileCapture(video_path)` — same shape as `WebcamCapture` but opens a saved video file instead of a live device; `read_frame()` raises `StopIteration` at end-of-file rather than blocking (a file has a defined end). Used by `process_video.py`, not by the live dashboard.
- `CameraWorker(capture, config, state)` — background thread: reads a frame, segments it, feeds `TileTracker`, and on tile departure runs the pipeline against the *largest-area* region seen while that tile was present (not just the last frame before departure — typically the most centered/least-blurred view), saves a snapshot if `capture_snapshots.enabled`, and publishes an annotated JPEG + results into `SharedState`. `SharedState` is the thread-safe latest-value store the Flask app reads from. Real hardware I/O + threading, not unit-tested (mirrors `AudioCapture`).

### `camera/process_video.py`

- `process_video_file(video_path, config, save_snapshots=True) -> list[TileRecord]` — the offline counterpart to `CameraWorker._loop()`: runs the identical segment → track → process_tile → snapshot chain against a saved video file (via `VideoFileCapture`) instead of a live device + `SharedState`/Flask, returning every tile's record once the file is exhausted. CLI (`python -m camera.process_video <video_path> [--report file.json] [--no-snapshots]`) prints a per-tile table and the total tile flow count for that clip. Not unit-tested; smoke-tested end-to-end against a synthetic video built from real tile photos (see `development/`) — confirmed correct tile counting, sequence numbering, and snapshot saving.

### `camera/dashboard.py` + `camera/templates/dashboard.html`

- `create_app(state) -> Flask` — `/` (dashboard page), `/video_feed` (MJPEG multipart stream from `SharedState`), `/api/status` (JSON: `tile_count` + `recent_tiles`, each a serialized `TileRecord`). Runs on `host: 0.0.0.0` from config so it's reachable over WiFi. Smoke-tested via Flask's test client, not part of the synthetic-image unit test suite.

### `camera/live_dashboard.py`

CLI entry point. No flags — loads config, starts `CameraWorker`, runs the Flask app. Ctrl+C stops both.

---

## Data Files

Acoustic captures are still shown live and discarded — nothing persisted there yet. Camera
tile photos **are** now persisted (added 2026-08-11): each tile a live dashboard run or
`camera/process_video.py` processes gets its isolated photo saved to
`data/camera_captures/` (config: `capture_snapshots`, see `camera/snapshot.py`), named
`tile_<seq>_<timestamp>.jpg`. `acoustic/config.yaml`, `camera/config.yaml`, and
`pick_place/config.yaml` are git-tracked (configuration, not runtime output); `data/`
itself is not.

`data/` also now holds ~380 real terracotta tile photos used to calibrate
`camera_node`'s vision thresholds (see `development/README.md`,
`development/analyze_dataset.py`) — single tiles on a checkerboard calibration sheet, not
yet including any damaged-tile examples. Excluded from git via `.gitignore` (`data/`,
`*.wav`, and — added 2026-08-07 alongside the camera module — `*.jpg`/`*.jpeg`/`*.png`) —
audio/image datasets should never be committed to git.

---

## Platform Constraints

- Current dev target: Windows 11. `sounddevice`/PortAudio work cross-platform without code changes, but device names/indices in `acoustic/config.yaml`'s `device: null` (system default) are Windows-specific in practice right now — re-verify `--list-devices` output if this ever runs on Linux/Raspberry Pi.
- This machine enumerates several "Microphone Array" input devices (Realtek, 2ch/4ch variants, one reporting a 16000 Hz default sample rate). `device: null` picks whatever Windows considers the default; if `AudioCapture.start()` fails to open at `sample_rate: 44100`, pin an explicit device index from `--list-devices` in `config.yaml` rather than debugging the default.
- **Windows Microphone Enhancements** (AGC / noise suppression) must be manually disabled in Sound Control Panel → input device → Properties → Enhancements tab. This cannot be controlled from Python/PortAudio. Until confirmed disabled, RMS threshold and FFT shape are not trustworthy for calibration — fine for pipeline smoke-testing, not for real threshold tuning.
- No hardware dependencies yet (no GPIO/I2C/serial/solenoid/PLC in this repo). Those land with the acoustic tapping station, dimensional sensors, and control layer per the charter — each will need the dev-machine simulation split required by `CLAUDE-COMMON.md` when it's built.
- `camera/config.yaml`'s `camera.device_index: 0` is OpenCV's default-webcam index — same "re-verify per machine" caveat as the mic's `device: null`. The eventual target is a separate PC-style webcam connected to the UNO Q (per the 2026-08-07 session), not this dev laptop's built-in camera — device index/backend will need re-checking once that's wired up.

---

## Deployment Notes

A lab Arduino UNO Q board (aarch64 Debian, hostname `KLM`, on the owner's laptop hotspot — DHCP lease has been `172.20.10.2` and `.3`; `ping` / check `tools/uno_q/config.bat`) is reachable for dev/bring-up work, ahead of the charter's Raspberry Pi/industrial-PC target (§12, §13). **The App Lab deploy path is verified (2026-09-01):** `arduino-app-cli app restart ~/ArduinoApps/<name> -v` compiles the sketch, flashes the STM32U585 over SWD, and starts the Python side in a Docker container (~90 s); the MCU runs **Zephyr** (Cortex-M33, classic Arduino API via a compat layer); the MCU↔Linux bridge is `Arduino_RouterBridge.h` / `Bridge.provide()` ↔ Python `Bridge.call()`. A teammate's single-motor test sketch already runs on the board as app `user:cnc`. No *tile-line* project code runs there in production yet. **Only this one physical board exists** — the three node folders (`camera_node/`, `acoustic_node/`, `pick_place_node/`) are a code-organization decision made ahead of hardware (`Automation_Architecture.md` §5.7), not confirmation of three physical boards. Which node runs on this board first is still open (`Automation_Architecture.md` §5.2/§5.3, `TODO.md`); pick-and-place bench bring-up is the current use.

- **Connection tooling**: `tools/uno_q/` — batch scripts to push code (`push.bat` → `acoustic_node/`; `push-stepper-test.bat` → `pick_place_node/bench_tests/stepper_smoke_test/` as an `arduino-app-cli` app), open a terminal (`ssh.bat`), and start/stop a VNC desktop (`vnc-start.bat` / `vnc-stop.bat`). See `tools/uno_q/README.md` for copy-paste setup and usage.
- **Auth**: both password (via `tools/uno_q/secrets.bat`, gitignored) and SSH-key (`~/.ssh/id_ed25519` on the dev machine, installed to the board's `authorized_keys`) work. The `.bat` scripts use password auth via `plink`/`pscp` to match this owner's existing tooling convention on other projects (`weather_daq`) and for fully non-interactive scripting.
- **VNC**: TigerVNC on the board binds `-localhost yes` (not exposed on the hotspot directly) — always reached through an SSH tunnel, never opened to the LAN. If `-geometry` ever seems ignored, it's a stale session lock (`tigervncserver -list` shows a `(stale)` PID) — kill and restart, which `vnc-start.bat` already does automatically.
- This is not yet a real dev-vs-target split in the `CLAUDE-COMMON.md` sense (only bench bring-up sketches and a teammate's motor test have run on the board, no tile-line node in production) — fill in a proper table here once a node's assignment is decided and its code actually runs on it.

---

## Known Technical Debt

- `acoustic_node/python/acoustic/config.yaml`'s `trigger.rms_threshold: 0.02` is a placeholder, not a calibrated value. It has not been tuned against this machine's actual noise floor (run `--calibrate` to do that) and is meaningless on any other mic. Do not treat it as a real setting.
- Windows Microphone Enhancements have not been confirmed disabled as of this session (see Platform Constraints). Any RMS/FFT numbers collected before that's done should be treated as pipeline smoke-test data only, not real signal characterization.
- `acoustic_node/python/acoustic/capture.py`'s `AudioCapture` (the real hardware path) has only been smoke-tested via `--list-devices`; `--calibrate` and full trigger-to-plot flow have not yet been run against a live mic in this session — needs a manual run to confirm end-to-end.
- `documents/requirements/requirements.md` was newly formalized (2026-07-10) from `documents/project/project_charter.md` and `Automation_Architecture.md` — it has not yet been reviewed section-by-section against the full charter for completeness, so treat it as a first pass, not an exhaustive spec.
- Tile size and weight range from SMTW's actual product line has never been obtained. Every dimensional figure in the docs so far (e.g. the ~300mm example JSON in `Automation_Architecture.md`) is illustrative, not a spec — don't size the conveyor, gantry, gripper, or ball-drop energy off of it.
- **App Lab deploy path verified 2026-09-01; the nodes' `python/main.py` files are still stubs.** The `arduino-app-cli` build/flash/run flow and the `Arduino_RouterBridge.h` ↔ Python `Bridge.call()` mechanism are confirmed on the lab board (see Deployment Notes) — this resolves the "how do the Python and sketch sides communicate" and "does `App.run()` work" unknowns flagged 2026-08-03. But `acoustic_node/python/main.py`, `camera_node/python/main.py`, and `pick_place_node/python/main.py` still just call `arduino.app_utils.App.run()` copied from `app-bricks-examples` and are **not** wired to their real modules; treat each node's `python/main.py` and (except `acoustic_node/sketch/sketch.ino`) each `sketch/` as an unverified stub. `acoustic_node/sketch/sketch.ino` has real tap-sequencer logic (2026-08-20) but has never been compiled or run. The generated `app.yaml` schema is known (`name` / `icon` / `ports` / `bricks`, mirrored in `pick_place_node/bench_tests/stepper_smoke_test/app.yaml`, checked on the board 2026-09-09), but App Lab's `App.run()` behaviour itself has not been exercised — the verified path used bare `arduino-app-cli` apps whose Python side is a no-op, not a real `python/main.py`.
- **Acoustic tap-trigger architecture is designed and unit-tested but has never touched real hardware** (added 2026-08-20, see `documents/electrical/schematics/acoustic_station_wiring.md` for the full decision): the ToF-triggered dual-solenoid (ARM+LOCK) gravity-drop mechanism, its MCU state machine (`sketch.ino`), and the Python-side hardware-trigger audio capture path (`hardware_trigger.py`, `tap_sequencer.py`, `capture.py`'s `trigger.mode` switch) are all written and covered by synthetic-input unit tests, and the `"simulated"` trigger mode exercises the whole path end-to-end on a dev machine — but no ToF sensor, solenoid, or MOSFET driver has been wired up yet, `sketch.ino`'s `readTofDistanceMm()` is a placeholder, and the App Bricks bridge-variable read in `main.py` (`read_tap_count()`) is unconfirmed. Ball mass/drop height and the reload/reset mechanism between tiles remain undecided.
- The `acoustic_node/`/`camera_node/`/`pick_place_node/` three-folder split assumes three physical UNO Q boards eventually; only one exists today (see Deployment Notes) — don't infer hardware procurement from the repo structure.
- **Pick-and-place: architecture fully designed, almost nothing built.** Decided 2026-08-31/09-01 (`documents/programming/pick_place_control_protocol.md`, `documents/electrical/schematics/pick_place_hardware_connections_plan.md`, `documents/pick_place_todo.md`): custom minimal firmware on the UNO Q MCU (**not** GRBL/Klipper — no STM32U585 port, FR-18 forbids off-the-shelf G-code), Linux owns the coordinate model + sequencer + HMI, line-based ASCII protocol between them. Hardware chosen and in hand: CNC Shield V3.10, 4× TMC2208, 4× 42HM48-1684 NEMA 17, Mean Well LRS-150-12; 3 logical axes (Y beam = 2 motors, A cloned from Y in hardware); vacuum-cup end effector. **What exists as code:** only the digital-twin HMI prototype (`pick_place_node/python/pick_place/`, driven by `MockGantrySimulator` — no real gantry) and `bench_tests/stepper_smoke_test/` (unrun). **Not started:** the firmware (`sketch.ino` is empty), every `pick_place/` module in the protocol doc's plan (`protocol.py`, `coordinate_model.py`, `sequencer.py`, `gantry_backend.py`, `job_runner.py`), and all of `documents/pick_place_todo.md` (nothing checked off — it all needs the physical rig). Gantry mechanical design, travel limits, and stack pitch (blocked on SMTW tile size) are open. Whether an accelerated stepper library (FastAccelStepper/AccelStepper) even builds under Zephyr on the UNO Q is an untested risk.
- `.CLAUDE/CLAUDE.md` previously documented a `venv/` at the repo root; the actual local environment found during this session was `.venv/` (PyCharm default) with only `pip` installed — `requirements.txt` had not been installed into it. Re-verify which venv convention is actually in use before trusting either name blindly.
- **Camera vision thresholds are now data-driven but still unvalidated for true-positive sensitivity** (updated 2026-08-11, was "unvalidated" as of 2026-08-07): `camera_node/python/camera/config.yaml`'s HSV segmentation range, crack-detection Canny thresholds/`border_margin_px`, and corner `min_fill_ratio` were calibrated against 311 real terracotta tile photos (`data/`, see `development/README.md`) — no longer blind guesses. But every one of those photos is a **known-intact** tile, so calibration only established a false-positive floor (loose enough not to flag a healthy tile), not whether a real crack or broken corner actually gets caught — there are no damaged-tile photos yet. `min_tile_area_px` and `border_margin_px` are also absolute pixel values tied to the close-up calibration photos' resolution, not the live 640x480 pipeline's real (undecided) camera distance — re-derive both once that's fixed. `min_crack_length_px`/`min_aspect_ratio` were not part of the calibration and remain the original placeholder guesses. See `camera_node/README.md` Known Limitations and `development/README.md` for the full reasoning. Do not trust any crack/corner detection result as a true-positive guarantee until real damaged-tile photos exist to validate against.
- **`detect_cracks()` had a border-silhouette false-positive bug** (found 2026-08-11 while running the calibration above, fixed same day): it runs Canny on segmentation's tight bounding-box crop, so the tile's own edge against the background sits right at the crop's border — a long, thin, high-contrast line indistinguishable from a real crack by the existing length/aspect-ratio filter. Measured ~98-100% false-positive rate on known-intact tile photos before the fix. Fixed by adding `border_margin_px` (blanks a border band of the Canny edge map before contour-matching — see `crack_detection.py`'s docstring and `development/analyze_dataset.py`), which dropped the false-positive rate to ~2-11% depending on margin. Worth remembering if crack detection is ever reworked: any crop-based edge detector on this kind of tight bounding-box input needs this kind of border exclusion.
- Crack "type" reported by the camera pipeline is severity (minor/major) + measured length, not a defect taxonomy (hairline vs. structural, edge vs. center crack) — that needs real labeled photos to design against.
- **`detect_broken_corner()`'s `fill_ratio` check alone under-caught diagonal corner chips** (found and fixed 2026-08-11, same session as the crack bug above): a triangular chip — the realistic way ceramic actually breaks — removes much less *area* than a square notch reaching the same distance into the tile, so `fill_ratio` badly under-represents how far a diagonal break reaches. Confirmed empirically: a chip whose two legs each reach halfway across the tile's edge only dropped `fill_ratio` to ~0.87, above the production `min_fill_ratio` (0.83) — it would not have been flagged. Fixed by adding `max_missing_extent_fraction`, a second check on how *deep* the gap reaches (distance transform), which does catch it — see `corner_detection.py`'s docstring. Also added real-world missing-area/depth measurements (`missing_area_sq_inches`, `missing_depth_inches`) using the tile's known 9x9in size. **Still unvalidated against a real broken-corner photo** — only one real defect photo has turned up in the whole dataset so far (a crack, in `data/9x9-5(Cam)/DSC_0065.JPG`, correctly caught by `detect_cracks()` — see `development/README.md`), no broken-corner example yet. Localizing *which* corner is broken remains deliberately deferred.
- The camera dashboard's live MJPEG stream is only smoke-tested via Flask's test client and manual review of the code — it has not been run against a real webcam feed end-to-end in this session (no camera hardware available to the assistant). Confirm `python -m camera.live_dashboard` actually opens a working webcam stream on the target machine before relying on it.

---

## Development Rules

1. **Hardware I/O stays separate from pure logic.** Any module touching real hardware (mic, camera, sensors, actuators) must isolate the hardware-facing code into a thin wrapper around pure, synthetic-input-testable logic — see `acoustic/capture.py`'s `TriggerDetector` (pure) vs `AudioCapture` (hardware wrapper), and `camera/segmentation.py`/`crack_detection.py`/`corner_detection.py`/`tile_tracker.py` (pure) vs `camera/capture.py`/`worker.py` (hardware wrapper), as the pattern to repeat for dimensional/control modules.
2. **No hardcoded device/sensor parameters.** Sample rate, device selection, thresholds, timing — all of it goes in a module-local `config.yaml`, never inline in source (extends the `CLAUDE-COMMON.md` hardware-address rule to audio params).
3. **Any threshold calibrated on prototype/substitute hardware (e.g. laptop mic instead of the measurement mic + real tapping mechanism) is provisional** and must be explicitly called out as such in code/docs until recalibrated on the real acoustic station (charter §6.2, §15.2, §18.1).
4. **New non-code artifacts get filed under the matching `documents/` subfolder in the same commit as the work that produced them** — see Documentation Architecture above. Don't leave CAD exports, schematics, or college submissions loose at the repo root or bundled into an unrelated folder.
5. **Every UNO Q station's code lives in its own top-level `<station>_node/` folder, shaped as an Arduino App Bricks project** (`app.yaml` + `sketch/` + `python/` — see `acoustic_node/` as the reference, `Automation_Architecture.md` §5.7). Don't add new hardware-station Python packages back at the repo root the way the old `acoustic/` was — that convention was replaced 2026-08-03.

---

## Project TODO List

Tracked in `TODO.md` (per `CLAUDE-COMMON.md` companion-file rule) rather than duplicated here, to avoid the two files drifting out of sync.

---

## User Rules

> Copy **Standard User Rules** from `CLAUDE-COMMON.md` verbatim here, then copy all sections from `PROJ_STARTER.md` below those. Add project-specific overrides at the bottom, clearly labelled.

See `CLAUDE-COMMON.md` → Standard User Rules and `PROJ_STARTER.md` for the full rule set.

### Project-Specific Overrides

_None — add below as needed._
