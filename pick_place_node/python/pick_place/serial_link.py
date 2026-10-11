"""The real serial transport to the gantry MCU (pyserial, reader thread).

Thin hardware wrapper, same role as camera/capture.py: all the logic lives in
`GantrySession`, which only needs `write_line()`, `read_lines()` and `connected`.
The port / baud come from config.yaml (`gantry.serial`); `serial_factory` lets a
test inject a fake port. `pyserial` is imported lazily so this module (and the
whole node) still imports on a machine without it.

Never run against the real board in this repo yet.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)


class LinkError(RuntimeError):
    """The gantry link is not usable (cannot open, lost, or never opened)."""


class SerialGantryLink:
    def __init__(self, port: str, baud: int = 115200,
                 serial_factory: Optional[Callable[..., object]] = None):
        self._port, self._baud = port, baud
        self._factory = serial_factory
        self._ser = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lines: "queue.Queue[str]" = queue.Queue(maxsize=5000)
        self._write_lock = threading.Lock()
        self.error: Optional[str] = None
        self.dropped_lines = 0

    # ---- lifecycle --------------------------------------------------------

    def open(self) -> "SerialGantryLink":
        factory = self._factory
        if factory is None:
            try:
                import serial  # noqa: PLC0415 - lazy: pyserial is not needed by the twin
            except ImportError as exc:
                raise LinkError("pyserial is not installed (pip install pyserial)") from exc
            factory = serial.Serial
        try:
            self._ser = factory(self._port, self._baud, timeout=0.1)
        except Exception as exc:  # noqa: BLE001 - serial.SerialException and OSError both land here
            raise LinkError(f"cannot open {self._port} at {self._baud} baud: {exc}") from exc
        self.error = None
        self._running = True
        self._thread = threading.Thread(target=self._read_loop, name="gantry-serial", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def connected(self) -> bool:
        return self._ser is not None and self.error is None

    # ---- I/O --------------------------------------------------------------

    def write_line(self, line: str) -> None:
        if not self.connected:
            raise LinkError(self.error or "gantry link is not open")
        data = (line.strip() + "\n").encode("ascii", errors="replace")
        try:
            with self._write_lock:
                self._ser.write(data)
        except Exception as exc:  # noqa: BLE001
            self.error = f"write failed: {exc}"
            raise LinkError(self.error) from exc

    def read_lines(self) -> list:
        out = []
        while True:
            try:
                out.append(self._lines.get_nowait())
            except queue.Empty:
                return out

    def _read_loop(self) -> None:
        while self._running:
            try:
                raw = self._ser.readline()
            except Exception as exc:  # noqa: BLE001 - unplugged cable etc.
                self.error = f"read failed: {exc}"
                log.error("gantry serial link lost: %s", exc)
                return
            if not raw:
                continue
            text = raw.decode("ascii", errors="replace").strip()
            if not text:
                continue
            try:
                self._lines.put_nowait(text)
            except queue.Full:
                self.dropped_lines += 1
