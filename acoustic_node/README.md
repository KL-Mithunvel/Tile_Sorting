# Acoustic Node

Code for the acoustic tap-test station (`project_charter.md` §6.2, `Automation_Architecture.md`
§5.3). Planned target hardware: an Arduino UNO Q, paired with a laser/ToF trigger sensor
and a ball-drop impactor.

Folder layout follows the Arduino App Bricks convention (see `app.yaml`), so this can
eventually be opened as an app in Arduino App Lab the same way as Arduino's own
`app-bricks-examples` (e.g. `core-and-foundational/02-led-matrix/03-led-matrix-animation-mcu`):

| Path | Role |
|---|---|
| `app.yaml` | App Lab metadata (name, description, icon) |
| `sketch/` | MCU-side code (laser/ToF trigger → ball-drop release). **Stub — not implemented**, see `sketch/sketch.ino`. |
| `python/` | Linux-side code. `python/acoustic/` is the working, tested signal-processing/capture package migrated from the repo root; `python/main.py` is the App Lab entry point. |

## What's real vs. stub here

- `python/acoustic/` is the same tested module described in the root `.CLAUDE/CLAUDE.md`
  (`TriggerDetector`, `AudioCapture`, FFT/RMS signal processing, CLI) — moved here
  unchanged, only its location changed. Covered by `tests/` at the repo root (see
  `pytest.ini` — `pythonpath = acoustic_node/python` keeps `from acoustic.capture import
  ...` working without editing the tests).
- `sketch/` and `python/main.py` are **new stubs, not yet run against real App Lab or
  hardware**. The laser/ToF trigger and ball-drop release logic they're meant to hold
  don't exist yet — the release mechanism, ball mass/drop height, and pin assignments are
  still open decisions (`TODO.md`, `documents/requirements/requirements.md` FR-21).

## Running it today (dev laptop, no App Lab, no board)

Same as before the move, just from inside this folder:

```bash
cd acoustic_node/python
python -m acoustic.live_monitor --list-devices
python -m acoustic.live_monitor --calibrate
python -m acoustic.live_monitor
```

Tests still run from the repo root as before: `python -m pytest tests/ -v`.

## Open item

Whether/how `python/main.py`'s App Lab entry point (`arduino.app_utils.App.run()`)
should actually drive `acoustic/capture.py` has not been figured out yet — this file is
currently just Arduino's own minimal example shape, not wired to anything. Needs a real
App Lab session on the board to work out.
