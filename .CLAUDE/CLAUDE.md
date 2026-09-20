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
- Entry point (camera module): `python -m camera.live_dashboard`, run from `camera_node/python/` — opens a WiFi-reachable Flask dashboard (added 2026-08-07). Flags: `--list-devices`, `--backend {auto,usb,picamera2}`, `--no-dashboard`. On the Pi station it is started by systemd via `camera_node/python/main.py` instead (see Deployment Notes).
- **Station→hardware map (updated 2026-09-20):** the camera station moved off the Arduino UNO Q to a **Raspberry Pi 5 (16 GB), Raspberry Pi OS Desktop 64-bit**; acoustic and pick-and-place stay on the UNO Q. Reasoning in `documents/project/Automation_Architecture.md` §5.2/§5.3 — in short, only acoustic uses the UNO Q's real-time MCU half (ToF trigger → solenoid ball-drop → capture window) and only camera is compute-bound (per-frame segmentation + a per-tile CNN/ViT classifier).
- `acoustic_node/` and `pick_place_node/` are organized as **Arduino App Bricks** projects (`app.yaml` + `sketch/` + `python/`), matching Arduino's own `app-bricks-examples` convention, so each can be opened in Arduino App Lab. `camera_node/` left that convention on 2026-09-20 (its `app.yaml` + `sketch/` were removed) and is now a plain Python service deployed with systemd — see `camera_node/deploy/`. All three have real Python-side code (`pick_place_node/`'s is a digital-twin HMI prototype + bench bring-up tooling — no gantry firmware yet). `pick_place_node/sketch/` is a stub; `acoustic_node/sketch/sketch.ino` is a real FSM, uncompiled. The App Lab deploy path (`arduino-app-cli`) was verified on the lab board 2026-09-01 — see Deployment Notes. Only one physical UNO Q board exists, plus (as of 2026-09-20) one Raspberry Pi 5.

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

# Which cameras can this machine actually open? Lists USB devices by OpenCV
# index and CSI modules by libcamera camera number. Run this first on any new
# machine (same role as the acoustic module's --list-devices).
python -m camera.live_dashboard --list-devices

# Opens a Flask dashboard at http://0.0.0.0:5000/ — reachable from any
# browser on the same WiFi network, not just localhost.
python -m camera.live_dashboard

# Force a capture backend for one run, overriding config.yaml's camera.backend
# ("auto" picks the CSI module if libcamera reports one, else a USB webcam).
python -m camera.live_dashboard --backend usb

# Vision pipeline with no web dashboard (headless grading only).
python -m camera.live_dashboard --no-dashboard

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

On the **Raspberry Pi 5 camera station** the same module is installed and run as a
systemd service instead — see `camera_node/deploy/README.md`:

```bash
./camera_node/deploy/install_pi.sh      # apt deps + systemd unit (NEVER RUN YET)
sudo systemctl start tile-camera
journalctl -u tile-camera -f
```

There is no hardware/production mode yet — everything above has only run on the dev laptop's built-in mic / webcam. The Pi has not been set up; `camera_node/deploy/` and `camera/capture.py`'s `PiCameraCapture` are written but unrun. No seed data or one-time setup beyond the venv.

---

## Architecture

Acoustic capture, the camera vision pipeline, and a pick-and-place digital-twin HMI prototype exist so far. The pick-and-place gantry control layer is fully *designed* (`documents/programming/pick_place_control_protocol.md` — custom firmware + ASCII line protocol) but no firmware or motion-control code is written. Dimensional, conveyor, decision/grading fusion, sorting execution, and database layers from the full charter (`documents/project/project_charter.md` §5) are not started.

**Repo layout (since 2026-08-03, amended 2026-09-20):** every planned station gets its
own top-level node folder. Stations that run on an **Arduino UNO Q** follow the Arduino
**App Bricks** convention (`app.yaml` + `sketch/` + `python/` — see Arduino's
`app-bricks-examples` repo) so each can be opened as an app in Arduino App Lab. Stations
on other hardware use that platform's normal packaging — as of 2026-09-20 that means
`camera_node/`, which runs on a **Raspberry Pi 5** and is a plain Python service
(`python/` + `deploy/`, no `app.yaml`, no `sketch/`):

| Folder | Status |
|---|---|
| `acoustic_node/` | Real code — `python/acoustic/` is the working, tested module (below), migrated unchanged from the old repo-root `acoustic/`. `sketch/` (laser/ToF trigger + ball-drop release) and `python/main.py`'s App Lab wiring are stubs, unverified on hardware. |
| `camera_node/` | Real code (added 2026-08-07). **Runs on a Raspberry Pi 5 (16 GB), Raspberry Pi OS Desktop 64-bit — not an UNO Q (decided 2026-09-20, `Automation_Architecture.md` §5.2).** `python/camera/` is a working, tested vision pipeline + WiFi dashboard (below), with three capture backends (USB/OpenCV, CSI/Picamera2, video file). `app.yaml` + `sketch/` were **deleted** 2026-09-20 — this node left the App Bricks convention. `python/main.py` is now a real systemd entry point, not an App Lab stub. `deploy/` (systemd unit + `install_pi.sh` + pinned deps) is written but **never run** — no Pi has been set up and `PiCameraCapture` has never seen a CSI module. Gained a configurable capture trigger (`line_trigger.py`) and an **optional, off-by-default** ONNX grade classifier (`grading_model.py` + the git-tracked `models/`) on 2026-09-20. |
| `pick_place_node/` | Partial (2026-08-17 / 09-01) — `python/pick_place/` is a working **digital-twin HMI prototype** (mock gantry simulator + Three.js browser twin + matplotlib desktop twin; `MockGantrySimulator` unit-tested). `bench_tests/stepper_smoke_test/` is a standalone `arduino-app-cli` app to confirm the 4 CNC-shield steppers move (checklist task 0.5). Gantry control **architecture** is fully designed (`documents/programming/pick_place_control_protocol.md` — custom firmware + ASCII line protocol; motor/driver/PSU hardware all chosen) but no real firmware or motion-control Python exists. `sketch/` + `python/main.py` still stubs. |

Conveyor stays on the Arduino Mega (not App-Lab-class, not part of this convention).

`development/` (added 2026-08-11) is a separate, dev-only top-level folder — **not** a
node, not part of the App Bricks convention above. It holds tooling for calibrating
`camera_node`'s vision thresholds against real tile photos (an interactive GUI tuner and
a batch analysis script) before those values get copied into
`camera_node/python/camera/config.yaml`. See `development/README.md`.

`camera_models/` (added 2026-08-20, reorganized into this shared parent folder
2026-08-26 — previously `cam_yolo/`/`cam_vit/` sat loose at the repo root) is another
dev-only top-level folder and **not** an App Bricks node — model training/evaluation
experiments, grouped here so new tile-grade-classification architectures don't keep
littering the repo root. It is still **not imported by** `camera_node` and never may be
(dev-only tooling, gitignored `runs/`); as of 2026-09-20 the link between them is a
deliberate manual copy in one direction only — `cam_edge/stage_model.py` promotes one
chosen `.onnx` plus a metadata sidecar into `camera_node/models/`, and
`camera_node/python/camera/grading_model.py` holds its own duplicated copy of the
preprocessing. Same relationship `Acoustic-Analysis` has with `acoustic_node`.
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
| `camera_models/cam_edge/` | Real code (added 2026-09-20) — **trains nothing**; measures what the other two produced. What each trained model *costs to run* on the Pi 5 station (speed/memory via `benchmark.py`, accuracy on one common val split with Wilson 95% CIs via `evaluate.py`), for the exact `.onnx` that would be deployed, plus `stage_model.py`, which promotes one chosen artifact into `camera_node/models/`. Depends only on onnxruntime/opencv/numpy/Pillow/psutil — deliberately not torch/ultralytics/transformers — so the identical script runs on the Pi. Headline results in `camera_models/cam_edge/README.md`: `yolo26n-cls` 82.9% @ 1.59 ms, `yolo26s-cls` 85.5% @ 4.16 ms, `vit-base` 92.1% @ 110 ms; dynamic INT8 makes the YOLO nets both slower *and* much worse (82.9% → 57.9%). |
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
| `camera_node/python/camera/line_trigger.py` | `LineCrossingDetector` — pure line-crossing trigger: fires once when a tile's bounding-box centre crosses a configured line, in **normalized** (0.0-1.0) coordinates so one config value is valid at any resolution. Hysteresis deadband stops a tile sitting on the line firing repeatedly from segmentation jitter. No I/O — synthetic-sequence testable. |
| `camera_node/python/camera/grading_model.py` | `TileGradeModel` — **optional** ONNX cosmetic grade-tier classifier (3A/3B/4/5) run as a *second opinion*; it cannot see cracks or broken corners. `onnxruntime` is imported lazily and `from_config()` returns None (never raises) when disabled/missing/uninstalled, so a station without it runs unchanged. `preprocess()` is pure and unit-tested; the ONNX session is not. Preprocessing is deliberately **copied, not imported**, from `camera_models/cam_edge/runtime.py`. |
| `camera_node/models/` | The staged, **git-tracked** deployable model + sidecar JSON (classes, preprocessing recipe, provenance, measured accuracy). Written by `camera_models/cam_edge/stage_model.py`; a scoped `.gitignore` exception, since `camera_models/runs/` is ignored and the Pi must get a model by cloning. Currently `tile_grade_yolo26n_cls.onnx` (6.2 MB, 82.9%). |
| `camera_node/python/camera/pipeline.py` | `process_tile()` — wires the three detectors above into one `TileRecord` + a first-pass rule-based grade. `tile_record_to_dict()` — shared JSON serialization, used by both the dashboard and `process_video.py`. |
| `camera_node/python/camera/snapshot.py` | `save_tile_snapshot()` — persists a departed tile's photo to `data/camera_captures/` (config: `capture_snapshots`), named by sequence number. I/O only, not unit-tested. |
| `camera_node/python/camera/capture.py` | Three same-shaped capture backends: `WebcamCapture` (USB/UVC via OpenCV), `PiCameraCapture` (CSI Raspberry Pi camera module via Picamera2 — lazy import, so this file still loads on Windows; **never run on hardware**), `VideoFileCapture` (saved file, for `process_video.py`). `create_capture(config)` picks one from `camera.backend` (`auto`/`usb`/`picamera2`); `list_cameras()` backs `--list-devices`. Real hardware/file I/O, not unit-tested. |
| `camera_node/python/camera/worker.py` | `CameraWorker` + `SharedState` — background thread wiring capture → segmentation → tracker → pipeline (+ snapshot save), thread-safe latest-value store for the dashboard. Tracks the *largest-area* sighting of each tile while it's crossing, not just the last frame before departure. |
| `camera_node/python/camera/process_video.py` | `process_video_file()` + CLI — offline counterpart to `worker.py`'s loop: runs the same pipeline against a saved video file, reports total tile flow count + a numbered per-tile crack/corner result for each tile that crossed. Not unit-tested; smoke-tested against a synthetic video (see `development/`). |
| `camera_node/python/camera/dashboard.py` + `templates/dashboard.html` | Flask app: `/video_feed` (MJPEG stream), `/api/status` (JSON), `/` (dashboard page). Serves on `0.0.0.0` so it's reachable over WiFi. |
| `camera_node/python/camera/live_dashboard.py` | CLI entry point, mirrors `acoustic_node`'s `live_monitor.py`. Flags: `--list-devices`, `--backend`, `--no-dashboard`. |
| `camera_node/python/main.py` | Raspberry Pi systemd service entry point — a thin `sys.path` shim over `live_dashboard.main()` so the service and the CLI can't drift apart. **Not** an App Lab stub any more (2026-09-20). |
| `camera_node/deploy/` | Pi deployment: `tile-camera.service` (systemd unit template), `install_pi.sh` (apt deps + unit install), `requirements-pi.txt` (USB-only venv fallback), `README.md`. Written 2026-09-20, **never run**. |
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
LineCrossingDetector.update()  — (processing_trigger: "line_crossing", the
   |                              default) fires on the frame the tile's centre
   |                              crosses the configured line
   v
process_tile()  — once per tile: detect_cracks() + detect_broken_corner() +
   |               grade_tile(), and — if grading_model.enabled — a second
   |               opinion from TileGradeModel.predict(), recorded alongside
   |               the rule-based grade, never replacing it
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
crack/corner pipeline only runs **once per tile**. Which frame that is depends on
`config.yaml`'s `processing_trigger` (added 2026-09-20):

- `"line_crossing"` (default) — the frame the tile's bounding-box centre crosses
  `trigger_line`. Right for a conveyor: every tile is captured at the same point in the
  frame, so scale, framing and motion blur are comparable between tiles.
- `"departure"` — the original behaviour: fire once `TileTracker` confirms the tile has
  fully left, grading the *largest-area* region seen while it was present (not the last
  frame before departure, which is often partially exited/motion-blurred at the edge).
  Kept because it needs no line to be positioned, which is useful on footage from a
  camera whose mounting isn't fixed yet.

The two grade **different frames**, so the same footage gives different measurements
under each — expected, not a bug. Measured on a synthetic clip (2026-09-20): capture
centres were x=340 for all four tiles under `"line_crossing"` versus 120/520/520/180
under `"departure"` — which is the inconsistency the line trigger exists to remove. The
tile *count* follows the trigger (crossings vs. departures); `TileTracker` runs in both
modes since it owns the presence debounce regardless.

`camera/process_video.py` runs this same segment → track/trigger → process_tile →
snapshot chain synchronously against a saved video file instead of a live device + Flask
dashboard, honouring the same `processing_trigger`, so offline results on a clip match
what the live station would have produced — see its section in Key Modules below.

### Threading model (camera)

Unlike `sounddevice`'s callback-driven model, OpenCV's `VideoCapture` is read
synchronously, so `CameraWorker` spins its own background thread (`threading.Thread`,
daemon) that owns the whole per-frame pipeline. `SharedState` is the only thing shared
across threads (the worker thread writes, Flask's request-handling threads read), guarded
by a single `threading.Lock` — reads/writes are "latest value wins," not a queue, since
the dashboard only ever wants the most recent frame/status, not a backlog.

### Simulation vs real mode

Not applicable yet — the acoustic module runs on the dev laptop's built-in mic, the camera module on a dev laptop webcam, and `pick_place_node/`'s twin on a `MockGantrySimulator`; no embedded/hardware target is involved for any of them. `TriggerDetector`, the `camera/` pure modules, and `MockGantrySimulator` are hardware-independent by construction (see Development Rules) — the same pure-logic / thin-wrapper split repeats as `acoustic_node/sketch/` (laser/ToF + ball-drop) and `pick_place_node/`'s real firmware + `gantry_backend.py` are built.

For the camera node the split is now backend-shaped rather than MCU-shaped: the dev laptop and the Pi station run the *same* `camera/` pure modules, and only `capture.py`'s backend differs (`WebcamCapture` vs. `PiCameraCapture`), chosen by `camera.backend` in config. `camera_node/sketch/` no longer exists — the Pi has no MCU half, so the tile-presence-trigger question (charter §6.1) is now a question about a sensor on the conveyor's Mega or a GPIO on the Pi, not about a sketch in this folder.

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

### `camera/line_trigger.py`

- `LineCrossingDetector(position=0.5, orientation="vertical", direction="both", hysteresis=0.02)` — pure trigger deciding **which frame of a tile gets graded**. `.update(center_normalized: tuple | None) -> CrossingEvent` returns a truthy event on exactly the frame the centre crosses the line, falsy otherwise (`CrossingEvent.__bool__` makes `if detector.update(c):` read naturally while still carrying the direction). `.crossing_count` / `.armed` / `.reset()`. Works in **normalized** 0.0-1.0 coordinates so one configured position is valid at any camera resolution — `TileRegion.center_normalized` does the conversion. Passing `None` (no tile this frame) resets the remembered side, so a new tile appearing on the far side of the line isn't mistaken for a continuation of the previous one. Two deliberate refusals to fire: the *first* decisive sighting of a tile is never a crossing (no previous side to compare, and a tile that entered the frame already past the line never crossed it in view), and a crossing the wrong way for `direction` updates the side without counting. Why a hysteresis deadband rather than a bare sign change: a tile sitting on the line jitters a pixel either side between frames from segmentation noise, and a naive `side != previous_side` test fires repeatedly for one tile. No I/O; covered by `tests/test_camera_line_trigger.py` with synthetic centre sequences.
- `TileRegion.center` is the **bounding-box** centre, not the contour centroid, on purpose: a broken corner pulls the area centroid away from the tile's geometric middle, which would make a chipped tile trip the line at a different moment than an intact one — exactly the inconsistency the trigger exists to avoid.

### `camera/grading_model.py`

- `TileGradeModel.from_config(config) -> TileGradeModel | None` — builds from `config.yaml`'s `grading_model` block, returning `None` (never raising) for every "not set up" case: disabled, model file missing, `onnxruntime` not installed, no class list. Each prints why. A model that *is* present but contradicts its metadata (output class count ≠ configured names) **does** raise — that's a misconfiguration worth stopping for. Classes/preprocess/input size come from the sidecar `models/*.json` so they can't drift from the weights.
- `.predict(tile_bgr) -> GradePrediction` — label, confidence, full probability dict, and `inference_ms`.
- `preprocess(image_bgr, mode, size=224) -> np.ndarray` — pure: BGR uint8 → float32 NCHW batch of 1. `"yolo_cls"` = shortest-side resize + centre crop + /255 (Ultralytics' Normalize is mean=0/std=1, a no-op); `"vit_hf"` = straight resize + /255 + `(x-0.5)/0.5`. Covered by `tests/test_camera_grading_model.py`, which deliberately **does not require onnxruntime** — the contract being tested is that a station without it still runs.
- **Two traps encoded here, both measured, both silent if broken.** (1) Pillow's antialiased BILINEAR, not `cv2.INTER_LINEAR`: on the 76-image val split that difference alone cost 8 accuracy points (82.9% → 75.0%) with nothing raised anywhere. (2) `int()` truncation, not `round()`, in the resize arithmetic — copied from torchvision's `_compute_resized_output_size`, because a one-pixel crop shift changes predictions.
- **It is a second opinion, not a replacement.** It classifies cosmetic grade tier (3A/3B/4/5) on an intact tile and **cannot see cracks or broken corners at all**. `pipeline.py` records it as `model_grade` *alongside* the rule-based `grade`, which remains the station's decision, so the two can be compared on real tiles instead of one silently overriding the other.
- Preprocessing is **copied, not imported**, from `camera_models/cam_edge/runtime.py` — `camera_node` must not import from `camera_models/` (dev-only tooling, gitignored `runs/`), the same one-directional relationship `Acoustic-Analysis` has with `acoustic_node`. Verified byte-identical on 2026-09-20 (max abs tensor diff 0.00000000 across all 76 val images, 100% label agreement, 82.89% reproduced). If you change one, change both and re-run `camera_models/cam_edge/evaluate.py`.

### `camera/pipeline.py`

- `process_tile(seq, region, ...) -> TileRecord` — runs `detect_cracks()` + `detect_broken_corner()` on one `TileRegion` and grades it (`grade_tile()`: any broken corner or major crack → Reject, minor crack → Grade B, else Grade A — a first-pass rule, not the master's fused final grade). `TileRecord.seq` is the tile's sequence number in this run's flow (1st, 2nd, ... tile to cross), and doubles as its identifier — carried alongside that tile's crack/corner results in the same record. `TileRecord.snapshot_path` is set by callers after `camera/snapshot.py` persists the tile's photo (`None` if snapshot saving is off). `TileRecord.center`/`.trigger` record where the tile was and which rule fired the record, so a report can be read without knowing what `config.yaml` said at the time. The optional `grading_model` argument is typed loosely as "anything with `.predict(tile_bgr)`" on purpose — importing the real one would drag `onnxruntime` into every import of the pipeline. A model raising is caught and recorded as *no prediction* rather than propagated: a classifier that can't load an image must not cost the station the crack/corner measurement it already computed, which is what it actually sorts on. `grade_tile()`, `tile_record_to_dict()`, and the model integration (including the failure path) are unit-tested (`tests/test_camera_pipeline.py`).
- `tile_record_to_dict(record) -> dict` — the JSON/API-friendly view of a `TileRecord` (everything except the raw pixel array). Shared by `dashboard.py`'s `/api/status` and `process_video.py`'s `--report`, so both report a tile the same way.

### `camera/snapshot.py`

- `save_tile_snapshot(record, output_dir) -> Path` — writes `record.tile_bgr` to `<output_dir>/tile_<seq>_<timestamp>.jpg`. I/O only, not unit-tested (mirrors `capture.py`'s hardware-wrapper modules). `resolve_output_dir(config)` anchors `capture_snapshots.output_dir` (config-relative, e.g. `"data/camera_captures"`) to the repo root regardless of current working directory.

### `camera/capture.py` / `camera/worker.py`

- `create_capture(config)` — factory returning the backend named by `config.yaml`'s `camera.backend`: `"usb"` → `WebcamCapture`, `"picamera2"` → `PiCameraCapture`, `"auto"` (default) → the CSI module if libcamera reports one, else USB. This is what lets one config file run unchanged on the Windows dev laptop and on the Pi station.
- `list_cameras(max_usb_index=8)` — enumerates openable USB indices (by probing) and CSI modules (by asking libcamera). Backs `live_dashboard.py --list-devices`.
- `WebcamCapture(config)` — thin `cv2.VideoCapture` wrapper around a live USB/UVC device (`start()`/`read_frame()`/`stop()`, context-manager support). `camera.api_preference` picks the OpenCV backend — `"any"` on Windows, pin `"v4l2"` on the Pi, where OpenCV can otherwise choose a GStreamer path that silently ignores the requested resolution/FPS. Real hardware I/O, not unit-tested.
- `PiCameraCapture(config)` — same contract, for a CSI Raspberry Pi camera module via Picamera2; returns BGR like the others so nothing downstream changes. `picamera2` is imported lazily inside `start()`/`is_available()`, so this module still imports fine on Windows. Two things are config-driven rather than hardcoded because both fail *silently*: `picamera2.format`/`swap_rb` (libcamera names formats by packing order, so `"RGB888"` already yields B,G,R — a wrong channel order would wreck the calibrated HSV range rather than raise) and `picamera2.controls` (auto-exposure/AWB left on means the sensor re-exposes per tile and the HSV range stops meaning anything). **Never run on hardware** — written 2026-09-20 with no CSI module attached.
- `VideoFileCapture(video_path)` — same shape as `WebcamCapture` but opens a saved video file instead of a live device; `read_frame()` raises `StopIteration` at end-of-file rather than blocking (a file has a defined end). Used by `process_video.py`, not by the live dashboard.
- `CameraWorker(capture, config, state)` — background thread: reads a frame, segments it, feeds `TileTracker`, and on tile departure runs the pipeline against the *largest-area* region seen while that tile was present (not just the last frame before departure — typically the most centered/least-blurred view), saves a snapshot if `capture_snapshots.enabled`, and publishes an annotated JPEG + results into `SharedState`. `SharedState` is the thread-safe latest-value store the Flask app reads from. Real hardware I/O + threading, not unit-tested (mirrors `AudioCapture`).

### `camera/process_video.py`

- `process_video_file(video_path, config, save_snapshots=True) -> list[TileRecord]` — the offline counterpart to `CameraWorker._loop()`: runs the identical segment → track → process_tile → snapshot chain against a saved video file (via `VideoFileCapture`) instead of a live device + `SharedState`/Flask, returning every tile's record once the file is exhausted. CLI (`python -m camera.process_video <video_path> [--report file.json] [--no-snapshots]`) prints a per-tile table and the total tile flow count for that clip. Not unit-tested; smoke-tested end-to-end against a synthetic video built from real tile photos (see `development/`) — confirmed correct tile counting, sequence numbering, and snapshot saving.

### `camera/dashboard.py` + `camera/templates/dashboard.html`

- `create_app(state) -> Flask` — `/` (dashboard page), `/video_feed` (MJPEG multipart stream from `SharedState`), `/api/status` (JSON: `tile_count` + `recent_tiles`, each a serialized `TileRecord`). Runs on `host: 0.0.0.0` from config so it's reachable over WiFi. Smoke-tested via Flask's test client, not part of the synthetic-image unit test suite.

### `camera/live_dashboard.py`

CLI entry point. No flags: load config, start `CameraWorker`, run the Flask app; Ctrl+C stops both. `--list-devices` prints every camera this machine can open and exits (same role as the acoustic module's flag of the same name). `--backend {auto,usb,picamera2}` overrides `camera.backend` for one run. `--no-dashboard` runs the vision pipeline headless (no Flask), blocking on `CameraWorker.join()` instead. `main(argv=None)` is also what `camera_node/python/main.py` — the Pi's systemd entry point — calls, so the service and the CLI are the same code path.

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
- `camera/config.yaml`'s `camera.device_index: 0` is OpenCV's default-webcam index — same "re-verify per machine" caveat as the mic's `device: null`. Use `python -m camera.live_dashboard --list-devices` to check.
- **The camera node's real target is a Raspberry Pi 5 running Raspberry Pi OS Desktop 64-bit** (decided 2026-09-20, supersedes the 2026-08-07 "webcam connected to the UNO Q" plan). Consequences not yet exercised: `camera.api_preference` should be `"v4l2"` there, not `"any"`; a CSI camera module needs `camera.backend: "picamera2"` and the apt-installed `python3-picamera2` (a pip venv next to it is the usual route to a numpy/libcamera ABI mismatch — see `camera_node/deploy/README.md`); auto-exposure/AWB must be locked via `camera.picamera2.controls` once the rig lighting is fixed or the calibrated HSV range drifts; and a Pi 5 running continuous CV wants an active cooler (`vcgencmd get_throttled`).
- **On this dev machine, `python -m pytest tests/` can die silently — no output, exit code 1 — unless OpenBLAS is pinned to one thread** (observed 2026-09-20, after an IDE/machine crash). The failure mode is a hard abort during collection, not a test failure, and it prints nothing at all, so it reads like pytest is broken. `numpy`, `cv2` and `pytest` all import fine individually. Workaround that works: `OPENBLAS_NUM_THREADS=1 python -m pytest tests/ -q`. Root cause not chased down — the underlying error, seen once on a bare `import onnxruntime`, was `OpenBLAS error: Memory allocation still failed after 10 retries`, i.e. thread/memory pressure rather than anything in this repo. If the suite ever "does nothing," try the env var before debugging the tests.
- Shell scripts and systemd units under `camera_node/deploy/` must stay LF-terminated. This repo has `core.autocrlf=true`, so `.gitattributes` (added 2026-09-20) pins `*.sh`/`*.service` to `eol=lf` — without it, a clone on the Pi gets CRLF and `install_pi.sh` fails with a misleading "bad interpreter" error.

---

## Deployment Notes

A lab Arduino UNO Q board (aarch64 Debian, hostname `KLM`, on the owner's laptop hotspot — DHCP lease has been `172.20.10.2` and `.3`; `ping` / check `tools/uno_q/config.bat`) is reachable for dev/bring-up work, ahead of the charter's Raspberry Pi/industrial-PC target (§12, §13). **The App Lab deploy path is verified (2026-09-01):** `arduino-app-cli app restart ~/ArduinoApps/<name> -v` compiles the sketch, flashes the STM32U585 over SWD, and starts the Python side in a Docker container (~90 s); the MCU runs **Zephyr** (Cortex-M33, classic Arduino API via a compat layer); the MCU↔Linux bridge is `Arduino_RouterBridge.h` / `Bridge.provide()` ↔ Python `Bridge.call()`. A teammate's single-motor test sketch already runs on the board as app `user:cnc`. No *tile-line* project code runs there in production yet. **Only this one physical board exists** — the three node folders (`camera_node/`, `acoustic_node/`, `pick_place_node/`) are a code-organization decision made ahead of hardware (`Automation_Architecture.md` §5.7), not confirmation of three physical boards. Which node runs on this board first is still open (`Automation_Architecture.md` §5.2/§5.3, `TODO.md`); pick-and-place bench bring-up is the current use.

- **Connection tooling**: `tools/uno_q/` — batch scripts to push code (`push.bat` → `acoustic_node/`; `push-stepper-test.bat` → `pick_place_node/bench_tests/stepper_smoke_test/` as an `arduino-app-cli` app), open a terminal (`ssh.bat`), and start/stop a VNC desktop (`vnc-start.bat` / `vnc-stop.bat`). See `tools/uno_q/README.md` for copy-paste setup and usage.
- **Auth**: both password (via `tools/uno_q/secrets.bat`, gitignored) and SSH-key (`~/.ssh/id_ed25519` on the dev machine, installed to the board's `authorized_keys`) work. The `.bat` scripts use password auth via `plink`/`pscp` to match this owner's existing tooling convention on other projects (`weather_daq`) and for fully non-interactive scripting.
- **VNC**: TigerVNC on the board binds `-localhost yes` (not exposed on the hotspot directly) — always reached through an SSH tunnel, never opened to the LAN. If `-geometry` ever seems ignored, it's a stale session lock (`tigervncserver -list` shows a `(stale)` PID) — kill and restart, which `vnc-start.bat` already does automatically.
- This is not yet a real dev-vs-target split in the `CLAUDE-COMMON.md` sense (only bench bring-up sketches and a teammate's motor test have run on the board, no tile-line node in production) — fill in a proper table here once a node's assignment is decided and its code actually runs on it.

**Raspberry Pi 5 (camera station) — decided 2026-09-20, not yet set up.** The camera node
moved off the UNO Q to a **Raspberry Pi 5, 16 GB RAM, Raspberry Pi OS Desktop (64-bit)**
(`Automation_Architecture.md` §5.2 has the decision and the comparison; §5.3 records that
acoustic stays on the UNO Q). Deployment is systemd, not App Lab:

| | UNO Q nodes (`acoustic_node/`, `pick_place_node/`) | Pi node (`camera_node/`) |
|---|---|---|
| Deploy | `arduino-app-cli app restart ~/ArduinoApps/<name>` via `tools/uno_q/*.bat` | `camera_node/deploy/install_pi.sh` → systemd unit `tile-camera` |
| Run | App Lab Docker container + Zephyr sketch on the MCU | `/usr/bin/python3 camera_node/python/main.py` |
| Deps | App Lab image | apt (`python3-picamera2`/`-opencv`/`-flask`/`-yaml`/`-numpy`) — deliberately **not** a venv, see `camera_node/deploy/README.md` |
| Logs | `arduino-app-cli` output | `journalctl -u tile-camera -f` |

**None of the Pi path has been executed.** No Pi has been imaged, no camera attached,
`install_pi.sh` has never run, and `PiCameraCapture` has never opened a CSI module.
Everything in `camera_node/deploy/` was written 2026-09-20 from documentation. Because it
runs the *Desktop* image, the Pi can also display its own dashboard locally at
`http://localhost:5000/` (Chromium kiosk snippet in `deploy/README.md`) — that browser is
one more consumer of the MJPEG stream, so watch the frame rate.

---

## Known Technical Debt

- `acoustic_node/python/acoustic/config.yaml`'s `trigger.rms_threshold: 0.02` is a placeholder, not a calibrated value. It has not been tuned against this machine's actual noise floor (run `--calibrate` to do that) and is meaningless on any other mic. Do not treat it as a real setting.
- Windows Microphone Enhancements have not been confirmed disabled as of this session (see Platform Constraints). Any RMS/FFT numbers collected before that's done should be treated as pipeline smoke-test data only, not real signal characterization.
- `acoustic_node/python/acoustic/capture.py`'s `AudioCapture` (the real hardware path) has only been smoke-tested via `--list-devices`; `--calibrate` and full trigger-to-plot flow have not yet been run against a live mic in this session — needs a manual run to confirm end-to-end.
- `documents/requirements/requirements.md` was newly formalized (2026-07-10) from `documents/project/project_charter.md` and `Automation_Architecture.md` — it has not yet been reviewed section-by-section against the full charter for completeness, so treat it as a first pass, not an exhaustive spec.
- Tile size and weight range from SMTW's actual product line has never been obtained. Every dimensional figure in the docs so far (e.g. the ~300mm example JSON in `Automation_Architecture.md`) is illustrative, not a spec — don't size the conveyor, gantry, gripper, or ball-drop energy off of it.
- **The Raspberry Pi 5 camera station exists on paper only** (added 2026-09-20). The decision is made and the code is restructured for it — `camera_node/` left the App Bricks convention (`app.yaml` + `sketch/` deleted), `python/main.py` is a real systemd entry point, `capture.py` gained `PiCameraCapture` + a `camera.backend` switch, and `deploy/` has a systemd unit and install script. **None of it has run.** No Pi has been imaged; `PiCameraCapture` has never opened a camera; `install_pi.sh`'s apt package names, the unit file, and the claim that Picamera2's `"RGB888"` already yields BGR are all from documentation, not observation. The USB path (`WebcamCapture`, `create_capture`, `--list-devices`, `main.py`) *was* exercised on the Windows dev laptop. Treat the CSI/Pi half as unverified until it runs on the bench, and expect `camera.api_preference: "v4l2"`, exposure/AWB locking, and frame-rate-under-load to all need attention there.
- **Camera thresholds will likely need re-deriving on the Pi rig anyway.** `segmentation.min_tile_area_px` and `crack_detection.border_margin_px` are absolute pixel counts calibrated at a different resolution and working distance; a new camera, new mounting height, and new enclosure lighting invalidate them again. Re-run `development/analyze_dataset.py` once the Pi's camera position is fixed.
- **App Lab deploy path verified 2026-09-01; the UNO Q nodes' `python/main.py` files are still stubs.** The `arduino-app-cli` build/flash/run flow and the `Arduino_RouterBridge.h` ↔ Python `Bridge.call()` mechanism are confirmed on the lab board (see Deployment Notes) — this resolves the "how do the Python and sketch sides communicate" and "does `App.run()` work" unknowns flagged 2026-08-03. But `acoustic_node/python/main.py` and `pick_place_node/python/main.py` still just call `arduino.app_utils.App.run()` copied from `app-bricks-examples` and are **not** wired to their real modules; treat each of those nodes' `python/main.py` and (except `acoustic_node/sketch/sketch.ino`) each `sketch/` as an unverified stub. (`camera_node/python/main.py` is no longer in this category — it's a real systemd entry point on the Pi as of 2026-09-20, and there is no `camera_node/sketch/` any more.) `acoustic_node/sketch/sketch.ino` has real tap-sequencer logic (2026-08-20) but has never been compiled or run. The generated `app.yaml` schema is known (`name` / `icon` / `ports` / `bricks`, mirrored in `pick_place_node/bench_tests/stepper_smoke_test/app.yaml`, checked on the board 2026-09-09), but App Lab's `App.run()` behaviour itself has not been exercised — the verified path used bare `arduino-app-cli` apps whose Python side is a no-op, not a real `python/main.py`.
- **Acoustic tap-trigger architecture is designed and unit-tested but has never touched real hardware** (added 2026-08-20, see `documents/electrical/schematics/acoustic_station_wiring.md` for the full decision): the ToF-triggered dual-solenoid (ARM+LOCK) gravity-drop mechanism, its MCU state machine (`sketch.ino`), and the Python-side hardware-trigger audio capture path (`hardware_trigger.py`, `tap_sequencer.py`, `capture.py`'s `trigger.mode` switch) are all written and covered by synthetic-input unit tests, and the `"simulated"` trigger mode exercises the whole path end-to-end on a dev machine — but no ToF sensor, solenoid, or MOSFET driver has been wired up yet, `sketch.ino`'s `readTofDistanceMm()` is a placeholder, and the App Bricks bridge-variable read in `main.py` (`read_tap_count()`) is unconfirmed. Ball mass/drop height and the reload/reset mechanism between tiles remain undecided.
- The node-folder split still outruns the hardware. As of 2026-09-20 there is **one UNO Q** and **one Raspberry Pi 5**, for three stations (`acoustic_node/`, `camera_node/`, `pick_place_node/`) — and the Pi hasn't been set up. Note that the Pi decision does *not* relieve the UNO Q shortage: acoustic (tap sequencing) and pick-and-place (step-pulse generation) both genuinely need an MCU and both want that one board; it only stops the board being spent on the node that least needed it. Pick-and-place bench bring-up currently has it. Don't infer hardware procurement from the repo structure.
- **Pick-and-place: architecture fully designed, almost nothing built.** Decided 2026-08-31/09-01 (`documents/programming/pick_place_control_protocol.md`, `documents/electrical/schematics/pick_place_hardware_connections_plan.md`, `documents/pick_place_todo.md`): custom minimal firmware on the UNO Q MCU (**not** GRBL/Klipper — no STM32U585 port, FR-18 forbids off-the-shelf G-code), Linux owns the coordinate model + sequencer + HMI, line-based ASCII protocol between them. Hardware chosen and in hand: CNC Shield V3.10, 4× TMC2208, 4× 42HM48-1684 NEMA 17, Mean Well LRS-150-12; 3 logical axes (Y beam = 2 motors, A cloned from Y in hardware); vacuum-cup end effector. **What exists as code:** only the digital-twin HMI prototype (`pick_place_node/python/pick_place/`, driven by `MockGantrySimulator` — no real gantry) and `bench_tests/stepper_smoke_test/` (unrun). **Not started:** the firmware (`sketch.ino` is empty), every `pick_place/` module in the protocol doc's plan (`protocol.py`, `coordinate_model.py`, `sequencer.py`, `gantry_backend.py`, `job_runner.py`), and all of `documents/pick_place_todo.md` (nothing checked off — it all needs the physical rig). Gantry mechanical design, travel limits, and stack pitch (blocked on SMTW tile size) are open. Whether an accelerated stepper library (FastAccelStepper/AccelStepper) even builds under Zephyr on the UNO Q is an untested risk.
- `.CLAUDE/CLAUDE.md` previously documented a `venv/` at the repo root; the actual local environment found during this session was `.venv/` (PyCharm default) with only `pip` installed — `requirements.txt` had not been installed into it. Re-verify which venv convention is actually in use before trusting either name blindly.
- **Camera vision thresholds are now data-driven but still unvalidated for true-positive sensitivity** (updated 2026-08-11, was "unvalidated" as of 2026-08-07): `camera_node/python/camera/config.yaml`'s HSV segmentation range, crack-detection Canny thresholds/`border_margin_px`, and corner `min_fill_ratio` were calibrated against 311 real terracotta tile photos (`data/`, see `development/README.md`) — no longer blind guesses. But every one of those photos is a **known-intact** tile, so calibration only established a false-positive floor (loose enough not to flag a healthy tile), not whether a real crack or broken corner actually gets caught — there are no damaged-tile photos yet. `min_tile_area_px` and `border_margin_px` are also absolute pixel values tied to the close-up calibration photos' resolution, not the live 640x480 pipeline's real (undecided) camera distance — re-derive both once that's fixed. `min_crack_length_px`/`min_aspect_ratio` were not part of the calibration and remain the original placeholder guesses. See `camera_node/README.md` Known Limitations and `development/README.md` for the full reasoning. Do not trust any crack/corner detection result as a true-positive guarantee until real damaged-tile photos exist to validate against.
- **`detect_cracks()` had a border-silhouette false-positive bug** (found 2026-08-11 while running the calibration above, fixed same day): it runs Canny on segmentation's tight bounding-box crop, so the tile's own edge against the background sits right at the crop's border — a long, thin, high-contrast line indistinguishable from a real crack by the existing length/aspect-ratio filter. Measured ~98-100% false-positive rate on known-intact tile photos before the fix. Fixed by adding `border_margin_px` (blanks a border band of the Canny edge map before contour-matching — see `crack_detection.py`'s docstring and `development/analyze_dataset.py`), which dropped the false-positive rate to ~2-11% depending on margin. Worth remembering if crack detection is ever reworked: any crop-based edge detector on this kind of tight bounding-box input needs this kind of border exclusion.
- **The staged ONNX grade model is accurate-as-measured but measured on a narrow basis, and validated only on intact tiles** (added 2026-09-20). `camera_node/models/tile_grade_yolo26n_cls.onnx` reproduces 82.89% through `camera_node`'s own path (re-verified end-to-end this session: 100% label agreement with `cam_edge`'s runtime, 0.00000000 preprocessing tensor diff) — but that is **76 val images, 95% CI 72.9-89.7**, i.e. roughly one tile in six misgraded, and per-class recall is uneven (grade "4" only 0.667). The training set is 376 photos of **intact** tiles from a camera that is not the station's camera, at a working distance that is not the station's. It classifies cosmetic grade tier only and **cannot see cracks or broken corners**, which is why `config.yaml` ships it `enabled: false` and `pipeline.py` records it as `model_grade` beside — never instead of — the rule-based grade. Do not let it drive a sorting decision, and expect to retrain once the Pi's camera and lighting are fixed.
- **The line-crossing trigger has never run against real conveyor footage or a live camera** (added 2026-09-20). `LineCrossingDetector` is unit-tested on synthetic sequences and smoke-tested end-to-end on a *synthetic* clip built from still photos slid across a frame. Its two tuning values are properties of a rig that doesn't exist yet: `trigger_line.position` depends on where the camera is mounted, and `direction` on which way the conveyor runs. `hysteresis: 0.02` was reasoned about (≈13 px at 640 px wide), not measured against real segmentation jitter. Also unexercised: what happens when two tiles are in frame at once — `segment_tile()` returns only the largest region, so the trigger currently assumes one tile at a time.
- **`onnxruntime` is not available through apt on Raspberry Pi OS**, so `install_pi.sh --with-grading` pip-installs it into the system interpreter with `--break-system-packages` (added 2026-09-20, **never run**). That is a deliberate, narrow choice — one pure-addition wheel into the interpreter the service already uses, rather than a venv that would break `python3-picamera2`'s numpy/libcamera ABI — but it is unverified, and the wheel may simply not exist for the Pi's Python/arch. The step is non-fatal by design: grading stays off and the station still inspects tiles.
- Crack "type" reported by the camera pipeline is severity (minor/major) + measured length, not a defect taxonomy (hairline vs. structural, edge vs. center crack) — that needs real labeled photos to design against.
- **`detect_broken_corner()`'s `fill_ratio` check alone under-caught diagonal corner chips** (found and fixed 2026-08-11, same session as the crack bug above): a triangular chip — the realistic way ceramic actually breaks — removes much less *area* than a square notch reaching the same distance into the tile, so `fill_ratio` badly under-represents how far a diagonal break reaches. Confirmed empirically: a chip whose two legs each reach halfway across the tile's edge only dropped `fill_ratio` to ~0.87, above the production `min_fill_ratio` (0.83) — it would not have been flagged. Fixed by adding `max_missing_extent_fraction`, a second check on how *deep* the gap reaches (distance transform), which does catch it — see `corner_detection.py`'s docstring. Also added real-world missing-area/depth measurements (`missing_area_sq_inches`, `missing_depth_inches`) using the tile's known 9x9in size. **Still unvalidated against a real broken-corner photo** — only one real defect photo has turned up in the whole dataset so far (a crack, in `data/9x9-5(Cam)/DSC_0065.JPG`, correctly caught by `detect_cracks()` — see `development/README.md`), no broken-corner example yet. Localizing *which* corner is broken remains deliberately deferred.
- The camera dashboard's live MJPEG stream is only smoke-tested via Flask's test client and manual review of the code — it has never been watched running against a real camera feed end-to-end (no camera hardware available to the assistant). `--list-devices` does find the dev laptop's webcam, so the device opens; the streaming path itself is still unconfirmed. Confirm `python -m camera.live_dashboard` actually shows a live stream before relying on it, and re-confirm on the Pi, where a local kiosk browser is a second client on the same stream.

---

## Development Rules

1. **Hardware I/O stays separate from pure logic.** Any module touching real hardware (mic, camera, sensors, actuators) must isolate the hardware-facing code into a thin wrapper around pure, synthetic-input-testable logic — see `acoustic/capture.py`'s `TriggerDetector` (pure) vs `AudioCapture` (hardware wrapper), and `camera/segmentation.py`/`crack_detection.py`/`corner_detection.py`/`tile_tracker.py` (pure) vs `camera/capture.py`/`worker.py` (hardware wrapper), as the pattern to repeat for dimensional/control modules.
2. **No hardcoded device/sensor parameters.** Sample rate, device selection, thresholds, timing — all of it goes in a module-local `config.yaml`, never inline in source (extends the `CLAUDE-COMMON.md` hardware-address rule to audio params).
3. **Any threshold calibrated on prototype/substitute hardware (e.g. laptop mic instead of the measurement mic + real tapping mechanism) is provisional** and must be explicitly called out as such in code/docs until recalibrated on the real acoustic station (charter §6.2, §15.2, §18.1).
4. **New non-code artifacts get filed under the matching `documents/` subfolder in the same commit as the work that produced them** — see Documentation Architecture above. Don't leave CAD exports, schematics, or college submissions loose at the repo root or bundled into an unrelated folder.
5. **Every station's code lives in its own top-level `<station>_node/` folder.** Don't add new hardware-station Python packages back at the repo root the way the old `acoustic/` was — that convention was replaced 2026-08-03. *How* a node folder is shaped depends on what it runs on (amended 2026-09-20, `Automation_Architecture.md` §5.7):
   - **On an Arduino UNO Q** — shape it as an Arduino App Bricks project (`app.yaml` + `sketch/` + `python/`), with `acoustic_node/` as the reference. Applies to `acoustic_node/` and `pick_place_node/`.
   - **On anything else** — use that platform's normal packaging, and don't carry App Bricks scaffolding that describes hardware the node doesn't have. `camera_node/` (Raspberry Pi 5) is the reference: `python/` + a `deploy/` folder with a systemd unit and an install script; its `app.yaml` and `sketch/` were deleted rather than left as dead stubs.

---

## Project TODO List

Tracked in `TODO.md` (per `CLAUDE-COMMON.md` companion-file rule) rather than duplicated here, to avoid the two files drifting out of sync.

---

## User Rules

> Copy **Standard User Rules** from `CLAUDE-COMMON.md` verbatim here, then copy all sections from `PROJ_STARTER.md` below those. Add project-specific overrides at the bottom, clearly labelled.

See `CLAUDE-COMMON.md` → Standard User Rules and `PROJ_STARTER.md` for the full rule set.

### Project-Specific Overrides

_None — add below as needed._
