"""Background loop that advances MockGantrySimulator on a wall-clock tick and
publishes the result into a thread-safe SharedState — the mock stand-in for a
real GantryWorker (documents/project/Pick_Place_Sorting_Subsystem.md §1/§3.1).
Not unit-tested itself, same reasoning as camera_node's CameraWorker: it's a
thin timing/threading wrapper around already-tested pure logic
(.claude/CLAUDE.md Development Rule 1).
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from pick_place.mock_state import GantryPose, MockGantrySimulator


class SharedState:
    """Thread-safe latest-value store the dashboard reads from."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pose = GantryPose(x=0.0, y=0.0, z=0.0, vacuum_on=False, machine_state="IDLE")

    def update(self, pose: GantryPose) -> None:
        with self._lock:
            self._pose = pose

    def status(self) -> dict:
        with self._lock:
            pose = self._pose
        return {
            "x": round(pose.x, 2),
            "y": round(pose.y, 2),
            "z": round(pose.z, 2),
            "vacuum_on": pose.vacuum_on,
            "machine_state": pose.machine_state,
        }


class TwinWorker:
    def __init__(self, simulator: MockGantrySimulator, state: SharedState, tick_hz: float = 30.0):
        self._simulator = simulator
        self._state = state
        self._period_s = 1.0 / tick_hz
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        last = time.monotonic()
        while self._running:
            time.sleep(self._period_s)
            now = time.monotonic()
            dt = now - last
            last = now
            pose = self._simulator.advance(dt)
            self._state.update(pose)
