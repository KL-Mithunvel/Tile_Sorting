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
- Current build phase: Phase 1 (Laboratory Proof of Concept, charter §12.1) — acoustic capture and camera vision pipeline exist, plus a standalone acoustic analysis/labelling workbench (`Acoustic-Analysis/` submodule, v0.3); dimensional, control, sorting, and database layers are not started. The **camera pipeline has now run on a real webcam and on 5 real belt videos (92 tiles, including real damaged ones)** - but no belt tile has been checked by eye, so its crack/corner/model checks are unvalidated; nothing has run on the Pi, and the acoustic and pick-and-place work has not run against real tiles/rig. For the camera node pick-up page see `documents/programming/camera_node_handover.md`.
- Entry point (**Pi station decision service**, added 2026-10-11): `python -m acoustic.live_station` (`--demo` = synthetic mic + fake camera/UNO Q, no hardware or broker), run from `acoustic_node/python/` — mic + fusion with the camera + final grade to the UNO Q + operator GUI on :5001; on the Pi started by systemd via `run_station.py`. **Built and tested on the laptop only; never run on the Pi, a real broker, or a real mic with tiles.** Design + message contract: `documents/programming/station_coordination.md`.
- **Pi record + master sync (2026-10-15):** `fusion/store.py` gained an append-only `events` table + `store_id`; `fusion/blobs.py` keeps per-tile evidence (camera picture, tap WAV, preview JSON) with SHA-256 and a disk budget (`station.store.blob_dir`/`blob_budget_gb`); `acoustic/sync_api.py` serves it read-only on :5001 (`station.sync.token`); the master's `mirror.py` + `sync.py` pull a verified copy (`/tiles` page, `/api/pi/*`), `integrity.py` runs deterministic record checks that `llm.py` (local Ollama by default) explains on request. Design: `station_coordination.md` section 8. Laptop-only (`tests/test_pi_record_sync.py`); never run on the Pi, a real broker or a real model.
- Entry point (acoustic module, the older stand-alone mic tool): `python -m acoustic.live_monitor`, run from `acoustic_node/python/` (see Architecture below — the module moved out of a repo-root `acoustic/` package into this App Bricks-shaped node folder on 2026-08-03).
- Entry point (camera module): `python -m camera.live_dashboard`, run from `camera_node/python/` — opens a WiFi-reachable Flask dashboard (added 2026-08-07). Flags: `--list-devices`, `--backend {auto,usb,picamera2}`, `--device N`, `--video PATH [--loop]`, `--no-dashboard`; the source can also be changed from the page (Source bar). On the Pi station it is started by systemd via `camera_node/python/main.py` instead (see Deployment Notes).
- **Station→hardware map (updated 2026-09-20):** the camera station moved off the Arduino UNO Q to a **Raspberry Pi 5 (16 GB), Raspberry Pi OS Desktop 64-bit**; acoustic and pick-and-place stay on the UNO Q. Reasoning in `documents/project/Automation_Architecture.md` §5.2/§5.3 — in short, only acoustic uses the UNO Q's real-time MCU half (ToF trigger → solenoid ball-drop → capture window) and only camera is compute-bound (per-frame segmentation + a per-tile CNN/ViT classifier).
- **Proposed revision (2026-10-10, not built):** to save cost, the **Pi 5 would run both camera and acoustic analysis** (two separate systemd services), an **Arduino Nano (classic ATmega328P)** would run the striker + DC belt PWM (USB serial to the Pi, not on the LAN), and the **UNO Q would be pick-and-place only** (plus encoders and limit switches). The Mega is dropped. A second compute unit for audio alone is the fallback. Decision record, feasibility risks and consequences: `Automation_Architecture.md` §5.3/§5.5/§23; network: `documents/electrical/network_plan.md`. Until built, the code layout below still describes the 2026-09-20 split.
- `acoustic_node/` and `pick_place_node/` are organized as **Arduino App Bricks** projects (`app.yaml` + `sketch/` + `python/`), matching Arduino's own `app-bricks-examples` convention, so each can be opened in Arduino App Lab. `camera_node/` left that convention on 2026-09-20 (its `app.yaml` + `sketch/` were removed) and is now a plain Python service deployed with systemd — see `camera_node/deploy/`. All three have real Python-side code (`pick_place_node/`'s is the digital twin plus the whole Pi-side gantry control layer — protocol, coordinate model, sequencer, session, job runner, simulated MCU — all laptop-tested, plus the UNO Q's station link — placed/status/alarm reports and remote commands). `pick_place_node/sketch/` holds the gantry firmware: `gantry_core.h` is host-verified, `sketch.ino` (board glue) has **never been compiled for the board**; `acoustic_node/sketch/sketch.ino` is a real FSM, uncompiled. The App Lab deploy path (`arduino-app-cli`) was verified on the lab board 2026-09-01 — see Deployment Notes. Only one physical UNO Q board exists, plus (as of 2026-09-20) one Raspberry Pi 5.

---

## Documentation Architecture

Non-code project artifacts live under `documents/` at the repo root (added 2026-07-10).
Full conventions are in `documents/README.md` and `documents/deliverables/README.md` —
summary:

| Location | Contents |
|---|---|
| `documents/mech/` | CAD source files, drafted mechanical sheets (PDF), BOMs |
| `documents/electrical/` | Wiring/connection schematics, component datasheets, panel layout, `network_plan.md` (D-Link control LAN), `i2c_bus_plan.md` (encoders, ToF, mux) |
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

Acoustic capture, the camera vision pipeline, and a pick-and-place digital-twin HMI prototype exist so far. The pick-and-place gantry control layer is designed (`documents/programming/pick_place_control_protocol.md` — custom firmware + ASCII line protocol) and its **Pi/Linux half is built and tested against a simulated MCU** (`protocol.py`, `coordinate_model.py`, `sequencer.py`, `gantry_session.py`, `job_runner.py`, `serial_link.py`, `fake_mcu.py`, `gantry_backend.py`; 2026-10-12); the **firmware core** (`pick_place_node/sketch/gantry_core.h`) is built and conformance-tested on a PC against the same scenarios as the Python spec; `sketch.ino` (pins, timer ISR) has never been compiled for the UNO Q and nothing has run against the real board. Dimensional, conveyor, decision/grading fusion, sorting execution, and database layers from the full charter (`documents/project/project_charter.md` §5) are not started.

**Repo layout (since 2026-08-03, amended 2026-09-20):** every planned station gets its
own top-level node folder. Stations that run on an **Arduino UNO Q** follow the Arduino
**App Bricks** convention (`app.yaml` + `sketch/` + `python/` — see Arduino's
`app-bricks-examples` repo) so each can be opened as an app in Arduino App Lab. Stations
on other hardware use that platform's normal packaging — as of 2026-09-20 that means
`camera_node/`, which runs on a **Raspberry Pi 5** and is a plain Python service
(`python/` + `deploy/`, no `app.yaml`, no `sketch/`):

| Folder | Status |
|---|---|
| `acoustic_node/` | Real code — `python/acoustic/` is the working, tested module (below), migrated unchanged from the old repo-root `acoustic/`. **Added 2026-10-11:** `python/fusion/` (pure tile-decision logic), `acoustic/station.py` + `dashboard.py` + `live_station.py` (the Pi station service and GUI), `deploy/` (systemd unit + installer incl. mosquitto), `python/run_station.py` — see "Station decision service" under Key Modules. `sketch/` (laser/ToF trigger + ball-drop release) and `python/main.py`'s App Lab wiring are stubs, unverified on hardware. |
| `camera_node/` | Real code (added 2026-08-07). **Runs on a Raspberry Pi 5 (16 GB), Raspberry Pi OS Desktop 64-bit — not an UNO Q (decided 2026-09-20, `Automation_Architecture.md` §5.2).** `python/camera/` is a working, tested vision pipeline + WiFi dashboard (below), with three capture backends (USB/OpenCV, CSI/Picamera2, video file). `app.yaml` + `sketch/` were **deleted** 2026-09-20 — this node left the App Bricks convention. `python/main.py` is now a real systemd entry point, not an App Lab stub. `deploy/` (systemd unit + `install_pi.sh` + pinned deps) is written but **never run** — no Pi has been set up and `PiCameraCapture` has never seen a CSI module. Gained a configurable capture trigger (`line_trigger.py`) and an **optional, off-by-default** ONNX grade classifier (`grading_model.py` + the git-tracked `models/`) on 2026-09-20. |
| `master_node/` | **Optional** PC observer/collector/commander for the station (added 2026-10-11): `python -m master.live_master`, :8000. The line runs without it. **2026-10-12/13:** four views - `/` Three.js **Plant 3D** (`plant.py`; four-legged Cartesian XYZ gantry that picks and carries each tile; line control), `/analysis` (`analysis.py`), `/troubleshoot` (`troubleshoot.py` + a `health_events` history), `/insights` (`insights.py` rule-based anomalies + `llm.py` optional Ollama/Claude explainer; **parked, never run with a real model - see TODO.md**); `/classic` the old page. Control: **Stop/Start line** (`station/cmd line`, soft stop, NOT an e-stop), **Pause/Resume feed** (`station/cmd feed`: no NEW tiles to the gantry; it drains and idles), gantry hard pause. Modes: `--demo` (`demo.py`, pretends), `--sim` (`sim_line.py`: the REAL coordinator + gantry runner on a simulated machine), `--ai ollama|anthropic`. Verified on the laptop only (877 tests; `development/drive_master_pages.py` 35 checks in real headless Edge); **never connected to a real broker, Pi, UNO Q, Nano or belt.** Handover: `documents/programming/master_gui_handover.md`. |
| `pick_place_node/` | Partial (updated 2026-10-12) — `python/pick_place/` has the **digital-twin HMI** (Three.js browser twin + matplotlib desktop twin; `twin_prototype --backend mock|sim|serial`) **and the Pi-side gantry control layer**: `protocol.py` (pure encode/parse), `coordinate_model.py` (named pickup/stacks -> XYZ, stack heights), `sequencer.py` (job -> ordered commands), `gantry_session.py` (numbered commands, outcomes, latched alarms), `job_runner.py` (consumes the `FinalQueue`; config push, home gate, one retry on vacuum miss, parks only when the cup is empty, aborts on alarm, silence = fault), `serial_link.py` (pyserial, **never run on the board**), `fake_mcu.py` (`SimulatedMcu`, the protocol's executable spec incl. closed-loop `LOST_STEPS`/`SKEW_Y` alarms), `gantry_backend.py` (`mock`/`sim`/`serial`), `tile_feed.py`, `mock_job_driver.py`. All laptop-tested only. Bench apps: `bench_tests/stepper_smoke_test/` (4 CNC-shield steppers, checklist 0.5), `bench_tests/io_smoke_test/` (e-stop, limit switches, vacuum + Flask GUI :5070), `bench_tests/stepper_lib_spike/` (2026-10-12, checklist 0.6: can the Zephyr core generate accelerated STEP pulses — AccelStepper polled vs a k_timer ISR trapezoid; **written from documentation, NOT compiled or run**). **Added 2026-10-12 (later):** `station_link.py` (`GantryReporter`: `tile/<id>/placed`, retained `station/gantry/status` + last-will, `machine/alarm` edges; `GantryCommands`: `station/gantry/cmd` pause/resume/acknowledge/reset_stack always, home/recover only with `gantry.station.motion_commands_enabled`), `gantry_station.py` (`attach_station_link()`, headless `python -m pick_place.gantry_station`), `main.py` now runs it; the job runner PINGs every 0.2 s during a motion so the firmware's link watchdog does not trip on long moves. **Firmware:** `sketch/gantry_core.h` (portable C++: parser, FSM, trapezoid generator, homing, limits, e-stop, vacuum, closed loop, watchdog), `sketch/sketch.ino` (board glue, unverified), `firmware_host/` (the core behind a simulated machine + `host_mcu.py`). **Not built / not verified:** the sketch on the board, steps/mm, limit polarity, `k_timer` rate, the vacuum sensor, AS5600 reads, how Python reaches the MCU from the App Lab container, the gantry frame CAD / travel limits. |

Conveyor stays on the Arduino Mega (not App-Lab-class, not part of this convention). *(Superseded in the 2026-10-10 proposal: the Mega is dropped and an Arduino Nano takes the belt PWM and striker — see `Automation_Architecture.md` §5.5.)*

`development/` (added 2026-08-11) is a separate, dev-only top-level folder — **not** a
node, not part of the App Bricks convention above. It holds tooling for calibrating
`camera_node`'s vision thresholds against real tile photos (an interactive GUI tuner and
a batch analysis script) before those values get copied into
`camera_node/python/camera/config.yaml`. See `development/README.md`. Added 2026-10-11:
`corner_sensitivity.py` (the corner threshold trade-off in numbers), `build_review_sheet.py`
+ `tune_from_labels.py` (a self-contained HTML sheet of the borderline tiles from
`data/videos/` for a human to label fine / chipped / cracked, then threshold advice from
those labels — it prints, it never edits config), with the pure logic in `review_tools.py`
(tested in `tests/test_dev_review_tools.py`). Also `export_tile_dataset.py` (2026-10-11): every graded tile of every video in `data/videos/` saved as a lossless crop + a context picture + a labels CSV/JSON (pipeline readings, source video/frame/time, model guess, blank `human_label`) + manifest (git commit, settings) + the config used, in `data/tile_dataset/<date_time>/`; `--merge-labels` copies a review-sheet export's human labels in (pure logic in `dataset_tools.py`, tests in `tests/test_dev_dataset_tools.py`).

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

**Status (as of 2026-10-08): v0.3 built and working end-to-end** — full pure-DSP suite
(`dsp/`: conditioning, spectrum, octave bands, Schroeder decay, IEC 61672 weighting,
sound level, filter chain, spectral-subtraction denoise, NC/Ln environment), feature
extraction + validity gate, `classify/` reference profile + rule-based grader, SQLite
dataset store + WAV I/O, a headless CLI (`python -m acoustic_analysis.cli`), and a
15-screen Tkinter GUI (dark instrument-look shell, left sidebar nav), a **Slice screen**
(v0.2) that imports a tap-test video, detects every strike, and cuts/labels one snippet per
tap into the dataset (two label axes: defect class + grade tier 3A/3B/4/5; needs ffmpeg via
`imageio-ffmpeg`), and — new in v0.3 (2026-10-08) — a collection workflow: a guided
**Collect** screen (Setup / Listen / Review: sound-triggered capture with an adaptive
threshold, motor spectral profile + striker time-domain template subtracted from every
strike, automatic tile / no-tile sort, one-key labelling), a **Library** file manager
(real folders; rename / move / drag-drop / delete with undo; keeps wav + sidecar + database
path in step), a sidebar cut to Collect / Library / Analyze / Export with the rest under a
collapsible ADVANCED group (the app now opens on Collect, not Home), plain-language Basic
settings saved to `data/user_settings.yaml`, and a step-by-step README with screenshots.
237 tests. Slice has only seen *generated* videos, never real footage, and **Collect /
machine-noise removal has only been tested on synthetic signals — never on the real mic or
the real striker rig.** **Not done / not trusted:** never run
against a real microphone; no pistonphone calibration measured (levels are relative, not
dB SPL); no real good-vs-defective tile recordings so the grader and every `config.yaml`
threshold are unvalidated; `dsp/loudness.py` (phon/sone) deferred; no PyInstaller build.
See `Acoustic-Analysis/TODO.md` and its `.CLAUDE/CLAUDE.md` Known Technical Debt.

| Folder | Status |
|---|---|
| `Acoustic-Analysis/` | Submodule — standalone acoustic analysis + data-labelling desktop app (independent repo). **v0.3 built** (DSP suite, feature extraction, rule-based grader, SQLite dataset, CLI, 15-screen Tkinter GUI incl. video Slice, guided Collect with machine-noise removal, and a Library file manager; 237 tests) as of 2026-10-08. Pending: first run of Collect on the real mic + striker rig, pistonphone calibration, real-tile threshold tuning. |
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
| `acoustic_node/python/main.py` | Raspberry Pi systemd entry point (2026-10-12) — a thin shim over `acoustic.live_station.main()`, like `camera_node/python/main.py`. Replaced the App Lab stub and its unverified `poll_bridge_and_capture()` / `tapCount` polling (in git history); `app.yaml` + `sketch/` untouched. `run_station.py` was folded into it. |
| `acoustic_node/sketch/sketch.ino` | MCU-side ToF-triggered dual-solenoid (ARM+LOCK) tap-sequencer state machine, e-stop handling. Real logic now (not an empty stub), but never compiled/run on hardware — see `documents/electrical/schematics/acoustic_station_wiring.md`. |
| `camera_node/python/camera/config.yaml` | All tunable vision parameters (device index, HSV segmentation range, Canny thresholds, crack/corner thresholds, dashboard host/port) — see camera_node/README.md for the full list |
| `camera_node/python/camera/segmentation.py` | `segment_tiles()` / `segment_tile()` — find tile-coloured, tile-**shaped** regions (HSV threshold + shape gate), optionally only inside the belt ROI; `locate_tiles()` is the config-driven entry point. No I/O — synthetic-image testable. |
| `camera_node/python/camera/belt_roi.py` | `find_belt_roi()` — where on the frame a tile can be: `off` / `fixed` (normalised rect) / `color` (finds the belt by its own hue each frame). Fail-closed: a configured ROI that can't see the belt means *no tile*, not "search everywhere". No I/O — synthetic-image testable. |
| `camera_node/python/camera/region_tracker.py` | `RegionTracker` (nearest-centre identity across frames, global greedy assignment) + `LineCrossingTracker` (one `LineCrossingDetector` per tile). Gives every tile its own id so two tiles in frame never get confused. No I/O — synthetic-sequence testable. |
| `camera_node/python/camera/crack_detection.py` | `detect_cracks()` — grayscale → blur → Canny → keep long/thin contours → measure length + severity. No I/O — synthetic-image testable. |
| `camera_node/python/camera/corner_detection.py` | `detect_broken_corner()` — contour area vs. its own bounding rect area (`fill_ratio`) *plus* a distance-transform-based check on how deep any gap reaches (`missing_extent_fraction`) — the latter catches diagonal chips the former misses (see Key Modules below). Converts missing area/depth to real inches given the tile's known size. No I/O — synthetic-contour testable. |
| `camera_node/python/camera/tile_tracker.py` | `TileTracker` — debounced presence/absence state machine, counts tiles crossing the frame. No I/O — synthetic-sequence testable. |
| `camera_node/python/camera/line_trigger.py` | `LineCrossingDetector` — pure line-crossing trigger: fires once when a tile's bounding-box centre crosses a configured line, in **normalized** (0.0-1.0) coordinates so one config value is valid at any resolution. Hysteresis deadband stops a tile sitting on the line firing repeatedly from segmentation jitter. No I/O — synthetic-sequence testable. |
| `camera_node/python/camera/grading_model.py` | `TileGradeModel` — **optional** ONNX cosmetic grade-tier classifier (3A/3B/4/5) run as a *second opinion*; it cannot see cracks or broken corners. `onnxruntime` is imported lazily and `from_config()` returns None (never raises) when disabled/missing/uninstalled, so a station without it runs unchanged. `preprocess()` is pure and unit-tested; the ONNX session is not. Preprocessing is deliberately **copied, not imported**, from `camera_models/cam_edge/runtime.py`. |
| `camera_node/models/` | The staged, **git-tracked** deployable model + sidecar JSON (classes, preprocessing recipe, provenance, measured accuracy). Written by `camera_models/cam_edge/stage_model.py`; a scoped `.gitignore` exception, since `camera_models/runs/` is ignored and the Pi must get a model by cloning. Currently `tile_grade_yolo26n_cls.onnx` (6.2 MB, 82.9%). |
| `camera_node/python/camera/sources.py` | Pure paths for video in/out: recording folders named by date+time (`data/recordings/2026-10-11_14-30-05/`), newest-first listing of recordings and loose videos under `data/`, and `resolve_media_path()` — the only gate for which files the dashboard may open (video files under `data/` only). No camera, no OpenCV — testable anywhere. |
| `camera_node/python/camera/recorder.py` | `Recorder` — saves the **raw** feed to `video.mp4` (MJPG/avi fallback) + `meta.json`; measures the camera's real fps from the first frames before opening the file. Hardware/file I/O around a small pure core (`estimate_fps`). |
| `camera_node/python/camera/label_session.py` | `LabelSession` - **live labelling** (added 2026-10-12): saves every graded tile as lossless PNGs (crop / belt context / whole frame) with a HUMAN verdict (`fine/chipped/cracked/both/unsure` + tier `3A/3B/4/5` + note) into `data/labelled/<date_time>/`, beside the pipeline's readings (`labels.csv`, `human_labels.json`). A *batch verdict* labels every tile that follows; keys relabel the last one. The folder opens directly in `development/annotate_tiles.py` and `tune_from_labels.py`. Dashboard: Label panel + `/api/label/start|stop|batch|tile`. A failed write is recorded as a station error and never stops grading. Not run on a real camera or the Pi. |
| `camera_node/python/camera/station.py` | `StationController` — the one control surface the dashboard routes and the CLI drive: open a camera by index, open a recording / file, pause / loop, start / stop recording, background camera scan. Capture backends injectable, so it is tested with fake cameras. |
| `camera_node/python/camera/pipeline.py` | `process_tile()` — wires the three detectors above into one `TileRecord` + a first-pass rule-based grade. `tile_record_to_dict()` — shared JSON serialization, used by both the dashboard and `process_video.py`. |
| `camera_node/python/camera/snapshot.py` | `save_tile_snapshot()` — persists a departed tile's photo to `data/camera_captures/` (config: `capture_snapshots`), named by sequence number. I/O only, not unit-tested. |
| `camera_node/python/camera/capture.py` | Three same-shaped capture backends: `WebcamCapture` (USB/UVC via OpenCV), `PiCameraCapture` (CSI Raspberry Pi camera module via Picamera2 — lazy import, so this file still loads on Windows; **never run on hardware**), `VideoFileCapture` (saved file, for `process_video.py`). `create_capture(config)` picks one from `camera.backend` (`auto`/`usb`/`picamera2`); `list_cameras()` backs `--list-devices`. Real hardware/file I/O, not unit-tested. |
| `camera_node/python/camera/worker.py` | `CameraWorker` + `SharedState` — background thread wiring capture → segmentation → tracker → pipeline (+ snapshot save), thread-safe latest-value store for the dashboard. Tracks the *largest-area* sighting of each tile while it's crossing, not just the last frame before departure. |
| `camera_node/python/camera/process_video.py` | `process_video_file()` + CLI — offline counterpart to `worker.py`'s loop: runs the same pipeline against a saved video file, reports total tile flow count + a numbered per-tile crack/corner result for each tile that crossed. Not unit-tested; smoke-tested against a synthetic video (see `development/`). |
| `camera_node/python/camera/dashboard.py` + `templates/dashboard.html` | Flask app: `/video_feed` (MJPEG stream), `/api/status` (JSON), `/` (dashboard page). Serves on `0.0.0.0` so it's reachable over WiFi. |
| `camera_node/python/camera/live_dashboard.py` | CLI entry point, mirrors `acoustic_node`'s `live_monitor.py`. Flags: `--list-devices`, `--backend`, `--no-dashboard`. |
| `camera_node/python/main.py` | Raspberry Pi systemd service entry point — a thin `sys.path` shim over `live_dashboard.main()` so the service and the CLI can't drift apart. **Not** an App Lab stub any more (2026-09-20). |
| `camera_node/deploy/` | Pi deployment: `tile-camera.service` (systemd unit template), `install_pi.sh` (apt deps + unit install), `requirements-pi.txt` (USB-only venv fallback), `README.md`. Written 2026-09-20, **never run**. |
| `acoustic_node/nano_firmware/` + `acoustic_node/firmware_host/` | **Nano firmware (2026-10-12).** `nano_core.h` = all the logic as portable C++11 behind a `Hal` (checksummed serial protocol, 2000 ms Pi-silence watchdog that stops the belt AND inhibits the striker with manual resume + `ERR WDOGRESET`, ToF-triggered tap sequence with a fresh ToF read at the strike — no reading = no strike, belt-end ToF fail-safe, latched e-stop). `nano_firmware.ino` = board glue, **never compiled for AVR or run on a Nano**; pins D9 (belt PWM) and D12 (ToF 3 XSHUT) are new and unconfirmed. `firmware_host/` compiles the real core against a simulated board; `tests/test_nano_firmware_conformance.py` (33 tests, skipped without a C++ compiler: `NANO_HOST_CXX`/`GANTRY_HOST_CXX` or `pip install --target <dir> ziglang`) drives it over the real `tap_protocol.py` and `TapBridge`. `station.nano.resend_on_recover` (default false) lets the bridge re-send the last BELT/STRIKE after a trip. |
| `acoustic_node/python/fusion/clock_skew.py` | Peer clock disagreement from heartbeat `ts` -> a `clock` health row on the service view (warn only, `station.health.clock_warn_s`). The Pi serves chrony (`install_pi.sh`); clients not yet pointed at it. |
| `development/regression_check.py` + `regression_tools.py` | Re-runs `data/videos/` and diffs per-tile grade / crack / corner against a baseline (an export's `labels.json` or `--save-baseline`); exit 1 on any change. |
| `pick_place_node/python/pick_place/config.yaml` | Twin/workspace parameters — gantry envelope, patrol path, poll interval, dashboard host/port |
| `pick_place_node/python/pick_place/mock_state.py` | `MockGantrySimulator` — pure deterministic gantry-motion simulator, `.advance(dt_s)` given elapsed time (stand-in for a real `GantryWorker`). No I/O; unit-tested (`tests/test_pick_place_mock_state.py`) with synthetic time steps, same pattern as `TriggerDetector`. |
| `pick_place_node/python/pick_place/worker.py` | `TwinWorker` + `SharedState` — background thread advancing the simulator on a wall-clock tick into a thread-safe latest-value store. Thin timing wrapper, not unit-tested (same reasoning as `CameraWorker`). |
| `pick_place_node/python/pick_place/dashboard.py` + `templates/twin.html` | Flask app: `/` (Three.js digital-twin page), `/api/pose` (JSON live pose + vacuum state). Serves on `0.0.0.0`. Three.js vendored under `static/vendor/three/`. |
| `pick_place_node/python/pick_place/twin_prototype.py` | CLI entry point — mock gantry + WiFi twin dashboard, no real command routing. Mirrors the other nodes' `live_*.py` shape. |
| `pick_place_node/python/pick_place/desktop_twin.py` | Same mock motion rendered in a local matplotlib 3D window instead of a browser — no Flask, no network. |
| `pick_place_node/sketch/gantry_core.h` | The gantry firmware's logic — portable C++11, no Arduino includes, behind a `Hal`. Compiled and tested on a PC (`tests/test_gantry_firmware_conformance.py`, needs a C++ compiler or `GANTRY_HOST_CXX`; skipped without one). Spec: `pick_place_control_protocol.md` §5.7-5.8; `fake_mcu.py` is the Python executable spec it must match. |
| `pick_place_node/sketch/sketch.ino` | Board glue around the core: pins, `k_timer` step ISR, serial line reader, vacuum sensor, AS5600/TCA9548A reads. **Never compiled for the UNO Q.** Open items in `sketch/README.md`. |
| `pick_place_node/python/pick_place/station_link.py` / `gantry_station.py` | The UNO Q's outbound contract (`placed`, status, alarms, remote commands) and the service that wires it (`attach_station_link()`); the twin attaches the same link with `--station-host`. Tested over an in-memory bus against the real coordinator. |
| `pick_place_node/python/pick_place/protocol.py` / `coordinate_model.py` / `sequencer.py` | Pure, no I/O: line-protocol encode/parse (incl. closed-loop `pos` with encoder readings), named locations + stack heights -> gantry mm, one job -> ordered command list (Z-then-XY-then-Z, cup at safe Z whenever X/Y changes). Unit-tested. |
| `pick_place_node/python/pick_place/gantry_session.py` / `job_runner.py` | One conversation with the MCU (HMI and runner share one session so no reply is read by the wrong consumer) and the runner that executes queued tiles over it. Driven by `tick()`, never blocks; a gap between ticks rebases deadlines, a really silent MCU faults. Unit-tested with a fake clock (`tests/test_pick_place_job_runner.py`, `test_pick_place_gantry_link.py`). |
| `pick_place_node/python/pick_place/serial_link.py` / `fake_mcu.py` / `gantry_backend.py` | `SerialGantryLink` (pyserial reader thread, lazy import, **never run on the board**), `SimulatedMcu` + `LoopbackLink` (line-level simulation of the firmware), and the `mock`/`sim`/`serial` backend seam the dashboard and twin worker talk to; `create_job_driver()` picks the matching executor. |
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

Not applicable yet — the acoustic module runs on the dev laptop's built-in mic, the camera module on a dev laptop webcam, and `pick_place_node/`'s twin on a `MockGantrySimulator` or, with `--backend sim`, the real protocol + job runner against `SimulatedMcu`; no embedded/hardware target is involved for any of them. `TriggerDetector`, the `camera/` pure modules, and `MockGantrySimulator` are hardware-independent by construction (see Development Rules) — the same pure-logic / thin-wrapper split repeats as `acoustic_node/sketch/` (laser/ToF + ball-drop) and `pick_place_node/`'s real firmware is built (its `gantry_backend.py`/`serial_link.py` seam already exists, so only the MCU half is missing).

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

- `segment_tiles(frame_bgr, hsv_lower, hsv_upper, min_area_px, morph_kernel_size=5, min_fill_ratio=None, min_aspect=None, max_aspect=None, roi=None) -> list[TileRegion]` — HSV colour threshold, then every contour that is big enough **and tile-shaped**, largest first. `segment_tile(...)` (same args) is the first of those or `None`, kept for single-tile callers (calibration scripts, the model benchmark). `TileRegion` carries the bbox, area, contour, mask, and cropped image. No I/O; covered by `tests/test_camera_segmentation.py` with synthetic colored rectangles.
- **Shape gate** (`is_tile_shaped`, 2026-10-09): colour alone matched a brown sandal merged with the wood rim beside the belt (68x291 px, fill 0.28, aspect 0.23) and graded it as a tile. A region must also fill ≥ `min_fill_ratio` (0.6) of its rotated bounding rectangle and have aspect within `min_aspect`/`max_aspect` (0.5-2.0); real tiles on the clip measure fill 0.93-0.96, aspect 1.00-1.02. Deliberately **looser than `corner_detection.min_fill_ratio` (0.83)** so a chipped tile is still segmented and then flagged, not silently dropped. The result is the largest region that *passes*, so a non-tile bigger than a real tile can't hide it. Widen the aspect range for non-square tiles.
- **Belt direction + the trigger line (2026-10-11):** `find_belt_roi()` returns a `BeltRoi` — still an ordinary 4-tuple — with `.axis` (`"vertical"` = belt runs top-to-bottom, `"horizontal"`, or `None` if no clear direction) and `.tilt_deg`, from the belt mask's principal axis (fixed mode: the rectangle's pixel shape; a compact blob has none). `LineCrossingTracker` takes `orientation: "auto"` and learns the line's orientation — perpendicular to the belt — from the first `trigger_line.auto_frames` (8) frames that agree; nothing fires before then, it never changes afterwards, a new source relearns it, and `Observation.direction` reports `forward`/`reverse`. `validate_line_config()` refuses `auto` with the belt ROI off. The dashboard header shows "horizontal, perpendicular to the belt" once learned.
- **Belt ROI** (`belt_roi.py`, 2026-10-09): everything outside the belt is blanked from the mask before colour matching, because the wooden bench has the tile's hue. `locate_tiles(frame, seg_cfg)` is the config-driven entry point (used by `worker.py` and `process_video.py` so they can't drift apart) and returns `(regions, roi)`. `mode: color` uses the lab conveyor's green mat (`hsv_lower/upper` in config) and unions *every* sizeable belt-coloured area, because a tile lying across the belt splits the green in two. **Fail-closed**: a configured ROI that can't see the belt returns no tiles. With a wrong belt colour the station sees nothing, loudly, rather than grading the bench. Set `belt_roi.mode: "off"` for bench tests with no belt in view. On the real rig use `fixed`; `color` exists because the recorded clip is hand-held and drifts ~100 px.

### `camera/region_tracker.py`

- `RegionTracker(max_match_distance=0.15, max_missed=3).update(centers) -> list[int]` — one stable track id per centre, in input order. Matching is greedy over *all* (track, detection) pairs nearest-first, so the order regions arrive in (largest-first — the noisy part) can't decide a contested track. A jump bigger than `max_match_distance` is a new tile, never a teleport. `.lost` = ids dropped this call; a tile may go unseen for `max_missed` frames before its id is dropped.
- `LineCrossingTracker(line: dict, match_distance, max_missed_frames).update(regions) -> list[Observation(track_id, region, crossed)]` — a `LineCrossingDetector` per track, so each tile fires exactly once however long it shares the frame with another. `.crossing_count`, `.active_ids`. Config: `trigger_line.match_distance` (0.15) and `max_missed_frames` (3). Distance is Euclidean in normalised frame units; derived from the real clip (belt travel per frame vs. gap between neighbouring tiles, ~10x apart) — re-derive on the rig. Covered by `tests/test_camera_region_tracker.py`.
- Track ids climb faster than the tile count (id `#31` at tile 8 on the clip): a tile clipped at the frame edge fails the aspect gate for a few frames and re-spawns a track when it enters. Harmless for counting (each track fires at most once, and a tile that re-spawns already past the line never fires) but means ids are not "tile numbers"; `TileRecord.seq` is.

### `camera/crack_detection.py`

- `detect_cracks(tile_bgr, canny_low, canny_high, min_crack_length_px, min_aspect_ratio, minor_severity_max_length_px, blur_kernel_size=5) -> CrackResult` — grayscale → Gaussian blur → Canny → keeps only contours that are both long enough and elongated enough (aspect ratio) to be a crack rather than texture noise or a compact defect. Measures length from the winning contour's `minAreaRect`, grades severity against a length threshold. Raises `ValueError` on an empty image. No I/O; covered by `tests/test_camera_crack_detection.py` with synthetic drawn lines.

### `camera/corner_detection.py`

- `detect_broken_corner(contour, min_fill_ratio, max_missing_extent_fraction=1.0, tile_size_inches=None) -> CornerResult` — two independent broken-corner checks, OR'd together: (1) `fill_ratio` (tile's actual contour area / its own `minAreaRect` area) — a low ratio means area is missing; and (2) `missing_extent_fraction` (added 2026-08-11, fixing a real gap) — how far the deepest point of the gap between the actual contour and the ideal rectangle reaches, as a fraction of the tile's side length, via a distance transform. **Why both are needed**: `fill_ratio` alone under-catches diagonal/triangular corner chips — the realistic way ceramic actually breaks — because a triangular chip removes much less area than a square notch reaching the same distance into the tile. Confirmed empirically: a chip whose two legs each reach halfway across the tile's edge only dropped `fill_ratio` to ~0.87, above the production `min_fill_ratio` (0.83), so it would not have been flagged; `missing_extent_fraction` catches it (measures ~0.35 there, well above `config.yaml`'s `max_missing_extent_fraction: 0.22`). If `tile_size_inches` is given (config: 9.0, these tiles are 9x9in), `CornerResult` also reports `missing_area_sq_inches`/`missing_depth_inches` — real physical measurements of the break, using the tile's own detected pixel size as the scale reference (no separate calibration needed). Does not localize *which* corner is broken. Raises `ValueError` on a degenerate (zero-area) contour. No I/O; covered by `tests/test_camera_corner_detection.py`, including the diagonal-chip case above and the tile-size conversion.
- **Two shape checks (2026-10-12), from 88 owner-labelled belt tiles:** `max_aspect_ratio` (1.15; a snapped tile is no longer square - intact <= 1.11, snapped piece 1.47; assumes SQUARE tiles, null for rectangular) and `max_notch_depth_fraction` (0.08; deepest convexity defect / tile side - a clean bite with fill still 0.94). With `min_fill_ratio` 0.86 (was 0.83): 11/13 damaged caught (was 6), 2/58 fine rejected (was 0). Tiny sample, one rig - re-check on real rig photos. A ridge-filter crack detector was also tried the same day and did NOT beat Canny when held out by video (11/28 vs 15/28), so `detect_cracks()` is unchanged; faint hairlines (1-2 px) need more pixels / raking light, not a filter.

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
- **One station grade (2026-10-10).** Two things grade a tile and are blind to each other: the crack/corner rules (measured defects → Grade A / B / Reject) and the model (cosmetic tier 3A / 3B / 4 / 5, cannot see cracks). They are put on one scale `GRADE_ORDER = (Grade A, Grade B, Grade C, Reject)` via `grading.tier_map` (3A→A, 3B→B, 4→C, 5→Reject) and combined by `fuse_grade()` as the **worse of the two**: the model can downgrade a clean tile but can never rescue one the rules reject. The model only votes at ≥ `grading.model_min_confidence` (0.6); below that it **abstains** and the rules stand alone. On this rig it scores ~27-44% on 4 classes (near chance), so it abstains on every tile until retrained on this camera. At 0.0 it would have rejected a good tile on a 30% guess. `TileRecord` keeps all three: `grade` (final), `rule_grade`, `model_station_grade` (+ `model_used`). `grading_options(config, model)` builds the `process_tile` kwargs and validates the map against the model's classes at start-up (a missing class refuses to start); shared by `worker.py` and `process_video.py`. Display-only (as before) when there is no tier map or no model.
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

- `create_app(state) -> Flask` — `/` (dashboard page), `/video_feed` (MJPEG multipart stream from `SharedState`), `/last_tile.png` (lossless PNG of the last captured tile — the exact image the model receives; 204 until a tile exists), `/api/status` (JSON: `tile_count`, `recent_tiles` each a serialized `TileRecord` incl. per-stage ms, `tally`, `station_state`, `info`). Runs on `host: 0.0.0.0` from config so it's reachable over WiFi. Backend covered by `tests/test_camera_shared_state.py` via Flask's test client.
- **Page (reworked 2026-10-09):** top bar (clock, tiles crossed, Grade A/B/C/Reject tally — one vocabulary shared with the model stage and the table —, reject rate, station-state pill Waiting / Tile in view / Processing / Result ready), live feed with the trigger line + box + centre overlay, last-tile panel with a 5-stage strip (Captured / Cracks / Corner / Model / Grade, with ms each) and model probability bars, recent-tiles table. The **Captured** stage shows real data (the size of the crop that was graded, `at line · #<track id>`); it used to be the hardcoded text "OK". The **Model** stage shows `3B → B 41%` and whether it "counted" or was "not counted"; the **Grade** stage says "rules only" / "rules + model" / "model lowered it". `SharedState.tally` is cumulative since start-up, deliberately **not** derived from the 50-tile `recent_tiles` window. Layout is responsive: the feed sits in a viewport-height box with `object-fit: contain`, so a **portrait** frame (belt running down a phone-shaped video) is letterboxed instead of overflowing; panels stack under 900 px. Visually checked in headless Edge at 1400 and 400 px wide only; the HTML/JS has no automated tests.

### Video source, recording and replay — `sources.py`, `recorder.py`, `station.py` (added 2026-10-11)

The dashboard's **Source** bar and the CLI (`--device N`, `--video PATH`, `--loop`) both drive `StationController`; nothing reaches into the worker directly.

- **Camera**: `open_camera(index)` (USB via OpenCV; `csi=True` for a Pi module). Switching goes through `CameraWorker.switch_source()`, which stops the old source **before** opening the new one (opening one USB camera twice fails on Windows), puts the old one back if the new one won't open, and resets counts / tracks / results so one source's grades never carry into the next. The worker can start with **no** source; a failed start with a dashboard is "pick one on the page", headless exits 1.
- **Record**: raw un-annotated frames (so a replay gives what live would have), written *before* the guarded pipeline step so a frame the pipeline chokes on is still on disk. Folder `data/recordings/<YYYY-MM-DD_HH-MM-SS>/` (`_2`, `_3` on a same-second collision) with `video.mp4` + `meta.json` (`status` `recording` → `complete`; a killed app leaves a listable recording flagged incomplete). The camera's **real fps is measured** from the first `recording.warmup_frames` arrivals before the file opens — a webcam asked for 15 fps often delivers 7-30, and the wrong rate makes every replay play too fast or slow. Only a live camera can be recorded; changing source or shutting down finalises it; a recording failure ends the recording, never the inspection.
- **Replay**: `PacedVideoCapture` (in `capture.py`) plays a file at its own fps like a live camera, with loop and pause, resynchronising after a stall rather than fast-forwarding. End of a non-looping file → source status `finished`. `VideoFileCapture` (unpaced) is still what `process_video.py` uses.
- **Camera trouble**: 50 consecutive failed reads → source status `error: the camera has stopped delivering frames` (instead of a silently frozen picture), cleared on recovery.
- **API** (`dashboard.py`, all POST+JSON): `/api/source/camera`, `/api/source/file` (`path` or `recording`), `/api/source/playback` (`paused`/`loop`), `/api/record/start|stop`, `/api/cameras/scan`; `GET /api/sources`; `/api/status` gains `source` (+ playback progress), `recording`, `last_recording`, `controls`. `ValueError` → 400, `RuntimeError` → 409. **Security**: files only from under `data/` (`..` escapes, symlinks out, non-video files refused); POST must be `application/json`, so a web page on another site can't drive the station without a CORS preflight the app never answers; `dashboard.controls_enabled: false` refuses every control (403) and lists nothing. The dashboard is served on `0.0.0.0` — anyone on the WiFi can use the controls unless that is turned off.
- **Verified on real hardware 2026-10-11** (the laptop's built-in webcam): connect, live feed, record 9.2 s / 284 frames at a measured 31 fps, stop, list, replay at 31 fps. That test found a real bug — see Platform Constraints (camera scan).

### Image quality: capture settings, sharpest-frame picking, frame rate (added 2026-10-11)

Why a frame is blurry, measured on the sample recordings and the laptop webcam, not assumed:
- **Motion blur = belt speed x exposure time.** Tiles cross the frame at **14.5 px/frame = 435 px/s** (1080-px frame, 30 fps). The webcam's auto exposure is **1/64 s**, so each frame is smeared by **~7 px** — enough to erase a 1-3 px hairline crack. A faster shutter needs more light: manual 1/128 s gave a nearly black image (brightness 26/255) in room light, and **the webcam's driver rejects every gain change**, so brightness cannot be recovered in software. **The real fix is ~8x more light on the belt** (1/256 s => ~2 px smear), or a slower belt / strobed light. `development/camera_probe.py` measures all of this for a given camera.
- **The old config was the frame-rate problem**: it asked for 640x480 @ 15 fps; the webcam delivers ~30 fps at 640x480, 1280x720 AND 1920x1080 (verified 29.8-29.9 fps with the new defaults). Defaults are now 1920x1080 @ 30, fourcc MJPG, buffer_size 1 (a deep driver queue makes frames old), `camera.exposure {mode, value}` (manual uses the backend's own codes: DirectShow 0.25/0.75 and log2 seconds, V4L2 1/3 and 100 us; not guessed for backends not known to honour it). `WebcamCapture.settings` reads back what the driver ACTUALLY gave (the dashboard shows it).
- **The pipeline is not the bottleneck on this laptop**: belt ROI + tracking + grading + overlay + JPEG = **24 ms/frame (41 fps)** at 1080p; a tile is graded in 1.7 ms. The 16-22 fps seen processing the recordings offline was H.264 *decoding*. Expect the Pi to be several times slower (belt ROI colour mask is ~13 ms of the 21 ms per-frame cost; a downscaled ROI search is the obvious saving if it matters there).
- `LatestFrameCapture` (live cameras only): camera read on its own thread, `read_frame()` returns the newest frame not yet delivered, skipped frames counted instead of queued; `RateMeter` gives capture fps and processing fps; the dashboard shows "1920x1080 · camera 29.8 fps · checks 30.4 fps · N% skipped". Measured effect on this hardware is modest (same throughput under load; it makes lag visible and guarantees the newest frame) — it matters more on a slower machine. The browser feed is downscaled to `dashboard.stream_width` (960) before JPEG encoding (detection always uses the full frame).
- **Sharpest-frame picking** (`sharpness.py`, `frame_picker.py`, `trigger_line.sharpest_band` 0.06, 0 = off): for each tile every frame within +/-6% of the frame of the line is scored and, once the tile has crossed and left that stretch, the SHARPEST is graded (costs a few frames of delay; graded position up to ~64 px from the line, median 38). The metric is the 95th percentile of the edge gradient ALONG THE BELT in a thin band around the tile outline — chosen by experiment: real frames blurred synthetically 0-30 px fell steadily 1.00 -> 0.50, where the first idea (Laplacian variance inside the tile) was flat and non-monotonic. The belt direction comes from the belt ROI, else perpendicular to the trigger line. Scores only compare frames of the SAME tile. **Measured on all 92 tiles (off vs on): graded frame 1.30x sharper (median; 25th-75th pct 1.15-1.63x), sharper for 91%, blurrier for 0%, same 92 tiles counted.**

### Station decision service — `fusion/`, `acoustic/station.py`, `acoustic/dashboard.py`, `camera/publisher.py` (added 2026-10-11)

Owner decision 2026-10-11: **the Pi computes the tile's final grade** (camera result + tap result) and sends it to the UNO Q; the **MQTT broker runs on the Pi**; the master PC is an optional observer/collector/commander and the line runs without it; the camera mints the tile id. Full design, topic/payload contract, fail-closed table and bring-up checklist: `documents/programming/station_coordination.md`. Summary:

- `fusion/` (pure, no I/O): `grades.py` (one vocabulary; `Hold` = do not sort automatically), `ledger.py` (`TileLedger`: FIFO — a tap binds to the *oldest waiting* tile; max-wait expiry, orphan taps, duplicate camera messages ignored), `final_grade.py` (`fuse_final()`: worse of camera and sound; sound counts only if `vote_enabled`, valid, graded, confident — default **not counted**), `store.py` (SQLite, one row per tile), `transport.py` (topics, `InMemoryBus`, `MqttBus` — paho 1.x/2.x, lazy import, re-subscribes on reconnect), `coordinator.py` (`StationCoordinator`: bus <-> ledger <-> store, stop-and-wait delivery to the UNO Q with acks/retries, health, alarms, `station/cmd`).
- `acoustic/features.py` (peak Hz, centroid, SNR, decay; optional rule grade from `station.reference`, null = abstain), `acoustic/station.py` (`StationService`: capture + analysis + tick threads; a missing mic is a reported FAULT, not an exit; injectable capture), `acoustic/dashboard.py` (Run view `/` read-only kiosk screen, Service view `/service`; controls gated by `station.dashboard.controls_enabled`, JSON-only; tile pictures only from under `data/`), `acoustic/demo.py` (synthetic peers).
- `camera/publisher.py`: forwards each graded tile from a **live** camera only (never a replayed file), tile id `T<date>-<time>-<n>` from its own counter (the camera `seq` restarts per source), heartbeat + last-will on `station/camera/status`; `config.yaml` `mqtt:` is **off by default**. `worker.py`'s `_forward_tile` can never cost an inspection.
- The camera and `fusion/` never import each other; `tests/test_station_contract.py` keeps the shared JSON contract in step.
- **Added later 2026-10-11:** `acoustic/tap_protocol.py` + `tap_bridge.py` (Nano serial link: checksummed lines, `station/tap` with a `tile_present` flag, keep-alive pings that feed the Nano's belt watchdog, retained `station/nano/status`, `station/belt` / `station/strike` commands; spec `documents/programming/nano_serial_protocol.md`, **no Nano firmware yet**); tile-present handling in the coordinator (empty strike uses no slot; a tile under the striker with no camera result becomes an `UNKNOWN-…` Hold final so the UNO Q's queue stays in step); `fusion/stats.py` + per-tile `transit_s` (camera->tap time, suggested `max_wait_s`); `pick_place/tile_feed.py` (the UNO Q's `FinalQueue` + `FeedClient`: FIFO, de-duplicating, acknowledging, back-pressure by not acking when full; `/api/feed`; bench CLI; its consumer is `pick_place/job_runner.py` (2026-10-12), laptop-tested against `SimulatedMcu` and the compiled firmware core; the gantry reports back with `tile/<id>/placed` + `station/gantry/status`, which the coordinator records and turns into `health.gantry` and a `not_placed` alarm — see `station_coordination.md` §4); `master_node/` (optional observer/collector/commander, Flask :8000, own SQLite + CSV, commands off by default); `acoustic_node/deploy/kiosk/` (Chromium kiosk autostart, never run).
- Config: `acoustic/config.yaml` `station:` block. Verified: 568-test suite, the demo run end to end, GUI in headless Edge. **Not verified:** a live broker, the Pi, a real mic, the Nano bridge (`station/tap` — format undesigned), the UNO Q consumer (not written).

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
yet including any damaged-tile examples. **Video and derived data (added 2026-10-11, all under `data/`, all gitignored):** `data/videos/` (the phone clip + four 1920x1080 laptop-webcam recordings with real damaged tiles), `data/recordings/<date_time>/` (the dashboard's Record button: raw `video.mp4` + `meta.json`), `data/review/` (the review sheet HTML), `data/tile_dataset/<date_time>/` (every graded tile as a lossless crop + context picture + `labels.csv/.json` + `manifest.json` + `config_used.yaml`; see `development/README.md`). Their labels are the pipeline's readings plus an empty `human_label` column - nothing there is verified by eye until someone fills it in. Excluded from git via `.gitignore` (`data/`,
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
- **Never probe a camera that is already streaming** (found 2026-10-11 on the real laptop webcam). Camera scanning works by opening each index; on Windows, opening the camera that is currently in use takes the device from the running capture, every read then fails, and the live feed (and any recording in progress) freezes — a recording showed 105 frames at 3.5 s and then barely moved. The dashboard starts a scan when the page loads, so this hit immediately. `list_cameras(skip_usb=...)` and `StationController.scan_cameras()` now skip the open camera (it is still listed, as "in use"). Any new code that enumerates cameras must do the same. Unit tests use fake cameras and could not have caught this.
- On Windows the first open of the webcam takes ~2 s (MSMF); if it is slow or the picture is wrong try `camera.api_preference: "dshow"`. OpenCV reports `fps: -1` for this webcam, so never trust the reported fps — the recorder measures it.
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
- **The Pi station decision service has never run on the Pi** (added 2026-10-11): `fusion/`, the station GUI and the camera publisher are tested on the laptop (in-memory bus, synthetic microphone) but not against a real mosquitto, a real USB microphone through PortAudio, `install_pi.sh`, or the systemd unit. The UNO Q side (`FeedClient` + `FinalQueue` in `pick_place/tile_feed.py`, consumed by `job_runner.py`) is written but laptop-tested only — paho-mqtt on the UNO Q is unconfirmed; the Nano firmware end of the tap bridge is not written. `ledger.max_wait_s` (30 s) is a placeholder because belt transit time is unmeasured; a *missed camera detection* shifts every later tile/tap pairing by one until a timeout and the FIFO cannot detect it alone. The sound features are unvalidated and `station.reference` is null, so sound has no grade and does not vote. See `documents/programming/station_coordination.md` §6-7.
- **Acoustic tap-trigger architecture is designed and unit-tested but has never touched real hardware** (added 2026-08-20, see `documents/electrical/schematics/acoustic_station_wiring.md` for the full decision): the ToF-triggered dual-solenoid (ARM+LOCK) gravity-drop mechanism, its MCU state machine (`sketch.ino`), and the Python-side hardware-trigger audio capture path (`hardware_trigger.py`, `tap_sequencer.py`, `capture.py`'s `trigger.mode` switch) are all written and covered by synthetic-input unit tests, and the `"simulated"` trigger mode exercises the whole path end-to-end on a dev machine — but no ToF sensor, solenoid, or MOSFET driver has been wired up yet, `sketch.ino`'s `readTofDistanceMm()` is a placeholder, and the App Bricks bridge-variable read in `main.py` (`read_tap_count()`) is unconfirmed. Ball mass/drop height and the reload/reset mechanism between tiles remain undecided.
- **The control network is partly configured** (updated 2026-10-12, was "planned, not configured"). The D-Link DIR-825 (isolated LAN, no uplink) is factory-reset and set to `192.168.1.1/24` with DHCP `.100-.199` and reservations for the UNO Q (`.15`, via the USB-C Ethernet hub, tested) and the Pi (`.11`), both verified; `tools/uno_q/config.bat` `UNO_Q_HOST` is `192.168.1.15`. **Still open:** the router admin password, Wi-Fi `TileLine` (WPA2) not seen in a scan, WPS/remote admin off, router SSH/Telnet off once the Pi is set up, the laptop/master reservation, and the hub's power passthrough under gantry load. **Time:** decided 2026-10-12 that the Pi serves chrony (`install_pi.sh`, never run); the UNO Q and master PC are not yet pointed at it. Everything is in `documents/electrical/network_plan.md`.
- **The shared-Pi / Nano compute plan is a proposal with real risks** (added 2026-10-10, `Automation_Architecture.md` §5.3): camera + mic + Nano must all sit near one Pi (short CSI ribbon, ~5 m USB); Pi fan noise next to the tap microphone; Nano has 2 KB RAM and two external interrupts; **conveyor speed is open-loop** (DC PWM, no calibration, no feedback) although the camera trigger line, acoustic settle time and pick position all depend on it; the belt's control path (master → MQTT → Pi → serial → Nano) runs through the Pi and router, so the Nano must stop the belt by itself on a heartbeat timeout; stoppers/diverter/pneumatics have no owner now the Mega is dropped. No code for any of it exists (`sketch.ino` still targets the UNO Q; serial tap-event path undesigned).
- **The I2C buses are planned on paper only** (added 2026-10-10, `documents/electrical/i2c_bus_plan.md`). Parts: TCA9548A mux, 4x AS5600 encoders (one per stepper), VL53L1X ToF sensors — **the wiring doc still says VL53L0X**. Two separate buses (UNO Q 3.3 V, Nano 5 V). Problems found, none yet resolved: **two VL53L1X on the Nano both default to `0x29`** (needs XSHUT re-addressing at every boot, with a fail-safe so a dead ToF 3 stops the belt); long gantry I2C runs beside TMC2208 drivers; whether the single-shaft 42HM48-1684 motors have a rear shaft for the AS5600 magnet; module voltages vs the 5 V Nano bus; unmeasured encoder sweep time (about 1 ms at 400 kHz, about 4 ms at 100 kHz, estimated); and the UNO Q Zephyr core's I2C timeout behaviour. Pick-and-place is now **closed-loop** (supersedes "open-loop, no encoders in v1" — rewritten in the connection plan, but `pick_place_control_protocol.md` and the firmware were not updated).
- **Two e-stops, no shared wiring** (added 2026-10-10): the Nano's cuts power upstream; the UNO Q uses the CNC-shield Abort pin. Whether the shield e-stop also drops the 12 V motor supply is unconfirmed. ToF 3 (belt end, same spot as the UNO Q's pickup ToF 1) stops the belt to prevent a fall; its mount distance (ToF latency plus belt coast) is unmeasured.
- The node-folder split still outruns the hardware. As of 2026-09-20 there is **one UNO Q** and **one Raspberry Pi 5**, for three stations (`acoustic_node/`, `camera_node/`, `pick_place_node/`) — and the Pi hasn't been set up. Note that the Pi decision does *not* relieve the UNO Q shortage: acoustic (tap sequencing) and pick-and-place (step-pulse generation) both genuinely need an MCU and both want that one board; it only stops the board being spent on the node that least needed it. Pick-and-place bench bring-up currently has it. Don't infer hardware procurement from the repo structure.
- **Pick-and-place: Pi side and firmware core built and simulated; board and hardware not started** (updated 2026-10-12). Decided 2026-08-31/09-01 (`documents/programming/pick_place_control_protocol.md`, `documents/electrical/schematics/pick_place_hardware_connections_plan.md`, `documents/pick_place_todo.md`): custom minimal firmware on the UNO Q MCU (**not** GRBL/Klipper — no STM32U585 port, FR-18 forbids off-the-shelf G-code), Linux owns the coordinate model + sequencer + HMI, line-based ASCII protocol between them. Hardware chosen and in hand: CNC Shield V3.10, 4× TMC2208, 4× 42HM48-1684 NEMA 17, Mean Well LRS-150-12; 3 logical axes (Y beam = 2 motors, A cloned from Y in hardware); vacuum-cup end effector. **What exists as code:** the whole Pi side (protocol, coordinate model, sequencer, session, job runner, serial link, simulated MCU, backends, digital twin, station link + headless service), the firmware core `gantry_core.h` (verified on a PC, incl. the real `JobRunner` placing tiles through it), `sketch.ino` glue, and three unrun bench apps. **Verification caveat:** the core and `SimulatedMcu` were written by the same hand from the same spec, so their agreement shows self-consistency plus the firmware-only checks (timing, step counts, homing geometry), not that either matches the board. **Not done:** compiling `sketch.ino` for the UNO Q; the board's `k_timer` rate (checklist 0.6); limit/e-stop polarity (the wiring doc and the NC-to-GND logic disagree — `io_smoke_test` settles it); steps/mm; the vacuum sensor type; AS5600 reads and `SETTOL` values; how Python reaches the MCU from the App Lab container (Serial vs RouterBridge); the physical rig (frame CAD, travel limits, stack pitch blocked on SMTW tile size, whether the shield e-stop drops the 12 V supply); most of `documents/pick_place_todo.md` (about 11 of ~100 ticked).
- `.CLAUDE/CLAUDE.md` previously documented a `venv/` at the repo root; the actual local environment found during this session was `.venv/` (PyCharm default) with only `pip` installed — `requirements.txt` had not been installed into it. Re-verify which venv convention is actually in use before trusting either name blindly.
- **Camera vision thresholds are now data-driven but still unvalidated for true-positive sensitivity** (updated 2026-08-11, was "unvalidated" as of 2026-08-07): `camera_node/python/camera/config.yaml`'s HSV segmentation range, crack-detection Canny thresholds/`border_margin_px`, and corner `min_fill_ratio` were calibrated against 311 real terracotta tile photos (`data/`, see `development/README.md`) — no longer blind guesses. But every one of those photos is a **known-intact** tile, so calibration only established a false-positive floor (loose enough not to flag a healthy tile), not whether a real crack or broken corner actually gets caught — there are no damaged-tile photos yet. `min_tile_area_px` and `border_margin_px` are also absolute pixel values tied to the close-up calibration photos' resolution, not the live 640x480 pipeline's real (undecided) camera distance — re-derive both once that's fixed. `min_crack_length_px`/`min_aspect_ratio` were not part of the calibration and remain the original placeholder guesses. See `camera_node/README.md` Known Limitations and `development/README.md` for the full reasoning. Do not trust any crack/corner detection result as a true-positive guarantee until real damaged-tile photos exist to validate against.
- **`detect_cracks()` had a border-silhouette false-positive bug** (found 2026-08-11 while running the calibration above, fixed same day): it runs Canny on segmentation's tight bounding-box crop, so the tile's own edge against the background sits right at the crop's border — a long, thin, high-contrast line indistinguishable from a real crack by the existing length/aspect-ratio filter. Measured ~98-100% false-positive rate on known-intact tile photos before the fix. Fixed by adding `border_margin_px` (blanks a border band of the Canny edge map before contour-matching — see `crack_detection.py`'s docstring and `development/analyze_dataset.py`), which dropped the false-positive rate to ~2-11% depending on margin. Worth remembering if crack detection is ever reworked: any crop-based edge detector on this kind of tight bounding-box input needs this kind of border exclusion.
- **The staged ONNX grade model is accurate-as-measured but measured on a narrow basis, and validated only on intact tiles** (added 2026-09-20). `camera_node/models/tile_grade_yolo26n_cls.onnx` reproduces 82.89% through `camera_node`'s own path (re-verified end-to-end this session: 100% label agreement with `cam_edge`'s runtime, 0.00000000 preprocessing tensor diff) — but that is **76 val images, 95% CI 72.9-89.7**, i.e. roughly one tile in six misgraded, and per-class recall is uneven (grade "4" only 0.667). The training set is 376 photos of **intact** tiles from a camera that is not the station's camera, at a working distance that is not the station's. It classifies cosmetic grade tier only and **cannot see cracks or broken corners**, which is why `config.yaml` ships it `enabled: false` and `pipeline.py` records it as `model_grade` beside — never instead of — the rule-based grade. Do not let it drive a sorting decision unsupervised (**2026-10-10: it can now vote on the final grade, but only through `grading.model_min_confidence` = 0.6 and only to downgrade, so it abstains on every tile on this rig today — see "One station grade" under `camera/pipeline.py`**), and expect to retrain once the Pi's camera and lighting are fixed.
- **FIXED 2026-10-09 — was a confirmed bug: `segment_tile()` returning only the largest region made tile identity flip when two tiles share the frame.** Fix: `segment_tiles()` returns every region and `region_tracker.py` keeps each tile's identity by nearest-centre matching, with one crossing detector per tile. On the real clip with the shipped config: 7 tiles from the live `CameraWorker` and 7 from `process_video`, every capture at y≈533 (line at 512), and still 7 with the `max_step` guard disabled — tracking alone does the work. Tested with synthetic sequences incl. two equal-area tiles (`tests/test_camera_region_tracker.py`, `tests/test_camera_worker_resilience.py`). **Still unvalidated on a real rig**: `match_distance` is derived from one hand-held clip, and the original diagnosis follows for context. Original entry:
- **(historical) the original diagnosis — `segment_tile()` returning only the largest region makes tile identity flip when two tiles share the frame** (found 2026-09-20 running the real belt footage through `development/process_conveyor_video.py`). This is the single most important thing found so far about the live pipeline, and it is **not** a trigger bug. On `data/videos/WhatsApp Video 2026-08-26 at 7.27.04 PM.mp4`, **34.8% of frames (596/1715) hold two tile-sized regions**, and the two tiles' areas land within **~0.02% of each other** (measured: 26283 vs 26278 px at frame 613). Whichever is fractionally larger wins, so "the tile" flips between two physically different tiles on pure segmentation noise, and the reported centre teleports ~0.6 of the frame height in a single frame. Any centre-based trigger then fires spuriously: 12 crossings reported where 7 are real. The tell is *where* a crossing is recorded — a genuine one lands just past the line (`cy≈0.52`), an identity flip lands far from it (`cy≈0.83-0.86`), because the centre jumped there rather than travelled there. **`LineCrossingDetector`'s hysteresis cannot fix this and is not meant to** — it is a spatial deadband for jitter *at* the line, and a 0.6 jump clears any deadband (swept to 0.08, no change). The script's old hand-rolled 12-frame temporal cooldown *appeared* to give the right answer (9) purely by swallowing repeats inside 0.4 s — it was masking the flip. The real fix is upstream: track N regions and hold tile identity across frames (nearest-centre association or similar), which is a design decision, not a tweak. Everything downstream — tile counts, per-tile grades, snapshots, the model's second opinion — is untrustworthy whenever two tiles are in frame, so this blocks any multi-tile conveyor run. **Partial mitigation added 2026-10-09:** `LineCrossingDetector(max_step=...)` (config `trigger_line.max_step: 0.15`) treats a centre jump bigger than real belt travel as a *different tile*: it drops the remembered side and never fires. On the same clip every trigger now lands at y≈534-576 (just past the line at 512), none away from it. It does **not** give tiles a stable identity — a flip right at the line can still swallow a real crossing (5 counted in ~1.5 loops of a ~7-tile clip, not verified frame by frame) — so the tracker redesign above is still needed.
- **Sharper frames moved the crack flags — the crack detector is the weak link, not the picture** (2026-10-11). Grading the sharpest frame instead of the crossing frame made 10 of 92 tiles change grade, and "major crack" flags went **8 -> 16** (Rejects 15 -> 19, Grade A 63 -> 60). Looked at by eye, the 10 changed tiles: 5 gained a crack nobody can see (tiles that look intact; one is the embossed *back* of a tile whose relief pattern reads as cracks), 3 lost a false crack flag, 1 chipped tile moved A -> B. So the sharper frame is real but `detect_cracks()` (Canny + fixed length/aspect thresholds, never calibrated, ~32% of intact photos flagged) simply flags different noise when given more detail. **Picking is ON by default because a sharper frame is what the model and a future calibrated detector need, but until the crack check is recalibrated from labels (`development/build_review_sheet.py` + `tune_from_labels.py`) expect ~4 more false Rejects per 92 tiles than with `sharpest_band: 0`.** Not measured: whether the sharper frame helps the ONNX grade model (it was never run on these videos at a useful confidence).
- **First results on real damaged tiles — five videos, 92 graded tiles (2026-10-11), shipped config, no overrides.** Videos live in `data/videos/` (gitignored): the phone clip + four 1920x1080 laptop-webcam recordings; the webcam ones show **real broken tiles and shards**, the first defect footage in the project. **What works:** the auto line is horizontal (perpendicular to the belt) at the frame's middle on all five; the belt is found in 100% of frames of every video; 13 / 30 / 27 / 15 / 7 tiles; **all 92 crossings are forward (0 reverse), no tile crossed twice, steady 1.3-3.9 s gaps**; every graded frame is one distinct tile (checked visually on all 13 of the broken-tile video); obvious shards and big chips are correctly Reject. **Overall: 62 Grade A, 12 B, 18 Reject** (before the hue fix below). **What does not work yet — judged by eye on the 13 tiles of `WIN_…12_16_03`:** (a) **corner check misses real chips** — tiles #6 (a visibly cut corner) and #7 (a chipped edge) read fill 0.855 / 0.869, just above the 0.83 threshold, graded B / A; this is the sensitivity trade-off in the corner entry above, now with real examples (a threshold ~0.88 would catch both and also flag ~6 more tiles in `…12_25_11` whose damage is unverified); (b) **crack check flags intact-looking tiles** — 8 "major" and 13 "minor" cracks over 92 tiles, and tiles #10/#11 (minor) and `…12_28_20` #6 (major, 124 px) show no visible crack; (c) **a colour cast cost an intact tile its grade** (see next). Not measured: ground truth for the other 79 tiles — no one has labelled them, so these are impressions from 13 images plus the counts, not accuracy figures.
- **Webcam colour cast broke tile segmentation** (found 2026-10-11): in `WIN_…12_16_03` an intact tile was graded Reject (corner fill 0.60) because a lighting/white-balance gradient moved part of it toward red (lost pixels hue 0-3 vs the tile range 6-15, same brightness — a brightness-cap change was tried first and did nothing), so those pixels left the mask and the outline looked broken. Tile hue floor lowered 6 → 3: corner flags over the 92 frames 13 → 10, that tile Grade A, no new false flags on the lab clip; 0 or wrapping past 179 starts flagging a lab-clip tile. The original range came from 311 photos with a floor of 6 — re-check on real rig photos. **The belt is TEAL in the webcam recordings (hue ~86-93) but green in the phone clip**, so `belt_roi.hsv_upper` hue is 95 (85 found the belt in 3/24 frames of two videos; 100+ lets jeans/shadows in).
- **Non-tile objects trigger the line — FIXED 2026-10-09** (found and fixed the same day, same clip; shape gate + belt ROI + worker guard, see Key Modules; real clip with the shipped config: no sandal or wood trigger, 0 errors). Original finding: with the saturation fix a brown sandal plus the dark wood rim beside the belt segments as one region (bbox 68x291, `fill`≈0.28, aspect 0.23) and fires a "crossing" — graded Reject, the only Reject on a clip of otherwise-Grade-A tiles. Real tiles on the same clip: aspect 1.00-1.02, fill 0.93-0.96, 23-29k px. No exception was raised for any region on any of the 1715 frames (checked with both saturation floors) — the symptom is a bogus tile in the count/tally, not a crash. `worker.py`'s loop used to have no try/except around the per-frame work, so one genuine exception would have silently killed the capture thread — now `CameraWorker._safe_step()` catches it, counts it (`SharedState.record_error`, shown as a red badge on the dashboard and in `/api/status` `errors`), logs the traceback once per distinct message, and keeps inspecting. The fixes: a shape gate (`segmentation.min_fill_ratio`/`min_aspect`/`max_aspect`) and a belt ROI (`segmentation.belt_roi`). **Not validated against real shoes/feet on a real rig** — one clip, one sandal.
- **The line-crossing trigger's tuning values are still rig-dependent and unvalidated** (updated 2026-09-20). The detector itself now *has* run against real conveyor footage (above) and behaved exactly as specified given its one-tile-in-frame precondition. But `trigger_line.position` depends on where the camera ends up mounted and `direction` on which way the belt runs, and `hysteresis: 0.02` was reasoned about (≈13 px at 640 px wide) rather than measured against real segmentation jitter — on the real clip it made no difference to the count at any value from 0.0 to 0.08, because the noise there was identity flipping, not jitter. Re-derive all three once the rig exists.
- **`onnxruntime` is not available through apt on Raspberry Pi OS**, so `install_pi.sh --with-grading` pip-installs it into the system interpreter with `--break-system-packages` (added 2026-09-20, **never run**). That is a deliberate, narrow choice — one pure-addition wheel into the interpreter the service already uses, rather than a venv that would break `python3-picamera2`'s numpy/libcamera ABI — but it is unverified, and the wheel may simply not exist for the Pi's Python/arch. The step is non-fatal by design: grading stays off and the station still inspects tiles.
- Crack "type" reported by the camera pipeline is severity (minor/major) + measured length, not a defect taxonomy (hairline vs. structural, edge vs. center crack) — that needs real labeled photos to design against.
- **`detect_broken_corner()`'s `fill_ratio` check alone under-caught diagonal corner chips** (found and fixed 2026-08-11, same session as the crack bug above): a triangular chip — the realistic way ceramic actually breaks — removes much less *area* than a square notch reaching the same distance into the tile, so `fill_ratio` badly under-represents how far a diagonal break reaches. Confirmed empirically: a chip whose two legs each reach halfway across the tile's edge only dropped `fill_ratio` to ~0.87, above the production `min_fill_ratio` (0.83) — it would not have been flagged. Fixed by adding `max_missing_extent_fraction`, a second check on how *deep* the gap reaches (distance transform), which does catch it — see `corner_detection.py`'s docstring. Also added real-world missing-area/depth measurements (`missing_area_sq_inches`, `missing_depth_inches`) using the tile's known 9x9in size. **Still unvalidated against a real broken-corner photo** — only one real defect photo has turned up in the whole dataset so far (a crack, in `data/9x9-5(Cam)/DSC_0065.JPG`, correctly caught by `detect_cracks()` — see `development/README.md`), no broken-corner example yet. Localizing *which* corner is broken remains deliberately deferred. **Correction 2026-10-10 — the fix above is weaker on real tiles than it looked.** It was verified on a perfect synthetic square, which flatters the detector: the `extent` metric depends on how well the minimum-area rectangle fits, and the same chip scores ~2.4x higher on a perfect square (leg 0.35 → 0.247) than on a real, slightly rotated tile (→ 0.101). Painting chips onto a real tile from the belt clip, the shipped thresholds (0.83 / 0.22) only flag a chip once its legs pass ~55% of the tile width (~5 in on a 9 in tile, ~15% of its area); a 4.5 in leg chip (12.5% of the area) ships as Grade A. Tightening is a trade, measured on 378 real intact photos with `development/corner_sensitivity.py`: 0.83/0.22 rejects 1.9% of good tiles and catches ≥5.0 in; 0.88/0.15 rejects 3.7% and catches ≥4.0 in; 0.92/0.10 rejects 5.6% and catches ≥2.7 in. The photos' own noise (p99 extent 0.154, max 0.264) sets the floor. **Which operating point to ship is an open business decision** (a chipped tile shipped vs. good tiles rejected), not changed here; re-run the tool on photos from the real rig, with real damaged tiles, before choosing.
- **Two footage-dependent settings silently produce zero tiles** (found 2026-10-09 running the dashboard on `data/videos/WhatsApp Video 2026-08-26 at 7.27.04 PM.mp4`, a 576x1024 *portrait* clip with the belt running top-to-bottom). (1) `trigger_line.orientation` must be perpendicular to the belt: the default `"vertical"` line at x=50% runs *parallel* to a top-to-bottom flow, so nothing ever crosses it — use `"horizontal"`. (2) The checkerboard-calibrated HSV range (`hsv_lower[1]` saturation floor 0) also matches a **brown wooden table**, which becomes a full-height blob and the largest region, so the tracker follows the table, not the tiles (centre never moves, count stays 0, state sticks on `in_view`). Measured tile S≈145-177 vs wood S≈87-126 → floor ≈135 separates them. Neither fails loudly. **Update 2026-10-09: the belt ROI now handles (2) with the shipped config — no saturation override needed; (1) is still rig-dependent and silent.** Both were overridden in a scratch launcher only; `config.yaml` is unchanged because the real rig's orientation and background are undecided. With both applied the dashboard counted tiles and graded them (22 over ~3 loops of a clip with ~7 real tiles), though the identity-flip bug above still makes counts untrustworthy with two tiles in frame. **FIXED 2026-10-11 — both halves.** (1) `trigger_line.orientation` is now `"auto"`: the line is made perpendicular to the belt (read from the belt ROI's principal axis) at `position: 0.5`, so footage of either shape works with no setting; "auto" with the belt ROI off is refused at start-up and an unreadable belt shows a dashboard error instead of counting zero. The old default `"vertical"` was parallel to the belt on every sample video, including the landscape webcam recordings (belt top-to-bottom there too). (2) the belt ROI handles the wood table. `live_dashboard.py` now has `--video`/`--device`/`--loop` as well.
- **Updated 2026-10-11 — the dashboard stream HAS now run against a real camera: the dev laptop's built-in webcam** (index 0, 640x480, mp4v). Verified end to end through the HTTP API: connect → live MJPEG served → record 9.2 s (284 frames, measured 31 fps) → stop → listed → replayed at 31 fps. **Still unverified: a USB camera other than the built-in one, the CSI/Pi path, and the Pi's local kiosk browser as a second MJPEG client.** Original note: the stream had only been smoke-tested via Flask's test client.

---

## Development Rules

1. **Hardware I/O stays separate from pure logic.** Any module touching real hardware (mic, camera, sensors, actuators) must isolate the hardware-facing code into a thin wrapper around pure, synthetic-input-testable logic — see `acoustic/capture.py`'s `TriggerDetector` (pure) vs `AudioCapture` (hardware wrapper), and `camera/segmentation.py`/`crack_detection.py`/`corner_detection.py`/`tile_tracker.py` (pure) vs `camera/capture.py`/`worker.py` (hardware wrapper), as the pattern to repeat for dimensional/control modules.
2. **No hardcoded device/sensor parameters.** Sample rate, device selection, thresholds, timing — all of it goes in a module-local `config.yaml`, never inline in source (extends the `CLAUDE-COMMON.md` hardware-address rule to audio params).
3. **Any threshold calibrated on prototype/substitute hardware (e.g. laptop mic instead of the measurement mic + real tapping mechanism) is provisional** and must be explicitly called out as such in code/docs until recalibrated on the real acoustic station (charter §6.2, §15.2, §18.1).
4. **New non-code artifacts get filed under the matching `documents/` subfolder in the same commit as the work that produced them** — see Documentation Architecture above. Don't leave CAD exports, schematics, or college submissions loose at the repo root or bundled into an unrelated folder.
5. **Every station's code lives in its own top-level `<station>_node/` folder.** Don't add new hardware-station Python packages back at the repo root the way the old `acoustic/` was — that convention was replaced 2026-08-03. *How* a node folder is shaped depends on what it runs on (amended 2026-09-20, `Automation_Architecture.md` §5.7):
   - **On an Arduino UNO Q** — shape it as an Arduino App Bricks project (`app.yaml` + `sketch/` + `python/`), with `acoustic_node/` as the reference. Applies to `acoustic_node/` and `pick_place_node/`.
   - **On anything else** — use that platform's normal packaging, and don't carry App Bricks scaffolding that describes hardware the node doesn't have. `camera_node/` (Raspberry Pi 5) is the reference: `python/` + a `deploy/` folder with a systemd unit and an install script; its `app.yaml` and `sketch/` were deleted rather than left as dead stubs.
6. **Keep the final report current (added 2026-10-12).** `documents/deliverables/review_2/Final_Report.md` is a living document (college report, template-shaped, with generated figures in `review_2/figures/`). When work changes what is true there — a feature built or a decision made/reversed, a new measurement or test, a bug or hardware surprise, a part bought, a paper read, or something "not yet run" that now has — update the report **in the same commit as the work**: the matching chapter, the figure (edit `figures/make_figures.py` and re-run it, never paste an image), the readiness matrix and status table, and add a `Logbook.md` row. Every number needs a source file or dated measurement; say what was *not* verified. Full table of triggers: report §0.1. The college forbids plagiarised/AI-generated report text, so the report is a draft the team must rewrite in its own words before submission.

---

## Project TODO List

Tracked in `TODO.md` (per `CLAUDE-COMMON.md` companion-file rule) rather than duplicated here, to avoid the two files drifting out of sync.

---

## User Rules

> Copy **Standard User Rules** from `CLAUDE-COMMON.md` verbatim here, then copy all sections from `PROJ_STARTER.md` below those. Add project-specific overrides at the bottom, clearly labelled.

See `CLAUDE-COMMON.md` → Standard User Rules and `PROJ_STARTER.md` for the full rule set.

### Project-Specific Overrides

_None — add below as needed._
