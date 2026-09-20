"""Camera node service entry point — Raspberry Pi 5 station.

This used to be an Arduino App Lab stub (`from arduino.app_utils import App;
App.run()`). It isn't any more: as of 2026-09-20 the camera station runs on a
Raspberry Pi 5 (16 GB) under Raspberry Pi OS Desktop 64-bit, not an Arduino
UNO Q, so there is no App Lab, no MCU sketch, and no bridge to wire up here —
see `documents/project/Automation_Architecture.md` §5.2 for that decision and
§5.7 for why `camera_node/` left the App Bricks convention.

This is what systemd starts on the Pi (`camera_node/deploy/tile-camera.service`).
It is deliberately a thin shim over `camera.live_dashboard.main()` rather than
a second copy of the wiring, so the service and the interactive CLI can never
drift apart:

    systemd  -> main.py            -> camera.live_dashboard.main()
    operator -> python -m camera.live_dashboard  (same function, same flags)

Any argument the CLI accepts works here too, which is how the service file
passes `--backend`/`--no-dashboard` when it needs to.
"""

from __future__ import annotations

import sys
from pathlib import Path

# systemd starts this by absolute path, so the `camera` package's parent
# directory isn't necessarily on sys.path the way it is under `python -m`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from camera.live_dashboard import main  # noqa: E402  (must follow the sys.path fix)

if __name__ == "__main__":
    main()
