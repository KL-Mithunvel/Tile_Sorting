"""Shared state + hardware wrapper for the encoder_smoke_test bench app.

BridgeEncBackend polls the sketch's `enc_status` over the App Lab Bridge on a
background thread into SharedState, and forwards move/stop/scan/reset/zero
requests. Same shape as io_smoke_test/bridge_backend.py (the MCU is reachable
only through arduino-router, not a serial device). Real hardware I/O -- not
unit-tested; the Bridge path is written from Arduino's example and has not
been confirmed for this app.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

import yaml

from enc_protocol import EncStatus, ST_DONE, build_result, parse_status_line


def load_config(path: Optional[str] = None) -> dict:
    config_path = Path(path) if path else Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


class SharedState:
    """Thread-safe latest-value store the dashboard reads from, plus the
    history of completed-move results."""

    def __init__(self, config: dict, history_len: int = 30):
        self._lock = threading.Lock()
        self._status: Optional[EncStatus] = None
        self._connected = False
        self._last_update: Optional[float] = None
        self._last_seq: Optional[int] = None
        self._history: deque = deque(maxlen=history_len)
        enc = config["encoder"]
        self._axes = config["axes"]
        self._names = enc["channel_names"]
        self._expected = float(enc["deg_per_motor_rev"])
        self._tol = float(enc["tolerance_deg"])

    def update(self, status: EncStatus) -> None:
        with self._lock:
            self._status = status
            self._connected = True
            self._last_update = time.monotonic()
            # First status after (re)start only sets the baseline: a move that
            # finished before this app started is not a new result.
            if self._last_seq is None:
                self._last_seq = status.seq
            elif status.seq != self._last_seq:
                self._last_seq = status.seq
                if status.state == ST_DONE:
                    result = build_result(status, self._axes, self._names, self._expected, self._tol)
                    if result is not None:
                        self._history.appendleft(result)

    def mark_disconnected(self) -> None:
        with self._lock:
            self._connected = False

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()

    def snapshot(self) -> dict:
        with self._lock:
            status, connected, last = self._status, self._connected, self._last_update
            history = list(self._history)
        stale = last is None or (time.monotonic() - last) > 1.5
        out = {"connected": connected, "stale": stale, "history": history}
        if status is not None:
            out["status"] = {
                "state": status.state, "axis": status.axis, "seq": status.seq,
                "mux_ok": status.mux_ok, "drivers_on": status.drivers_on,
                "raw": status.raw, "pos_deg": status.pos_deg, "mag": status.mag,
                "errors": status.errors, "move_fails": status.move_fails,
                "scan": status.scan,
            }
        return out


class BridgeEncBackend:
    def __init__(self, config: dict, state: SharedState):
        bridge_cfg = config["bridge"]
        self._poll_s = bridge_cfg["poll_interval_s"]
        self._timeout_s = bridge_cfg["call_timeout_s"]
        self._num_axes = len(config["axes"])
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

    def _call(self, name: str, *args) -> None:
        if self._bridge is None:
            raise RuntimeError("bridge backend not started")
        self._bridge.call(name, *args, timeout=self._timeout_s)

    def move(self, axis: int) -> None:
        if not 0 <= axis < self._num_axes:
            raise ValueError(f"axis must be 0..{self._num_axes - 1}")
        self._call("enc_move", axis)

    def jog(self, axis: int, steps: int) -> None:
        """Manual jog: `steps` is signed (negative = the other direction). One int goes over the
        Bridge: ((axis*2 + dir) << 20) | |steps|."""
        if not 0 <= axis < self._num_axes:
            raise ValueError(f"axis must be 0..{self._num_axes - 1}")
        n = abs(int(steps))
        if not 1 <= n <= 0xFFFFF:
            raise ValueError("steps must be between 1 and 1048575 (either sign)")
        self._call("enc_jog", ((axis * 2 + (1 if steps > 0 else 0)) << 20) | n)

    def halt(self) -> None:
        self._call("enc_stop")

    def scan(self) -> None:
        self._call("enc_scan")

    def mux_reset(self) -> None:
        self._call("enc_mux_reset")

    def zero(self) -> None:
        self._call("enc_zero")

    def _loop(self) -> None:
        while self._running:
            try:
                line = self._bridge.call("enc_status", timeout=self._timeout_s)
            except Exception as exc:  # router down, sketch not up yet, timeout
                print(f"bridge enc_status failed: {exc}", flush=True)
                self._state.mark_disconnected()
                time.sleep(1.0)
                continue

            status = parse_status_line(str(line))
            if status is not None:
                self._state.update(status)
            time.sleep(self._poll_s)
