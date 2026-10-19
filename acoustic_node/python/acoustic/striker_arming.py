"""Raises the striker when the camera sees a tile coming.

The coil overheated when it was held up all the time (owner, 2026-10-19), so the striker now RESTS DOWN
(cool) and is raised only when it is needed. The camera is upstream of the striker: each graded tile
(`tile/<id>/camera/result`, published once per live tile as it crosses the camera's line) means a tile is
on its way, so this sends the Nano `UP`. The Nano raises the striker, waits for the ToF to see the tile,
strikes 2 s later, and lowers itself again when nothing more is coming (request-only mode in nano_core.h).

If the camera misses a tile the Nano still raises the striker when the ToF sees one, so a missed detection
costs a late retract, not a missed strike. If the tile never reaches the sensor the Nano forgets the
request after `up_timeout_ms` and lowers itself.

This sends a request only. It does not arm: STRIKE 1 (the Armed button) is still the operator's switch.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from fusion.transport import CAMERA_RESULT_FILTER, TOPIC_STRIKER_CMD, Bus

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
