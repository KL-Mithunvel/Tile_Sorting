"""Pure gantry-motion simulator — no I/O, no threading, no clock of its own.

Stands in for a real GantryWorker until pick-and-place motion-control hardware
exists (documents/project/Pick_Place_Sorting_Subsystem.md §3.1,
.claude/CLAUDE.md Development Rule 1: hardware I/O stays separate from pure
logic). `MockGantrySimulator.advance(dt_s)` is deterministic given elapsed
time, so it's testable with synthetic time steps the same way
acoustic/capture.py's TriggerDetector is testable with synthetic audio blocks.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


def load_config(path: Optional[Path] = None) -> dict:
    if path is None:
        path = Path(__file__).parent / "config.yaml"
    with open(path, "r") as f:
        return yaml.safe_load(f)


@dataclass(frozen=True)
class GantryPose:
    x: float
    y: float
    z: float
    vacuum_on: bool
    machine_state: str
    mode: str = "auto"


@dataclass(frozen=True)
class Waypoint:
    x: float
    y: float
    z: float
    travel_state: str
    vacuum_on: bool
    dwell_state: str
    dwell_s: float = 0.0


def build_default_patrol(config: dict) -> list[Waypoint]:
    """Home -> pick (descend, vacuum on) -> place (descend, vacuum off) -> home,
    looped forever. Gives the digital twin something to visibly do — moving,
    picking, placing, vacuum toggling — with no real motion-control code."""
    mock_cfg = config["mock"]
    home = mock_cfg["home"]
    pick_xy = mock_cfg["pick_xy"]
    place_xy = mock_cfg["place_xy"]
    travel_z = home["z"]
    dwell_s = mock_cfg["dwell_s"]

    return [
        Waypoint(home["x"], home["y"], travel_z, "HOMING", vacuum_on=False, dwell_state="IDLE", dwell_s=dwell_s),
        Waypoint(pick_xy["x"], pick_xy["y"], travel_z, "MOVING", vacuum_on=False, dwell_state="IDLE"),
        Waypoint(pick_xy["x"], pick_xy["y"], mock_cfg["pick_z_mm"], "MOVING", vacuum_on=True, dwell_state="PICKING", dwell_s=dwell_s),
        Waypoint(pick_xy["x"], pick_xy["y"], travel_z, "MOVING", vacuum_on=True, dwell_state="IDLE"),
        Waypoint(place_xy["x"], place_xy["y"], travel_z, "MOVING", vacuum_on=True, dwell_state="IDLE"),
        Waypoint(place_xy["x"], place_xy["y"], mock_cfg["place_z_mm"], "MOVING", vacuum_on=False, dwell_state="PLACING", dwell_s=dwell_s),
        Waypoint(place_xy["x"], place_xy["y"], travel_z, "MOVING", vacuum_on=False, dwell_state="IDLE"),
    ]


AXES = ("x", "y", "z")
MODES = ("auto", "manual")


class MockGantrySimulator:
    """Two modes: "auto" loops the patrol; "manual" holds still until jog()/
    set_vacuum() are called. All public methods take an internal lock, since
    advance() runs on the worker thread while jog() etc. come from Flask's."""

    def __init__(self, waypoints: list[Waypoint], travel_speed_mm_s: float,
                 workspace: Optional[dict] = None):
        if not waypoints:
            raise ValueError("waypoints must be non-empty")
        if travel_speed_mm_s <= 0:
            raise ValueError("travel_speed_mm_s must be positive")
        self._waypoints = waypoints
        self._speed = travel_speed_mm_s
        start = waypoints[0]
        self._x, self._y, self._z = start.x, start.y, start.z
        self._vacuum_on = start.vacuum_on
        self._machine_state = start.dwell_state
        # Start already sits at waypoints[0], so the first target is the *next*
        # waypoint rather than re-arriving at the one we're already at.
        self._index = 1 % len(waypoints)
        self._dwelling = False
        self._dwell_remaining = 0.0
        self._lock = threading.RLock()
        self._mode = "auto"
        self._manual_target = (self._x, self._y, self._z)
        self._limits = {
            a: (workspace[f"{a}_min_mm"], workspace[f"{a}_max_mm"]) for a in AXES
        } if workspace else None

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        with self._lock:
            if mode == self._mode:
                return
            self._mode = mode
            if mode == "manual":
                self._manual_target = (self._x, self._y, self._z)
                self._machine_state = "IDLE"
            else:
                # Resume the patrol from its first waypoint (home).
                self._index = 0
                self._dwelling = False

    def jog(self, axis: str, delta_mm: float) -> None:
        """Shift the manual target by delta_mm on one axis (clamped to the
        workspace). Switches to manual mode if needed."""
        if axis not in AXES:
            raise ValueError(f"axis must be one of {AXES}")
        with self._lock:
            self.set_mode("manual")
            target = list(self._manual_target)
            i = AXES.index(axis)
            target[i] += delta_mm
            if self._limits:
                lo, hi = self._limits[axis]
                target[i] = min(max(target[i], lo), hi)
            self._manual_target = tuple(target)

    def set_vacuum(self, on: bool) -> None:
        with self._lock:
            self.set_mode("manual")
            self._vacuum_on = bool(on)

    def advance(self, dt_s: float) -> GantryPose:
        if dt_s < 0:
            raise ValueError("dt_s must be non-negative")
        with self._lock:
            if self._mode == "manual":
                return self._advance_manual(dt_s)
            return self._advance_auto(dt_s)

    def _advance_manual(self, dt_s: float) -> GantryPose:
        tx, ty, tz = self._manual_target
        dx, dy, dz = tx - self._x, ty - self._y, tz - self._z
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        step = self._speed * dt_s
        if dist <= step:
            self._x, self._y, self._z = tx, ty, tz
            self._machine_state = "IDLE"
        else:
            r = step / dist
            self._x += dx * r
            self._y += dy * r
            self._z += dz * r
            self._machine_state = "MOVING"
        return self._pose()

    def _advance_auto(self, dt_s: float) -> GantryPose:
        if self._dwelling:
            self._dwell_remaining -= dt_s
            if self._dwell_remaining <= 0:
                self._dwelling = False
                self._index = (self._index + 1) % len(self._waypoints)
            return self._pose()

        target = self._waypoints[self._index]
        dx, dy, dz = target.x - self._x, target.y - self._y, target.z - self._z
        dist = math.sqrt(dx * dx + dy * dy + dz * dz)
        step = self._speed * dt_s

        if dist <= step:
            self._x, self._y, self._z = target.x, target.y, target.z
            self._vacuum_on = target.vacuum_on
            self._machine_state = target.dwell_state
            if target.dwell_s > 0:
                self._dwelling = True
                self._dwell_remaining = target.dwell_s
            else:
                self._index = (self._index + 1) % len(self._waypoints)
        else:
            ratio = step / dist
            self._x += dx * ratio
            self._y += dy * ratio
            self._z += dz * ratio
            self._machine_state = target.travel_state

        return self._pose()

    def _pose(self) -> GantryPose:
        return GantryPose(
            x=self._x, y=self._y, z=self._z,
            vacuum_on=self._vacuum_on, machine_state=self._machine_state,
            mode=self._mode,
        )
