"""No-hardware stand-ins for the station: a synthetic microphone and a fake
camera, so the GUI and the whole fusion path can be run and looked at on any
laptop (`python -m acoustic.live_station --demo`).

Nothing here is used in production.
"""

from __future__ import annotations

import queue
import random
import threading
import time

import numpy as np

from fusion.transport import Bus


def synthetic_strike(sample_rate: int = 44100, freq: float = 2800.0, decay_s: float = 0.05, seed=None) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(int(0.5 * sample_rate)) / sample_rate
    x = rng.normal(0, 0.001, t.size)
    k = t >= 0.1
    x[k] += 0.5 * np.exp(-(t[k] - 0.1) / decay_s) * np.sin(2 * np.pi * freq * (t[k] - 0.1))
    return x.astype(np.float32)


class SyntheticCapture:
    """Looks like acoustic.capture.AudioCapture. A tap notification produces a
    synthetic strike clip; `last_rms` / `last_block_monotonic` look alive."""

    def __init__(self, config: dict | None = None):
        self.sample_rate = int(((config or {}).get("audio") or {}).get("sample_rate", 44100))
        self.trigger_mode = "simulated"
        self._q: "queue.Queue[np.ndarray]" = queue.Queue()
        self._started = False

    @property
    def last_block_monotonic(self):
        return time.monotonic() if self._started else None

    @property
    def last_rms(self) -> float:
        return 0.0008 + 0.0004 * random.random() if self._started else 0.0

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def notify_tap(self) -> None:
        freq = random.choice([2800.0, 2800.0, 2800.0, 1700.0])  # now and then a "dull" one
        self._q.put(synthetic_strike(self.sample_rate, freq=freq))

    def get_clip(self, timeout=None):
        return self._q.get(timeout=timeout)


class FakeCameraAndStriker(threading.Thread):
    """Publishes a camera heartbeat and a graded tile every `interval_s`, then
    "taps" it `transit_s` later by publishing station/tap — standing in for the
    camera service and the Nano's strike event. Also answers every final with
    an ack and a `placed` report, and keeps a gantry status alive, standing in
    for the UNO Q."""

    def __init__(
        self, bus: Bus, interval_s: float = 4.0, transit_s: float = 2.0, ack: bool = True, tap: bool = True
    ):
        super().__init__(daemon=True, name="demo-camera")
        self._bus, self._interval, self._transit, self._ack = bus, interval_s, transit_s, ack
        self._tap = tap  # False: a real microphone/striker makes the taps
        self._halt = threading.Event()
        self._n = 0
        self._placed = 0

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        if self._ack:
            self._bus.subscribe("tile/+/final", self._answer)
        # Stand in for the camera service in the diagnostic too.
        self._bus.subscribe("station/diag/ping", self._answer_ping)
        last_tile = 0.0
        pending: list[float] = []
        tile_count = 0
        while not self._halt.is_set():
            now = time.monotonic()
            self._bus.publish(
                "station/nano/status",
                {"state": "online", "link": True, "belt_pct": 60, "ts": time.time()},
                qos=0,
            )
            if self._ack:
                self._bus.publish(
                    "station/gantry/status",
                    {"state": "online", "level": "ok", "phase": "idle", "homed": True, "paused": False,
                     "queue_depth": 0, "completed": self._placed, "failed": 0, "backend": "demo",
                     "ts": time.time()},
                    qos=0,
                )
            self._bus.publish(
                "station/camera/status",
                {"source_kind": "camera", "source_status": "running", "tile_count": tile_count, "ts": time.time()},
                qos=0,
            )
            if now - last_tile >= self._interval:
                last_tile = now
                self._n += 1
                tile_count += 1
                grade = random.choices(["Grade A", "Grade B", "Reject"], weights=[7, 2, 1])[0]
                tid = f"DEMO-{self._n:05d}"
                self._bus.publish(
                    f"tile/{tid}/camera/result",
                    {"tile_id": tid, "seq": self._n, "station": "camera", "status": "done", "grade": grade,
                     "rule_grade": grade, "crack_detected": grade != "Grade A", "corner_broken": grade == "Reject",
                     "ts": time.time()},
                )
                if self._tap:
                    pending.append(now + self._transit)
            if pending and now >= pending[0]:
                pending.pop(0)
                self._bus.publish("station/tap", {"ts": time.time(), "tile_present": True}, qos=0)
            self._halt.wait(0.25)

    def _answer_ping(self, topic: str, payload: bytes) -> None:
        import json

        try:
            pid = json.loads(payload).get("id")
        except ValueError:
            pid = None
        self._bus.publish(
            "station/diag/pong/camera",
            {"id": pid, "node": "camera", "ts": time.time(), "ok": True, "detail": {"source_kind": "camera", "demo": True}},
            qos=0,
        )

    def _answer(self, topic: str, payload: bytes) -> None:
        import json

        tid = topic.split("/")[1]
        self._bus.publish(f"tile/{tid}/ack", {"status": "ok"})
        try:
            dest = json.loads(payload).get("destination")
        except ValueError:
            dest = None
        self._placed += 1
        self._bus.publish(
            f"tile/{tid}/placed",
            {"tile_id": tid, "station": "gantry", "outcome": "placed", "destination": dest, "reason": None,
             "tile_in_cup": False, "stack_count": None, "ts": time.time()},
        )
