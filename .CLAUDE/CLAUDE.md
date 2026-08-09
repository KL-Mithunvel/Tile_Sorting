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
- Current build phase: Phase 1 (Laboratory Proof of Concept, charter §12.1) — acoustic capture and camera vision pipeline exist; dimensional, control, sorting, and database layers are not started.
- Entry point (acoustic module): `python -m acoustic.live_monitor`, run from `acoustic_node/python/` (see Architecture below — the module moved out of a repo-root `acoustic/` package into this App Bricks-shaped node folder on 2026-08-03).
- Entry point (camera module): `python -m camera.live_dashboard`, run from `camera_node/python/` — opens a WiFi-reachable Flask dashboard (added 2026-08-07, see Architecture below).
- Every UNO Q station (`camera_node/`, `acoustic_node/`, `pick_place_node/`) is organized as an **Arduino App Bricks** project (`app.yaml` + `sketch/` + `python/`), matching Arduino's own `app-bricks-examples` convention, so each can be opened in Arduino App Lab. `acoustic_node/` and `camera_node/` have real Python-side code; `pick_place_node/` and both nodes' `sketch/`/App Lab wiring are still scaffolding. Only one physical UNO Q board exists — see Deployment Notes.

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
```

There is no hardware/production mode yet — everything above runs on the dev laptop's built-in mic / webcam. No seed data or one-time setup beyond the venv.

---

## Architecture

Acoustic capture and the camera vision pipeline exist so far. Dimensional, control, decision/grading, sorting, and database layers from the full charter (`documents/project/project_charter.md` §5) are not started.

**Repo layout (since 2026-08-03):** every planned UNO Q station gets its own top-level
node folder, following the Arduino **App Bricks** convention (`app.yaml` + `sketch/` +
`python/` — see Arduino's `app-bricks-examples` repo) so each can be opened as an app in
Arduino App Lab:

| Folder | Status |
|---|---|
| `acoustic_node/` | Real code — `python/acoustic/` is the working, tested module (below), migrated unchanged from the old repo-root `acoustic/`. `sketch/` (laser/ToF trigger + ball-drop release) and `python/main.py`'s App Lab wiring are stubs, unverified on hardware. |
| `camera_node/` | Real code (added 2026-08-07) — `python/camera/` is a working, tested vision pipeline + WiFi dashboard (below). `sketch/` and `python/main.py`'s App Lab wiring are still stubs. |
| `pick_place_node/` | Scaffolding only — no gantry motion-control code exists yet. |

Conveyor stays on the Arduino Mega (not App-Lab-class, not part of this convention).

| File | Role |
|---|---|
| `acoustic_node/python/acoustic/config.yaml` | All tunable audio/trigger parameters (device, sample rate, RMS threshold, timing) |
| `acoustic_node/python/acoustic/signal_processing.py` | Pure functions: `compute_rms`, `compute_fft`, `dominant_frequency`. No I/O — synthetic-signal testable. |
| `acoustic_node/python/acoustic/capture.py` | `TriggerDetector` (pure trigger-state logic, testable without a mic) + `AudioCapture` (thin `sounddevice` wrapper that feeds real audio blocks into it) |
| `acoustic_node/python/acoustic/plotting.py` | `plot_waveform_and_spectrum()` — matplotlib waveform + FFT display |
| `acoustic_node/python/acoustic/live_monitor.py` | CLI entry point: `--list-devices`, `--calibrate`, or continuous monitor mode |
| `acoustic_node/python/main.py` | Arduino App Lab entry point (`arduino.app_utils.App.run()`) — unverified stub, not wired to the module above yet |
| `camera_node/python/camera/config.yaml` | All tunable vision parameters (device index, HSV segmentation range, Canny thresholds, crack/corner thresholds, dashboard host/port) — see camera_node/README.md for the full list |
| `camera_node/python/camera/segmentation.py` | `segment_tile()` — isolate the tile from the background via HSV threshold. No I/O — synthetic-image testable. |
| `camera_node/python/camera/crack_detection.py` | `detect_cracks()` — grayscale → blur → Canny → keep long/thin contours → measure length + severity. No I/O — synthetic-image testable. |
| `camera_node/python/camera/corner_detection.py` | `detect_broken_corner()` — contour area vs. its own bounding rect area → missing-corner-area measurement. No I/O — synthetic-contour testable. |
| `camera_node/python/camera/tile_tracker.py` | `TileTracker` — debounced presence/absence state machine, counts tiles crossing the frame. No I/O — synthetic-sequence testable. |
| `camera_node/python/camera/pipeline.py` | `process_tile()` — wires the three detectors above into one `TileRecord` + a first-pass rule-based grade |
| `camera_node/python/camera/capture.py` | `WebcamCapture` — thin OpenCV `VideoCapture` wrapper. Real hardware I/O, not unit-tested. |
| `camera_node/python/camera/worker.py` | `CameraWorker` + `SharedState` — background thread wiring capture → segmentation → tracker → pipeline, thread-safe latest-value store for the dashboard |
| `camera_node/python/camera/dashboard.py` + `templates/dashboard.html` | Flask app: `/video_feed` (MJPEG stream), `/api/status` (JSON), `/` (dashboard page). Serves on `0.0.0.0` so it's reachable over WiFi. |
| `camera_node/python/camera/live_dashboard.py` | CLI entry point, mirrors `acoustic_node`'s `live_monitor.py` |
| `pytest.ini` | `pythonpath = acoustic_node/python, camera_node/python`, so `tests/` can `import acoustic` / `import camera` unchanged |

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
   |               on the last-seen isolated tile region, grade_tile()
   v
SharedState  — thread-safe: latest annotated JPEG frame + tile_count + recent TileRecords
   |
   v
Flask dashboard  — /video_feed (MJPEG, every frame) and /api/status (JSON,
                     polled by the page every second), served on 0.0.0.0
```

Every frame gets segmented and re-encoded to JPEG for the live stream, but the
crack/corner pipeline only runs once per tile — on the frame where `TileTracker` confirms
the tile has departed, using the last region seen while it was present.

### Threading model (camera)

Unlike `sounddevice`'s callback-driven model, OpenCV's `VideoCapture` is read
synchronously, so `CameraWorker` spins its own background thread (`threading.Thread`,
daemon) that owns the whole per-frame pipeline. `SharedState` is the only thing shared
across threads (the worker thread writes, Flask's request-handling threads read), guarded
by a single `threading.Lock` — reads/writes are "latest value wins," not a queue, since
the dashboard only ever wants the most recent frame/status, not a backlog.

### Simulation vs real mode

Not applicable yet — the acoustic module runs on the dev laptop's built-in mic and the camera module runs on a dev laptop webcam, no embedded/hardware target involved for either. `TriggerDetector` and the `camera/` pure modules are hardware-independent by construction (see Development Rules), so once `pick_place_node/`'s real code is added, and once `acoustic_node/sketch/` (laser/ToF + ball-drop) and `camera_node/sketch/` (tile-presence trigger, if any) are built, the same dev-machine-first split should be repeated for each.

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

### `camera/segmentation.py`

- `segment_tile(frame_bgr, hsv_lower, hsv_upper, min_area_px, morph_kernel_size=5) -> TileRegion | None` — HSV color-range threshold + largest-contour selection. Returns `None` if nothing large enough matches. `TileRegion` carries the bbox, area, contour, mask, and cropped image. No I/O; covered by `tests/test_camera_segmentation.py` with synthetic colored rectangles.

### `camera/crack_detection.py`

- `detect_cracks(tile_bgr, canny_low, canny_high, min_crack_length_px, min_aspect_ratio, minor_severity_max_length_px, blur_kernel_size=5) -> CrackResult` — grayscale → Gaussian blur → Canny → keeps only contours that are both long enough and elongated enough (aspect ratio) to be a crack rather than texture noise or a compact defect. Measures length from the winning contour's `minAreaRect`, grades severity against a length threshold. Raises `ValueError` on an empty image. No I/O; covered by `tests/test_camera_crack_detection.py` with synthetic drawn lines.

### `camera/corner_detection.py`

- `detect_broken_corner(contour, min_fill_ratio) -> CornerResult` — ratio of the tile's actual contour area to its own `minAreaRect` area; a low ratio means a corner is missing. Returns the fill ratio and a missing-area measurement, not which corner. Raises `ValueError` on a degenerate (zero-area) contour. No I/O; covered by `tests/test_camera_corner_detection.py` with synthetic clipped-corner polygons.

### `camera/tile_tracker.py`

- `TileTracker(min_present_frames, min_absent_frames)` — pure debounced state machine. `.process_frame(tile_detected: bool) -> bool` returns `True` exactly once per tile, on the frame its presence is confirmed to have ended. `.tile_count` / `.confirmed_present` properties. No I/O; covered by `tests/test_camera_tile_tracker.py` with synthetic presence sequences.

### `camera/pipeline.py`

- `process_tile(seq, region, ...) -> TileRecord` — runs `detect_cracks()` + `detect_broken_corner()` on one `TileRegion` and grades it (`grade_tile()`: any broken corner or major crack → Reject, minor crack → Grade B, else Grade A — a first-pass rule, not the master's fused final grade). Pure glue, not separately unit-tested (exercised via the smoke test below and indirectly by the modules it calls).

### `camera/capture.py` / `camera/worker.py`

- `WebcamCapture(config)` — thin `cv2.VideoCapture` wrapper (`start()`/`read_frame()`/`stop()`, context-manager support). Real hardware I/O, not unit-tested.
- `CameraWorker(capture, config, state)` — background thread: reads a frame, segments it, feeds `TileTracker`, runs the pipeline on tile departure, and publishes an annotated JPEG + results into `SharedState`. `SharedState` is the thread-safe latest-value store the Flask app reads from. Real hardware I/O + threading, not unit-tested (mirrors `AudioCapture`).

### `camera/dashboard.py` + `camera/templates/dashboard.html`

- `create_app(state) -> Flask` — `/` (dashboard page), `/video_feed` (MJPEG multipart stream from `SharedState`), `/api/status` (JSON: `tile_count` + `recent_tiles`, each a serialized `TileRecord`). Runs on `host: 0.0.0.0` from config so it's reachable over WiFi. Smoke-tested via Flask's test client, not part of the synthetic-image unit test suite.

### `camera/live_dashboard.py`

CLI entry point. No flags — loads config, starts `CameraWorker`, runs the Flask app. Ctrl+C stops both.

---

## Data Files

Nothing is persisted yet for either module — acoustic captures and camera frames/results are shown live and discarded (deferred to later by explicit decision). `acoustic/config.yaml` and `camera/config.yaml` are the only data files, and both are git-tracked (configuration, not runtime output).

Planned, not yet built: a `data/` directory for saved WAV clips, tile photos, and extracted features once dataset collection starts (charter §22.1). Already excluded in `.gitignore` (`data/`, `*.wav`, and — added 2026-08-07 alongside the camera module — `*.jpg`/`*.jpeg`/`*.png`) so it's ready when that lands — audio/image datasets should never be committed to git.

---

## Platform Constraints

- Current dev target: Windows 11. `sounddevice`/PortAudio work cross-platform without code changes, but device names/indices in `acoustic/config.yaml`'s `device: null` (system default) are Windows-specific in practice right now — re-verify `--list-devices` output if this ever runs on Linux/Raspberry Pi.
- This machine enumerates several "Microphone Array" input devices (Realtek, 2ch/4ch variants, one reporting a 16000 Hz default sample rate). `device: null` picks whatever Windows considers the default; if `AudioCapture.start()` fails to open at `sample_rate: 44100`, pin an explicit device index from `--list-devices` in `config.yaml` rather than debugging the default.
- **Windows Microphone Enhancements** (AGC / noise suppression) must be manually disabled in Sound Control Panel → input device → Properties → Enhancements tab. This cannot be controlled from Python/PortAudio. Until confirmed disabled, RMS threshold and FFT shape are not trustworthy for calibration — fine for pipeline smoke-testing, not for real threshold tuning.
- No hardware dependencies yet (no GPIO/I2C/serial/solenoid/PLC in this repo). Those land with the acoustic tapping station, dimensional sensors, and control layer per the charter — each will need the dev-machine simulation split required by `CLAUDE-COMMON.md` when it's built.
- `camera/config.yaml`'s `camera.device_index: 0` is OpenCV's default-webcam index — same "re-verify per machine" caveat as the mic's `device: null`. The eventual target is a separate PC-style webcam connected to the UNO Q (per the 2026-08-07 session), not this dev laptop's built-in camera — device index/backend will need re-checking once that's wired up.

---

## Deployment Notes

A lab Arduino UNO Q board (aarch64 Debian, hostname `KLM`, reachable at `arduino@172.20.10.2` over a local hotspot) is now reachable for dev/bring-up work, ahead of the charter's Raspberry Pi/industrial-PC target (§12, §13). No project code runs there in production yet — this is prep infrastructure (SSH + VNC access). **Only this one physical board exists** — the three node folders (`camera_node/`, `acoustic_node/`, `pick_place_node/`) are a code-organization decision made ahead of hardware (`Automation_Architecture.md` §5.7), not confirmation of three physical boards. Which node (if any) actually runs on this board first is still open (`Automation_Architecture.md` §5.2/§5.3, `TODO.md`).

- **Connection tooling**: `tools/uno_q/` — batch scripts to push code (`push.bat`), open a terminal (`ssh.bat`), and start/stop a VNC desktop (`vnc-start.bat` / `vnc-stop.bat`). See `tools/uno_q/README.md` for copy-paste setup and usage. `push.bat` currently only pushes `acoustic_node/` (the only node with real code).
- **Auth**: both password (via `tools/uno_q/secrets.bat`, gitignored) and SSH-key (`~/.ssh/id_ed25519` on the dev machine, installed to the board's `authorized_keys`) work. The `.bat` scripts use password auth via `plink`/`pscp` to match this owner's existing tooling convention on other projects (`weather_daq`) and for fully non-interactive scripting.
- **VNC**: TigerVNC on the board binds `-localhost yes` (not exposed on the hotspot directly) — always reached through an SSH tunnel, never opened to the LAN. If `-geometry` ever seems ignored, it's a stale session lock (`tigervncserver -list` shows a `(stale)` PID) — kill and restart, which `vnc-start.bat` already does automatically.
- This is not yet a real dev-vs-target split in the `CLAUDE-COMMON.md` sense (no project code executes on the board yet) — fill in a proper table here once camera/acoustic node assignment is decided and code actually starts running on it.

---

## Known Technical Debt

- `acoustic_node/python/acoustic/config.yaml`'s `trigger.rms_threshold: 0.02` is a placeholder, not a calibrated value. It has not been tuned against this machine's actual noise floor (run `--calibrate` to do that) and is meaningless on any other mic. Do not treat it as a real setting.
- Windows Microphone Enhancements have not been confirmed disabled as of this session (see Platform Constraints). Any RMS/FFT numbers collected before that's done should be treated as pipeline smoke-test data only, not real signal characterization.
- `acoustic_node/python/acoustic/capture.py`'s `AudioCapture` (the real hardware path) has only been smoke-tested via `--list-devices`; `--calibrate` and full trigger-to-plot flow have not yet been run against a live mic in this session — needs a manual run to confirm end-to-end.
- `documents/requirements/requirements.md` was newly formalized (2026-07-10) from `documents/project/project_charter.md` and `Automation_Architecture.md` — it has not yet been reviewed section-by-section against the full charter for completeness, so treat it as a first pass, not an exhaustive spec.
- Tile size and weight range from SMTW's actual product line has never been obtained. Every dimensional figure in the docs so far (e.g. the ~300mm example JSON in `Automation_Architecture.md`) is illustrative, not a spec — don't size the conveyor, gantry, gripper, or ball-drop energy off of it.
- **App Bricks/App Lab is unverified** (added 2026-08-03): `acoustic_node/python/main.py`, `camera_node/python/main.py`, and `pick_place_node/python/main.py` all just call `arduino.app_utils.App.run()`, copied from the shape of Arduino's own `app-bricks-examples`. Nobody has run this against the lab UNO Q board yet, so it's unknown whether `App.run()` behaves as assumed, what `sketch/sketch.yaml`'s real schema is, or how the Python/sketch sides are meant to communicate. Treat every `sketch/` and `python/main.py` file in the three node folders as an unverified stub, not working code.
- The `acoustic_node/`/`camera_node/`/`pick_place_node/` three-folder split assumes three physical UNO Q boards eventually; only one exists today (see Deployment Notes) — don't infer hardware procurement from the repo structure.
- `.CLAUDE/CLAUDE.md` previously documented a `venv/` at the repo root; the actual local environment found during this session was `.venv/` (PyCharm default) with only `pip` installed — `requirements.txt` had not been installed into it. Re-verify which venv convention is actually in use before trusting either name blindly.
- **Every camera vision threshold is unvalidated** (added 2026-08-07): `camera_node/python/camera/config.yaml`'s HSV segmentation range, Canny edge thresholds, crack length/aspect-ratio thresholds, and corner fill-ratio threshold are all placeholder starting guesses for brown terracotta tiles. No real tile photos were available on this machine when this was built (the user has sample photos from a different camera than the one that will actually be used, but they weren't accessible here) — see `camera_node/README.md` Known Limitations. Do not trust any crack/corner detection result until these are re-tuned against real photos.
- Crack "type" reported by the camera pipeline is severity (minor/major) + measured length, not a defect taxonomy (hairline vs. structural, edge vs. center crack) — that needs real labeled photos to design against.
- Corner detection reports that + how much area is missing, not which corner — localization was deliberately deferred (see `corner_detection.py` docstring).
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
