# Software architecture — module boundaries and threading

Status (2026-10-12): an **overview of what is built**, derived from the code and
`.CLAUDE/CLAUDE.md` (which has the per-module detail). Nothing here has run on the Pi.

## 1. Layout

| Folder | Runs on | Entry point | Role |
|---|---|---|---|
| `camera_node/python/camera/` | Pi 5 (`tile-camera`) | `main.py` / `python -m camera.live_dashboard` | segmentation, tile tracking, crack / corner checks, optional ONNX grade model, dashboard :5000, MQTT publisher |
| `acoustic_node/python/acoustic/` + `fusion/` | Pi 5 (`tile-acoustic`) | `main.py` / `python -m acoustic.live_station` | microphone capture, tap features, decision (`fusion/`), operator GUI :5001, Nano bridge |
| `acoustic_node/nano_firmware/` | Arduino Nano | `nano_firmware.ino` | striker sequence, belt PWM, watchdog; logic in `nano_core.h` |
| `pick_place_node/python/pick_place/` | UNO Q (Linux) | `gantry_station` / `twin_prototype` | tile feed queue, job runner, sequencer, coordinate model, HMI twin |
| `pick_place_node/sketch/` | UNO Q MCU | `sketch.ino` | step generation, homing, limits, e-stop; logic in `gantry_core.h` |
| `master_node/python/master/` | laptop / PC | `python -m master.live_master` | optional observer, collector, commander :8000 |
| `Acoustic-Analysis/` | laptop (submodule) | its own | acoustic data collection, analysis, labelling |
| `camera_models/`, `development/` | laptop | scripts | model training / benchmarks, calibration and labelling tools; **never imported by a node** |

## 2. The pattern repeated everywhere

**Pure logic, thin hardware wrapper.** Each hardware-facing module is split into a pure
core (synthetic-input testable, no I/O, injectable clock) and a small wrapper that owns
the device or thread:

| Pure core | Wrapper |
|---|---|
| `camera/segmentation.py`, `crack_detection.py`, `corner_detection.py`, `tile_tracker.py`, `line_trigger.py` | `camera/capture.py`, `worker.py` |
| `acoustic/signal_processing.py`, `features.py`, `TriggerDetector`, `tap_sequencer.py` | `acoustic/capture.py` (`AudioCapture`), `station.py` |
| `fusion/ledger.py`, `final_grade.py`, `clock_skew.py`, `stats.py` | `fusion/coordinator.py`, `transport.py` (`MqttBus`) |
| `acoustic/tap_protocol.py` | `acoustic/tap_bridge.py` (serial) |
| `nano_core.h`, `gantry_core.h` (behind a `Hal`) | `nano_firmware.ino`, `sketch.ino` |
| `pick_place/protocol.py`, `coordinate_model.py`, `sequencer.py`, `mock_state.py` | `gantry_session.py`, `serial_link.py` |

Firmware cores are compiled on the laptop and driven by the same Python protocol modules
the Pi uses (`tests/test_*_firmware_conformance.py`), so the two ends of each link are
checked against each other.

## 3. Interfaces between nodes

Only three things cross a node boundary, all documented elsewhere:

1. **MQTT JSON** — topics and payloads in `documents/programming/station_coordination.md`.
   `tests/test_station_contract.py` keeps the camera and `fusion/` sides in step; the two
   packages never import each other.
2. **Nano serial lines** — `documents/programming/nano_serial_protocol.md`.
3. **Gantry line protocol** — `documents/programming/pick_place_control_protocol.md`.

Configuration is per module (`config.yaml` next to the code); no hardware parameter is
hard-coded in source.

## 4. Threading

| Process | Threads | Shared state |
|---|---|---|
| `tile-camera` | capture/worker thread (owns the whole per-frame pipeline); Flask request threads; MQTT publisher heartbeat thread | `SharedState`, one lock, latest-value store |
| `tile-acoustic` | PortAudio callback thread; analysis thread; tick thread; Flask threads; Nano bridge thread; MQTT network thread | `StationCoordinator` with one re-entrant lock; clips cross threads on a `queue.Queue` |
| `master` | collector (MQTT) thread; Flask threads | its own SQLite |
| UNO Q gantry | HMI/Flask, feed client, runner tick | `GantrySession` shared by HMI and runner so no reply is read by the wrong consumer |
| firmware (both) | `loop()` + (gantry) a timer ISR for step generation | `volatile` fields, short `isrLock` sections |

Rules: an audio callback never waits on the camera; one process crash never restarts the
other (separate systemd units); every worker loop catches and counts its errors instead of
dying silently.

## 5. Tests

`python -m pytest tests/ -q` from the repo root (`pytest.ini` puts every node's `python/`
on `sys.path`). On this machine set `OPENBLAS_NUM_THREADS=1` if collection dies silently.
Firmware conformance tests need a C++11 compiler (`NANO_HOST_CXX` / `GANTRY_HOST_CXX`, or
`pip install --target <dir> ziglang` and `python -m ziglang c++`); they skip without one.

## 6. Not built

Dimensional inspection module, conveyor / sorting execution beyond the gantry feed, a
database layer beyond the station's per-tile SQLite, encoder-based tile tracking. See
`TODO.md`.
