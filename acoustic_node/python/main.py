"""Acoustic + decision station service entry point for the Raspberry Pi (systemd).

A thin shim over `acoustic.live_station.main()`, exactly like
camera_node/python/main.py, so the service and the interactive CLI
(`python -m acoustic.live_station`) cannot drift apart.

This file used to be the Arduino App Lab stub for the UNO Q (`App.run()` plus an
unverified `poll_bridge_and_capture()` that read the sketch's `tapCount` bridge
variable). The acoustic station moved to the shared Pi in the 2026-10-10
proposal, where the Nano's strike events arrive over serial instead
(`acoustic/tap_bridge.py`), so that polling code was removed; it is in git
history if the UNO Q path is ever revived. `app.yaml` and `sketch/` are left as
they were.
"""

from __future__ import annotations

import sys
from pathlib import Path

# systemd starts this by absolute path; `acoustic` and `fusion` live beside it.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from acoustic.live_station import main  # noqa: E402

if __name__ == "__main__":
    main()
