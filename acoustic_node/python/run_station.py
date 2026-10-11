"""Acoustic station service entry point for the Raspberry Pi (systemd).

A thin shim over `acoustic.live_station.main()`, exactly like
camera_node/python/main.py, so the service and the interactive CLI cannot
drift apart. (`main.py` in this folder is the older Arduino App Lab stub for
the UNO Q path and is left alone.)
"""

from __future__ import annotations

import sys
from pathlib import Path

# systemd starts this by absolute path; `acoustic` and `fusion` live beside it.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from acoustic.live_station import main  # noqa: E402

if __name__ == "__main__":
    main()
