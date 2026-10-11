"""Runs the Pi's queued tiles on the MOCK gantry, step by step, so the twin shows
real destinations and real stack heights without any hardware.

This is the mock stand-in for `job_runner.py` + a serial backend (batch 2): it
uses the real `plan_pick_place()` and the real `CoordinateModel`, and only the
motion itself is simulated. Pure logic around a simulator object — `tick(pose,
dt_s)` is deterministic given the pose it is shown, so it is tested with a fake
simulator and synthetic ticks.

It acts only while the simulator is in "jobs" mode. A jog or a vacuum click
switches the simulator to "manual", which pauses the job where it is; switching
back to "jobs" re-issues the interrupted step.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from pick_place.coordinate_model import (CoordinateModel, OutOfEnvelopeError, StackFullError,
                                         UnknownLocationError)
from pick_place.protocol import Command
from pick_place.sequencer import Feeds, PickPlacePlan, plan_pick_place

log = logging.getLogger(__name__)

ARRIVE_TOL_MM = 0.05


class MockJobDriver:
    def __init__(self, sim, queue, model: CoordinateModel, feeds: Feeds, dwell_s: float = 0.4,
                 on_job_done: Optional[Callable[[str, bool, Optional[str]], None]] = None):
        self._sim, self._queue, self._model, self._feeds = sim, queue, model, feeds
        self._dwell_s = dwell_s
        self._on_job_done = on_job_done
        self._lock = threading.Lock()
        self._job = None
        self._plan: Optional[PickPlacePlan] = None
        self._index = 0
        self._target: Optional[tuple] = None
        self._wait_s = 0.0
        self._issued = False
        self._holding = False
        self._last_error: Optional[str] = None
        self._completed = 0
        self._failed = 0

    # ---- one simulation tick ----------------------------------------------

    def tick(self, pose, dt_s: float) -> None:
        """Call once per simulator advance, with the pose it just returned."""
        with self._lock:
            if self._sim.mode != "jobs":
                self._issued = False  # re-issue the current step when jobs resume
                return
            if self._plan is None and not self._begin_next():
                return
            step = self._plan.steps[self._index]
            if not self._issued:
                self._issue(step.command)
                return
            if self._wait_s > 0:
                self._wait_s -= dt_s
                if self._wait_s > 0:
                    return
            elif self._target is not None and not self._arrived(pose):
                return
            self._complete_step(step)

    def _begin_next(self) -> bool:
        job = self._queue.start_next()
        if job is None:
            return False
        try:
            self._plan = plan_pick_place(job.source, job.destination, self._model, self._feeds)
        except (UnknownLocationError, StackFullError, OutOfEnvelopeError) as exc:
            reason = exc.args[0] if exc.args else type(exc).__name__
            self._queue.finish(job.tile_id, ok=False)
            self._failed += 1
            self._last_error = f"{job.tile_id}: {reason}"
            log.warning("job %s refused: %s", job.tile_id, reason)
            if self._on_job_done:
                self._on_job_done(job.tile_id, False, str(reason))
            return False
        self._job, self._index, self._issued, self._holding = job, 0, False, False
        return True

    def _issue(self, cmd: Command) -> None:
        self._issued = True
        if cmd.verb == "MOVE":
            axes = {a[0]: float(a[1:]) for a in cmd.args if a[0] in "XYZ"}
            cur = self._sim.target  # an axis left out of a MOVE holds
            self._target = (axes.get("X", cur[0]), axes.get("Y", cur[1]), axes.get("Z", cur[2]))
            self._sim.move_to(*self._target)
            self._wait_s = 0.0
        elif cmd.verb == "VAC":
            on = cmd.args[0] == "ON"
            self._sim.command_vacuum(on, "PICKING" if on else "PLACING")
            self._target = None
            self._wait_s = self._dwell_s

    def _arrived(self, pose) -> bool:
        tx, ty, tz = self._target
        return (abs(pose.x - tx) <= ARRIVE_TOL_MM and abs(pose.y - ty) <= ARRIVE_TOL_MM
                and abs(pose.z - tz) <= ARRIVE_TOL_MM)

    def _complete_step(self, step) -> None:
        if step.label == "grip":
            self._holding = True
        elif step.label == "release":
            self._holding = False
            self._model.record_placed(self._plan.destination)
        self._index += 1
        self._issued = False
        self._target = None
        if self._index >= len(self._plan.steps):
            self._queue.finish(self._job.tile_id, ok=True)
            self._completed += 1
            self._last_error = None
            done_id = self._job.tile_id
            self._job = self._plan = None
            if self._on_job_done:
                self._on_job_done(done_id, True, None)

    # ---- for the GUI --------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            active = None
            if self._job is not None and self._plan is not None:
                step = self._plan.steps[min(self._index, len(self._plan.steps) - 1)]
                active = {
                    "tile_id": self._job.tile_id, "grade": self._job.grade,
                    "source": self._plan.source, "destination": self._plan.destination,
                    "step": self._index + 1, "steps_total": len(self._plan.steps),
                    "step_label": step.label, "place_z_mm": self._plan.place_z,
                    "command": step.command.encode(0)[2:],
                }
            return {
                "active": active, "holding": self._holding,
                "paused": self._sim.mode != "jobs",
                "completed": self._completed, "failed": self._failed,
                "last_error": self._last_error,
            }
