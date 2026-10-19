"""Raises the striker when the camera sees a tile coming.

The coil overheated when it was held up all the time (owner, 2026-10-19), so the striker now RESTS DOWN
(cool) and is raised only when it is needed. The camera is upstream of the striker: each graded tile
(`tile/<id>/camera/result`, published once per live tile as it crosses the camera's line) means a tile is
on its way, so this sends the Nano `UP`. The Nano raises the striker, waits for the ToF to see the tile,
strikes 2 s later, and lowers itself again when nothing more is coming (request-only mode in nano_core.h).

If the camera misses a tile the Nano still raises the striker when the ToF sees one, so a missed detection
costs a late retract, not a missed strike. If the tile never reaches the sensor the Nano forgets the
request after `up_timeout_ms` and lowers itself.

`CameraArming` sends a request only; `AutoArm` (below) decides whether the striker is ARMED (STRIKE 1).
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import json

from fusion.transport import CAMERA_RESULT_FILTER, TOPIC_NANO_STATUS, TOPIC_STRIKE, TOPIC_STRIKER_CMD, Bus

log = logging.getLogger(__name__)


class CameraArming:
    def __init__(self, bus: Bus, enabled: bool = True, min_gap_s: float = 0.3,
                 clock: Callable[[], float] = time.time):
        self._bus, self.enabled, self._gap, self._clock = bus, bool(enabled), float(min_gap_s), clock
        self.requests = 0
        self.last_ts = None

    def attach(self) -> None:
        self._bus.subscribe(CAMERA_RESULT_FILTER, self._on_camera)

    def _on_camera(self, topic: str, payload: bytes) -> None:
        if not self.enabled:
            return
        now = self._clock()
        if self.last_ts is not None and now - self.last_ts < self._gap:
            return                      # one request per tile is plenty; two tiles within 0.3 s are one request
        self.last_ts = now
        self.requests += 1
        self._bus.publish(TOPIC_STRIKER_CMD, {"action": "up"}, qos=1)


class AutoArm:
    """The striker is armed (STRIKE 1) ONLY while there is a tile in the system, and disarmed otherwise.

    Owner, 2026-10-19: "only stay armed if there is a tile in the system" -- an armed striker raises its coil, and a
    coil that is on when nothing is coming is what overheated. So the Pi arms it when a tile appears and disarms it
    once the tile has been dealt with; the Nano lowers itself when it is not armed and has nothing to do.

    A tile is "in the system" while ANY of these holds (each is a fact the Pi already has):
      * the camera graded it and its tap has not happened yet   (the ledger's awaiting list)
      * the Nano holds a request for it (`up_wanted`)            (the camera asked for the striker up)
      * a tile is under the ToF (distance <= tile_mm)            (covers a tile the camera missed, and the tile
                                                                  that has just been struck and must be lifted off)
    (The Nano's own striking/cooldown state is deliberately NOT a reason: the quiet lowering to rest also passes
    through it, and counting it re-armed the striker after every rest -- found by the whole-life test.)
    Disarming waits `grace_s` of "nothing in the system" so a flickering sensor cannot make it flap. In MANUAL mode,
    with a dead link, or when `enabled` is false (the operator has the Armed button) it does nothing.

    The Nano reports `armed` back every heartbeat, so the decision is made on what the Nano is doing, not on what
    was last sent. `awaiting()` and the Nano status are all it needs; it never talks to the camera directly."""

    def __init__(self, bus: Bus, awaiting: Callable[[], int] = lambda: 0, enabled: bool = True,
                 tile_mm: int = 100, grace_s: float = 2.0, resend_s: float = 1.0,
                 stale_s: float = 3.0, clock: Callable[[], float] = time.time):
        self._bus, self.awaiting, self.enabled = bus, awaiting, bool(enabled)
        self._tile_mm, self._grace, self._resend, self._stale, self._clock = int(tile_mm), float(grace_s), float(resend_s), float(stale_s), clock
        self._nano = None
        self._nano_ts = None
        self._clear_since = None
        self._last_cmd = -1e9
        self.in_system = False
        self.reason = "no status from the Nano yet"
        self.commands = {"arm": 0, "disarm": 0}

    def attach(self) -> None:
        self._bus.subscribe(TOPIC_NANO_STATUS, self._on_status)

    def _on_status(self, topic: str, payload: bytes) -> None:
        try:
            d = json.loads(payload)
        except ValueError:
            return
        if isinstance(d, dict):
            self._nano, self._nano_ts = d, self._clock()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._clear_since = None

    def evaluate(self) -> tuple:
        """(in_system, why) from the latest facts."""
        n = self._nano or {}
        waiting = 0
        try:
            waiting = int(self.awaiting())
        except Exception:  # noqa: BLE001 - a broken probe must read as "unknown", handled as nothing waiting
            waiting = 0
        mm = n.get("tof1_mm")
        if waiting > 0:
            return True, f"{waiting} tile(s) graded by the camera and not yet struck"
        if n.get("up_wanted"):
            return True, "the camera asked for the striker up"
        if mm is not None and mm <= self._tile_mm:
            return True, f"a tile is under the sensor ({mm} mm)"
        return False, "no tile in the system"

    def tick(self) -> None:
        now = self._clock()
        n = self._nano
        if not self.enabled:
            self.reason = "auto-arm is off (the Armed button is in control)"
            return
        if n is None or self._nano_ts is None or now - self._nano_ts > self._stale or n.get("link") is False:
            self.reason = "no live status from the Nano"
            return
        if n.get("manual"):
            self.reason = "MANUAL mode (the operator is in control)"
            return
        self.in_system, self.reason = self.evaluate()
        armed = bool(n.get("armed"))
        if self.in_system:
            self._clear_since = None
            if not armed and now - self._last_cmd >= self._resend:
                self._send(True, now)
        else:
            if self._clear_since is None:
                self._clear_since = now
            elif now - self._clear_since >= self._grace and armed and now - self._last_cmd >= self._resend:
                self._send(False, now)

    def _send(self, arm: bool, now: float) -> None:
        self._last_cmd = now
        self.commands["arm" if arm else "disarm"] += 1
        self._bus.publish(TOPIC_STRIKE, {"enabled": arm}, qos=1)

    def status(self) -> dict:
        return {"enabled": self.enabled, "in_system": self.in_system, "reason": self.reason,
                "armed_commands": self.commands["arm"], "disarm_commands": self.commands["disarm"]}
