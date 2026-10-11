"""Serial bridge between the Arduino Nano (striker + belt) and the MQTT bus.

Reads the Nano's lines (acoustic/tap_protocol.py) and publishes:

- `station/tap`          one message per strike, with the tile-present flag;
- `station/nano/status`  retained heartbeat: link alive, belt %, ToF ok, counters.

It also writes to the Nano:

- PING every `ping_s` — the Nano's watchdog stops the BELT if these stop, which
  makes this process (and the Pi, cable and USB port under it) part of the
  belt's safety chain on purpose: a dead decision station must not leave tiles
  streaming past it unjudged;
- BELT / STRIKE when `station/belt` / `station/strike` arrive from the master
  or the operator.

The serial port is injectable (anything with readline() / write() / close()), so
the whole bridge is tested with a scripted fake; pyserial is imported lazily.
`handle_line()` and `tick()` are the pure-ish core: no threads, no clock but the
one passed in.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Optional

from acoustic.tap_protocol import encode_belt, encode_ping, encode_strike, parse_line
from fusion.transport import (
    TOPIC_ALARM,
    TOPIC_BELT,
    TOPIC_DIAG_PING,
    TOPIC_NANO_STATUS,
    TOPIC_STRIKE,
    TOPIC_TAP,
    Bus,
    diag_pong_topic,
)

log = logging.getLogger(__name__)



def open_pyserial(port: str, baud: int):
    import serial  # lazy: pyserial is only needed where a Nano is attached

    return serial.Serial(port, baudrate=baud, timeout=0.2)


class TapBridge:
    def __init__(
        self,
        serial_port,
        bus: Bus,
        clock: Callable[[], float] = time.time,
        ping_s: float = 0.5,
        status_s: float = 1.0,
        stale_s: float = 2.0,
    ):
        self._ser, self._bus, self._clock = serial_port, bus, clock
        self._ping_s, self._status_s, self._stale_s = ping_s, status_s, stale_s
        self._lock = threading.Lock()
        self._last_seq: Optional[int] = None
        self._last_rx: Optional[float] = None
        self._last_ping = 0.0
        self._last_status = 0.0
        self._ping_n = 0
        self._belt_pct: Optional[int] = None
        self._tof = {"tof1_ok": None, "tof3_ok": None}
        self._fw: Optional[str] = None
        self.counters = {"taps": 0, "lost_taps": 0, "bad_lines": 0, "duplicates": 0, "errors_from_nano": 0}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ---- the pure-ish core ------------------------------------------------

    def handle_line(self, raw, now: Optional[float] = None) -> Optional[dict]:
        """Act on one received line. Returns the parsed message (for tests)."""
        now = self._clock() if now is None else now
        msg = parse_line(raw)
        if msg is None:
            return None
        with self._lock:
            if msg["type"] == "bad":
                self.counters["bad_lines"] += 1
                log.warning("bad line from Nano (%s): %r", msg["reason"], msg["raw"])
                return msg
            self._last_rx = now
            kind = msg["type"]
            if kind == "hello":
                self._fw = msg["fw"]
                self._last_seq = None  # a rebooted Nano restarts its counter
            elif kind == "hb":
                self._belt_pct = msg["belt_pct"]
                self._tof = {"tof1_ok": msg["tof1_ok"], "tof3_ok": msg["tof3_ok"]}
            elif kind == "err":
                self.counters["errors_from_nano"] += 1
                self._alarm("nano_error", f"Nano reports {msg['code']}: {msg['text']}")
            elif kind == "tap":
                self._on_tap(msg, now)
        return msg

    def _on_tap(self, msg: dict, now: float) -> None:
        seq = msg["seq"]
        if self._last_seq is not None:
            if seq == self._last_seq:
                self.counters["duplicates"] += 1  # a retransmit; one strike, one event
                return
            lost = (seq - self._last_seq - 1) % 65536
            if lost:
                self.counters["lost_taps"] += lost
                self._alarm("tap_gap", f"{lost} strike event(s) from the Nano never arrived (seq {self._last_seq} -> {seq})")
        self._last_seq = seq
        self.counters["taps"] += 1
        self._bus.publish(
            TOPIC_TAP,
            {
                "seq": seq,
                "tile_present": msg["tile_present"],
                "tof_mm": msg["tof_mm"],
                "nano_ms": msg["uptime_ms"],
                "ts": now,
            },
            qos=1,
        )

    def tick(self, now: Optional[float] = None) -> None:
        """Keep-alive to the Nano and the status heartbeat."""
        now = self._clock() if now is None else now
        if now - self._last_ping >= self._ping_s:
            self._last_ping = now
            self._ping_n += 1
            self._write(encode_ping(self._ping_n))
        if now - self._last_status >= self._status_s:
            self._last_status = now
            with self._lock:
                link = self._last_rx is not None and now - self._last_rx <= self._stale_s
                body = {
                    "state": "online",
                    "link": link,
                    "fw": self._fw,
                    "belt_pct": self._belt_pct,
                    **self._tof,
                    **self.counters,
                    "ts": now,
                }
            self._bus.publish(TOPIC_NANO_STATUS, body, qos=0, retain=True)

    # ---- commands to the Nano --------------------------------------------

    def _on_belt(self, topic: str, payload: bytes) -> None:
        try:
            pct = float(json.loads(payload)["percent"])
        except (ValueError, KeyError, TypeError):
            self._alarm("bad_message", "unreadable station/belt command")
            return
        self._write(encode_belt(pct))

    def _on_strike(self, topic: str, payload: bytes) -> None:
        try:
            enabled = bool(json.loads(payload)["enabled"])
        except (ValueError, KeyError, TypeError):
            self._alarm("bad_message", "unreadable station/strike command")
            return
        self._write(encode_strike(enabled))

    def _on_diag_ping(self, topic: str, payload: bytes) -> None:
        """Answer for the Nano: `ok` only if the serial link is really alive
        (a valid line from the Nano within stale_s), not merely if this bridge
        process is."""
        try:
            probe = json.loads(payload) if payload else {}
        except ValueError:
            probe = {}
        now = self._clock()
        with self._lock:
            link = self._last_rx is not None and now - self._last_rx <= self._stale_s
            detail = {"link": link, "fw": self._fw, "belt_pct": self._belt_pct, **self._tof, **self.counters}
        self._bus.publish(
            diag_pong_topic("nano"),
            {"id": probe.get("id") if isinstance(probe, dict) else None, "node": "nano", "ts": now, "ok": link,
             "detail": detail},
            qos=0,
        )

    def _write(self, data: bytes) -> None:
        try:
            self._ser.write(data)
        except Exception as exc:  # noqa: BLE001 - a dead port shows up as link=false
            log.error("serial write failed: %s", exc)

    def _alarm(self, kind: str, message: str) -> None:
        self._bus.publish(
            TOPIC_ALARM, {"ts": self._clock(), "level": "alarm", "kind": kind, "message": message, "tile_id": None}, qos=1
        )

    # ---- thread -----------------------------------------------------------

    def start(self) -> None:
        self._bus.subscribe(TOPIC_BELT, self._on_belt)
        self._bus.subscribe(TOPIC_STRIKE, self._on_strike)
        self._bus.subscribe(TOPIC_DIAG_PING, self._on_diag_ping)
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="nano-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        # Deliberately do NOT send BELT 0: silence is the signal. Stopping the
        # pings is what trips the Nano's own watchdog, which is the path that
        # must work when this process has crashed instead of stopped.
        try:
            self._bus.publish(TOPIC_NANO_STATUS, {"state": "offline", "link": False}, qos=1, retain=True)
            self._ser.close()
        except Exception:  # noqa: BLE001
            pass

    def _loop(self) -> None:
        while self._running:
            try:
                line = self._ser.readline()
                if line:
                    self.handle_line(line)
                self.tick()
            except Exception:  # noqa: BLE001
                log.exception("Nano bridge loop error")
                time.sleep(0.2)
