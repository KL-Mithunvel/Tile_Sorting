"""The App Lab Bridge transport to the gantry MCU (poll thread over g_cmd / g_poll).

On the UNO Q the Python side cannot open the MCU as a serial port: `Serial` on the MCU is the
router's monitor, and the container reaches the sketch through arduino-router (RouterBridge).
The firmware (`sketch/sketch.ino`) therefore exposes two Bridge methods:

    g_cmd(line)   Pi -> MCU, one protocol line (the sketch queues it and runs it from loop())
    g_poll()      MCU -> Pi, the queued reply lines, newline-separated ("" when there are none)

`BridgeGantryLink` gives `GantrySession` the same five things `SerialGantryLink` does
(`write_line()`, `read_lines()`, `connected`, `error`, `dropped_lines`) so nothing above it
changes. A reader thread calls `g_poll` every `poll_interval_s`; writes call `g_cmd` from the
caller's thread. One lock serialises every Bridge call, because the Bridge client is not
documented as thread-safe.

The firmware's link watchdog (`link_timeout_ms`, 500) is fed by the commands the runner sends,
not by this polling, so a poll thread that is slow is a latency problem, not a safety one -
but a dead poll thread means no replies, which the runner treats as a silent MCU and faults.

`arduino.app_utils.Bridge` is imported lazily and `bridge` can be injected, so this module
imports (and is tested) on a laptop. Checked against the real board with
`bench_tests/gantry_fw_test/`; the link itself has not run there.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

from pick_place.serial_link import LinkError

log = logging.getLogger(__name__)


class BridgeGantryLink:
    def __init__(self, poll_interval_s: float = 0.02, fail_limit: int = 5, bridge: Optional[object] = None):
        self._poll_interval_s = float(poll_interval_s)
        self._fail_limit = max(1, int(fail_limit))
        self._bridge = bridge
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._opened = False
        self._call_lock = threading.Lock()
        self._lines: "queue.Queue[str]" = queue.Queue(maxsize=5000)
        self._stop = threading.Event()
        self.error: Optional[str] = None
        self.dropped_lines = 0
        self._fails = 0

    # ---- lifecycle --------------------------------------------------------

    def open(self) -> "BridgeGantryLink":
        if self._bridge is None:
            try:
                from arduino.app_utils import Bridge  # noqa: PLC0415 - only exists in the App Lab container
            except ImportError as exc:
                raise LinkError("arduino.app_utils is not available (run inside the App Lab container)") from exc
            self._bridge = Bridge
        self.error = None
        self._fails = 0
        self._stop.clear()
        self._running = True
        self._opened = True
        self._thread = threading.Thread(target=self._poll_loop, name="gantry-bridge", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._running = False
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._opened = False
        self._thread = None

    @property
    def connected(self) -> bool:
        return self._opened and self.error is None

    # ---- I/O --------------------------------------------------------------

    def write_line(self, line: str) -> None:
        if not self.connected:
            raise LinkError(self.error or "gantry link is not open")
        text = line.strip()
        try:
            with self._call_lock:
                self._bridge.call("g_cmd", text)
        except Exception as exc:  # noqa: BLE001 - timeout, router gone, method missing
            self.error = f"write failed: {exc}"
            raise LinkError(self.error) from exc

    def read_lines(self) -> list:
        out = []
        while True:
            try:
                out.append(self._lines.get_nowait())
            except queue.Empty:
                return out

    def _poll_once(self) -> None:
        with self._call_lock:
            reply = self._bridge.call("g_poll")
        self._fails = 0
        if not reply:
            return
        for raw in str(reply).splitlines():
            text = raw.strip()
            if not text:
                continue
            try:
                self._lines.put_nowait(text)
            except queue.Full:
                self.dropped_lines += 1

    def _poll_loop(self) -> None:
        while self._running:
            try:
                self._poll_once()
            except Exception as exc:  # noqa: BLE001
                self._fails += 1
                log.warning("gantry bridge poll failed (%d/%d): %s", self._fails, self._fail_limit, exc)
                if self._fails >= self._fail_limit:
                    self.error = f"poll failed {self._fails} times: {exc}"
                    log.error("gantry bridge link lost: %s", self.error)
                    return
            if self._stop.wait(self._poll_interval_s):
                return
