"""Hardware wrapper: opens the serial link to the io_smoke_test sketch, reads
status lines on a background thread into a thread-safe SharedState, and sends
VAC ON/OFF commands. Real hardware I/O -- not unit-tested (same reasoning as
acoustic/capture.py's AudioCapture, camera/capture.py's WebcamCapture).

**Where this serial port actually is on the UNO Q is unverified.** The App
Bricks Python<->MCU bridge is itself unconfirmed on this board
(pick_place_control_protocol.md §9), and whether the App Lab Python container
this app runs in even has access to a serial device node for the sketch is
untested. config.yaml's serial.port is a placeholder -- if it doesn't open,
the fallback is running this file directly over SSH (tools/uno_q/ssh.bat)
instead of through arduino-app-cli, so you can `ls /dev/tty*` on the board
and try candidates by hand.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Optional

import serial
import yaml

from io_protocol import IOStatus, format_vac_command, parse_status_line


def load_config(path: Optional[str] = None) -> dict:
    config_path = Path(path) if path else Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


class SharedState:
    """Thread-safe latest-value store the dashboard reads from."""

    def __init__(self):
        self._lock = threading.Lock()
        self._status: Optional[IOStatus] = None
        self._connected = False
        self._last_update_monotonic: Optional[float] = None

    def update(self, status: IOStatus) -> None:
        with self._lock:
            self._status = status
            self._connected = True
            self._last_update_monotonic = time.monotonic()

    def mark_disconnected(self) -> None:
        with self._lock:
            self._connected = False

    def status(self) -> dict:
        with self._lock:
            status = self._status
            connected = self._connected
            last_update = self._last_update_monotonic

        stale = last_update is None or (time.monotonic() - last_update) > 1.0
        base = {"connected": connected, "stale": stale}
        if status is None:
            return base
        return {
            **base,
            "lim_x": status.lim_x,
            "lim_y": status.lim_y,
            "lim_z": status.lim_z,
            "estop": status.estop,
            "vac": status.vac,
        }


class SerialIOBackend:
    """Background-thread reader/writer for the io_smoke_test sketch's serial
    link. .start() opens the port and begins reading; .set_vacuum() sends a
    command; .stop() closes it. Mirrors camera/capture.py's WebcamCapture
    shape (open/read-loop/close), not gantry_backend.py's planned
    SerialGantryBackend, since this bench tool predates the real protocol."""

    def __init__(self, config: dict, state: SharedState):
        self._port = config["serial"]["port"]
        self._baud = config["serial"]["baud"]
        self._timeout_s = config["serial"].get("timeout_s", 1.0)
        self._state = state
        self._conn: Optional[serial.Serial] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()

    def start(self) -> None:
        self._conn = serial.Serial(self._port, self._baud, timeout=self._timeout_s)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._conn is not None:
            self._conn.close()

    def set_vacuum(self, on: bool) -> None:
        if self._conn is None:
            raise RuntimeError("serial connection not started")
        with self._write_lock:
            self._conn.write(format_vac_command(on).encode("ascii"))

    def _loop(self) -> None:
        while self._running:
            try:
                raw = self._conn.readline()
            except (OSError, serial.SerialException):
                self._state.mark_disconnected()
                time.sleep(0.5)
                continue

            if not raw:
                # readline() timed out with nothing -- not necessarily an
                # error, but if it keeps happening the status goes stale on
                # its own via SharedState's age check.
                continue

            line = raw.decode("ascii", errors="replace")
            status = parse_status_line(line)
            if status is not None:
                self._state.update(status)
