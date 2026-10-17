"""Hardware wrapper: talks to the io_smoke_test sketch through the App Lab
Bridge (arduino-router), polling its `io_status` function on a background
thread into the shared SharedState and calling `vac` to switch the vacuum.

Replaces serial_backend.SerialIOBackend for the on-board app. A raw serial
device does not exist for the MCU in the App Lab container (observed
2026-10-16: /dev/ttyACM0 missing; the MCU link /dev/ttyHS1 belongs to the
arduino-router service). Linux reaches the sketch only through the router, via
`Bridge.call(name, ...)` here and `Bridge.provide(name, fn)` in the sketch.

Same interface as SerialIOBackend (start / stop / set_vacuum) so dashboard.py
is unchanged. Real hardware I/O -- not unit-tested.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from io_protocol import parse_status_line
from serial_backend import SharedState


class BridgeIOBackend:
    def __init__(self, config: dict, state: SharedState):
        bridge_cfg = config["bridge"]
        self._poll_s = bridge_cfg["poll_interval_s"]
        self._timeout_s = bridge_cfg["call_timeout_s"]
        self._state = state
        self._bridge = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        # Imported here so this module still imports on a dev laptop, where
        # arduino.app_utils does not exist.
        from arduino.app_utils import Bridge

        self._bridge = Bridge
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def set_vacuum(self, on: bool) -> None:
        if self._bridge is None:
            raise RuntimeError("bridge backend not started")
        self._bridge.call("vac", 1 if on else 0, timeout=self._timeout_s)

    def _loop(self) -> None:
        while self._running:
            try:
                line = self._bridge.call("io_status", timeout=self._timeout_s)
            except Exception as exc:  # router down, sketch not up yet, timeout
                print(f"bridge io_status failed: {exc}", flush=True)
                self._state.mark_disconnected()
                time.sleep(1.0)
                continue

            status = parse_status_line(str(line))
            if status is not None:
                self._state.update(status)
            time.sleep(self._poll_s)
