# Acoustic Node

Code for the acoustic tap-test station (`project_charter.md` §6.2, `Automation_Architecture.md`
§5.3). Target hardware: an Arduino UNO Q, paired with a ToF trigger sensor and a
dual-solenoid (ARM + LOCK) ball-drop impactor — see
`documents/electrical/schematics/acoustic_station_wiring.md` for the full pin table, BOM,
and wiring diagram (decided 2026-08-20).

Folder layout follows the Arduino App Bricks convention (see `app.yaml`), so this can
eventually be opened as an app in Arduino App Lab the same way as Arduino's own
`app-bricks-examples` (e.g. `core-and-foundational/02-led-matrix/03-led-matrix-animation-mcu`):

| Path | Role |
|---|---|
| `app.yaml` | App Lab metadata (name, description, icon) |
| `sketch/` | MCU-side code — ToF-triggered dual-solenoid (ARM+LOCK) tap sequencer. Real state-machine logic now (see `sketch/sketch.ino`), but **unverified against real App Lab/hardware** — see What's real vs. stub below. |
| `python/` | Linux-side code. `python/acoustic/` is the working, tested signal-processing/capture package, now including hardware-tap support (`hardware_trigger.py`, `tap_sequencer.py`); `python/main.py` is the App Lab entry point. |

## What's real vs. stub here

- `python/acoustic/` is the tested module described in the root `.CLAUDE/CLAUDE.md`
  (`TriggerDetector`/`HardwareTapDetector`, `AudioCapture`, `TapSequencer`, FFT/RMS signal
  processing, CLI). Covered by `tests/` at the repo root (see `pytest.ini` —
  `pythonpath = acoustic_node/python` keeps `from acoustic.capture import ...` working
  without editing the tests).
- `sketch/sketch.ino` now has real, structured tap-sequencer logic (ToF read → SETTLE →
  ARM → ARM_SETTLE → RELEASE → COOLDOWN, e-stop handling) instead of an empty stub — but
  it has **never been compiled or run against real App Lab or hardware**. Two specific
  placeholders inside it are called out as such: the ToF library call
  (`readTofDistanceMm()`) and the bridge-variable syntax for exposing `tapCount` to
  Python. Pin numbers and timing constants are documented in
  `documents/electrical/schematics/acoustic_station_wiring.md`; ball mass/drop height and
  the reload/reset mechanism between tiles are still open (`TODO.md`,
  `documents/requirements/requirements.md` FR-21).
- `python/main.py` has a real `poll_bridge_and_capture()` function shaped to drive
  `AudioCapture.notify_tap()` from the sketch's `tapCount`, but it is deliberately **not**
  wired into `App.run()` yet — `read_tap_count()` is a placeholder until App Bricks'
  actual bridge-read API is confirmed on hardware.

## Running it today (dev laptop, no App Lab, no board)

Same as before the move, just from inside this folder:

```bash
cd acoustic_node/python
python -m acoustic.live_monitor --list-devices
python -m acoustic.live_monitor --calibrate
python -m acoustic.live_monitor
```

`config.yaml`'s `trigger.mode` controls what triggers a capture: `"rms"` (default,
mic-only, no hardware needed) or `"simulated"` (fakes the whole ToF→arm→release sequence
on a timer via `acoustic.tap_sequencer.TapSequencer`, no hardware needed either — good
for exercising the hardware-trigger code path before the real station exists). `"hardware"`
requires the real ToF + dual-solenoid station and `main.py`'s bridge wiring.

Tests still run from the repo root as before: `python -m pytest tests/ -v`.

## Open item

Whether/how `python/main.py`'s App Lab entry point (`arduino.app_utils.App.run()`)
should actually drive `acoustic/capture.py` has not been figured out yet — `main.py` now
has a `poll_bridge_and_capture()` function shaped for this, but it isn't called from
`App.run()` and `read_tap_count()` is a placeholder. Needs a real App Lab session on the
board to work out.
