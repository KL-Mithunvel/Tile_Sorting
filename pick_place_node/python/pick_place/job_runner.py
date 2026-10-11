"""Runs the Pi's queued tiles on the real gantry, one command at a time.

The counterpart of `mock_job_driver.py`, but over a `GantrySession`: it takes a
job from the `FinalQueue`, plans it with the real sequencer, sends each command,
waits for its outcome, and only then sends the next (protocol rule 3: one motion
in flight). `tick()` is the only entry point; it never blocks and is driven by
the worker thread or a test loop with a fake clock.

Safety rules this module owns (the MCU enforces its own as well):

  - Nothing is sent until the config has been pushed (SETENV / SETSPEED / SETTOL)
    and the machine reports HOMED=1. Homing moves the machine, so it is an
    explicit `request_home()`, never automatic.
  - A `VACUUM_TIMEOUT` on the grip is retried once (re-issue the descent + grip);
    a second miss drops the pump, raises to safe Z and fails the job. No tile is
    in the cup, so parking is safe.
  - ANY other failure with a tile in the cup stops and holds — it never releases
    or moves on its own. An operator must `acknowledge()`.
  - An `alarm` (limit, e-stop, LOST_STEPS, SKEW_Y, link timeout) aborts the job and
    sends nothing further. `recover()` = CLEARALARM + HOME ALL, explicit.
  - A tile is counted onto a stack only after the release's `ok`.
  - Silence is a fault: a command with no answer in time stops the machine.
  - When the runner is disabled (jog / manual) an in-flight command finishes, no
    new one starts, and on resume the gantry first rises to safe Z and then
    re-issues the interrupted step, so a manual move can't make it drag a tile.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from pick_place import protocol
from pick_place.coordinate_model import (CoordinateModel, OutOfEnvelopeError, StackFullError,
                                         UnknownLocationError)
from pick_place.gantry_session import GantrySession
from pick_place.protocol import Command
from pick_place.sequencer import Feeds, PickPlacePlan, PlanStep, plan_park, plan_pick_place

log = logging.getLogger(__name__)

IDLE, JOB, SERVICE, FAULT = "idle", "job", "service", "fault"


@dataclass(frozen=True)
class RunnerConfig:
    ack_timeout_s: float = 2.0       # `ok` / `err` for an ordinary command
    vacuum_timeout_s: float = 5.0    # `ok` for VAC ON (sent only after the grip is confirmed)
    motion_timeout_s: float = 60.0   # `done` for a motion that was accepted
    ping_interval_s: float = 1.0     # idle PING so the MCU's link watchdog stays happy
    pick_retries: int = 1

    @classmethod
    def from_config(cls, config: dict) -> "RunnerConfig":
        r = (config.get("gantry") or {}).get("runner") or {}
        return cls(**{k: r[k] for k in cls.__dataclass_fields__ if k in r})


class _Active:
    def __init__(self, kind: str, steps: list, plan: Optional[PickPlacePlan] = None, job=None, name: str = ""):
        self.kind, self.steps, self.plan, self.job, self.name = kind, steps, plan, job, name
        self.index = 0
        self.seq: Optional[int] = None
        self.cmd: Optional[Command] = None
        self.label = ""
        self.deadline = 0.0
        self.retries_left = 0
        self.interrupted = False
        self.resume_raise = False


class JobRunner:
    def __init__(self, session: GantrySession, queue, model: CoordinateModel, feeds: Feeds, config: dict,
                 clock: Callable[[], float] = time.monotonic,
                 enabled: Callable[[], bool] = lambda: True,
                 on_job_done: Optional[Callable[[str, bool, Optional[str]], None]] = None):
        self._s, self._queue, self._model, self._feeds = session, queue, model, feeds
        self._cfg = RunnerConfig.from_config(config)
        self._gantry_cfg = config.get("gantry") or {}
        self._env = config["workspace"]
        self._clock, self._enabled, self._on_job_done = clock, enabled, on_job_done
        self._lock = threading.RLock()
        self._active: Optional[_Active] = None
        self._phase = IDLE
        self._configured = False
        self._holding = False
        self._last_error: Optional[str] = None
        self._fault: Optional[str] = None
        self._completed = self._failed = 0
        self._last_ping = float("-inf")
        self._ping_seq: Optional[int] = None
        self._link_error: Optional[str] = None

    # ---- operator actions ---------------------------------------------------

    def request_home(self) -> bool:
        """Home all axes. Refused (False) unless the runner is idle and there is no alarm."""
        with self._lock:
            if self._active is not None or self._phase == FAULT or self._s.alarm is not None:
                return False
            self._start_service("home", [PlanStep(protocol.home("ALL"), "home"),
                                         PlanStep(protocol.get_state(), "state")])
            return True

    def recover(self) -> bool:
        """After an alarm: CLEARALARM, then HOME ALL. The operator has dealt with the cause."""
        with self._lock:
            if self._active is not None and self._active.kind == SERVICE:
                return False
            act, self._active = self._active, None
            if act is not None and act.kind == JOB:  # never orphan a job in the queue
                self._queue.finish(act.job.tile_id, ok=False)
                self._failed += 1
                self._last_error = f"{act.job.tile_id}: abandoned by recover"
                self._notify(act.job.tile_id, False, "recover requested")
            self._holding = False
            self._phase = IDLE
            self._fault = None
            self._start_service("recover", [PlanStep(protocol.clear_alarm(), "clear_alarm"),
                                            PlanStep(protocol.home("ALL"), "home"),
                                            PlanStep(protocol.get_state(), "state")])
            return True

    def acknowledge(self) -> bool:
        """Clear a held fault once the operator has dealt with it (e.g. removed the tile)."""
        with self._lock:
            if self._phase != FAULT or self._s.alarm is not None:
                return False
            self._phase, self._fault, self._holding = IDLE, None, False
            return True

    # ---- the one entry point --------------------------------------------------

    def tick(self, pose=None, dt_s: float = 0.0) -> None:
        with self._lock:
            try:
                self._s.pump()
                self._link_error = None
                self._step()
            except Exception as exc:  # noqa: BLE001 - LinkError and anything else: report, don't die
                self._link_error = f"{type(exc).__name__}: {exc}"
                log.warning("gantry runner: %s", self._link_error)
                if self._active is not None:
                    self._finish_failed(self._active, self._link_error, park=False)

    def _step(self) -> None:
        now = self._clock()
        recovering = self._active is not None and self._active.kind == SERVICE and self._active.name == "recover"
        if self._s.alarm is not None and not recovering:
            if self._active is not None or self._phase != FAULT:
                self._abort_for_alarm(self._s.alarm.reason)
            return
        if self._active is not None:
            self._advance(now)
            return
        if self._phase == FAULT or not self._s.connected:
            return
        if self._ping_seq is not None:
            r = self._s.result(self._ping_seq)
            if r is not None and r.finished and not r.failed:
                self._ping_seq = None
            elif r is None or r.failed or now - r.sent_ts > self._cfg.ack_timeout_s:
                self._ping_seq = None
                self._phase, self._fault = FAULT, "gantry link silent (no answer to PING)"
                self._last_error = self._fault
                return
        if not self._configured:
            self._start_service("startup", self._startup_steps())
            return
        if self._enabled() and self._s.homed and self._s.fsm == "IDLE" and self._begin_job():
            return
        if self._ping_seq is None and now - self._last_ping >= self._cfg.ping_interval_s:
            self._last_ping = now
            self._ping_seq = self._s.send(protocol.ping())

    # ---- running a command list ---------------------------------------------------

    def _startup_steps(self) -> list:
        g, e = self._gantry_cfg, self._env
        steps = [PlanStep(protocol.set_env((e["x_min_mm"], e["x_max_mm"]), (e["y_min_mm"], e["y_max_mm"]),
                                           (e["z_min_mm"], e["z_max_mm"])), "set_env")]
        for axis, sp in (g.get("speed") or {}).items():
            steps.append(PlanStep(protocol.set_speed(axis, sp["max_mm_min"], sp["accel_mm_s2"]), f"speed_{axis}"))
        tol = g.get("tolerance_mm")
        if tol:
            steps.append(PlanStep(protocol.set_tolerance(tol["lost_steps"], tol["skew"]), "set_tolerance"))
        steps.append(PlanStep(protocol.get_state(), "state"))
        return steps

    def _start_service(self, name: str, steps: list) -> None:
        self._active = _Active(SERVICE, list(steps), name=name)
        self._phase = SERVICE
        self._advance(self._clock())

    def _begin_job(self) -> bool:
        job = self._queue.start_next()
        if job is None:
            return False
        try:
            plan = plan_pick_place(job.source, job.destination, self._model, self._feeds)
        except (UnknownLocationError, StackFullError, OutOfEnvelopeError) as exc:
            reason = str(exc.args[0]) if exc.args else type(exc).__name__
            self._queue.finish(job.tile_id, ok=False)
            self._failed += 1
            self._last_error = f"{job.tile_id}: {reason}"
            self._notify(job.tile_id, False, reason)
            return True
        act = _Active(JOB, list(plan.steps), plan=plan, job=job)
        act.retries_left = self._cfg.pick_retries
        self._active, self._phase, self._holding = act, JOB, False
        self._advance(self._clock())
        return True

    def _send_current(self, act: _Active, now: float) -> None:
        step = act.steps[act.index]
        self._dispatch(act, step.command, step.label, now)

    def _dispatch(self, act: _Active, cmd: Command, label: str, now: float) -> None:
        act.cmd, act.label = cmd, label
        act.seq = self._s.send(cmd)
        act.deadline = now + (self._cfg.vacuum_timeout_s if cmd == protocol.vac(True) else self._cfg.ack_timeout_s)

    def _advance(self, now: float) -> None:
        act = self._active
        if act is None:
            return
        if act.seq is None:
            if act.kind == JOB and not self._enabled():
                act.interrupted = True
                return
            if act.interrupted:
                act.interrupted, act.resume_raise = False, True
                self._dispatch(act, plan_park(self._model, self._feeds).command, "resume_raise", now)
                return
            self._send_current(act, now)
            return

        res = self._s.result(act.seq)
        if res is None:
            self._finish_failed(act, "result lost")
            return
        if res.status == "ok" and res.command.is_motion:
            act.deadline = max(act.deadline, (res.acked_ts or now) + self._cfg.motion_timeout_s)
        if not res.finished:
            if now > act.deadline:
                self._s.send(protocol.stop())
                self._finish_failed(act, f"no answer to {act.cmd.verb} ({act.label})", park=False)
            return
        act.seq = None
        if res.failed:
            self._on_err(act, res.reason or "ERR")
            return
        self._on_ok(act)

    def _on_ok(self, act: _Active) -> None:
        if act.resume_raise:
            act.resume_raise = False  # now re-issue the interrupted step (index unchanged)
            return
        label = act.label
        if act.kind == JOB:
            if label == "grip":
                self._holding = True
            elif label == "release":
                self._holding = False
                self._model.record_placed(act.plan.destination)
        act.index += 1
        if act.index >= len(act.steps):
            self._finish_ok(act)

    def _on_err(self, act: _Active, reason: str) -> None:
        if act.kind != JOB:
            self._phase = FAULT
            self._fault = f"{act.name}: {act.label} -> {reason}"
            self._last_error = self._fault
            self._active = None
            return
        if act.resume_raise:
            act.resume_raise = False
        if act.label == "grip" and reason == "VACUUM_TIMEOUT" and act.retries_left > 0:
            act.retries_left -= 1
            act.index = act.plan.pick_first
            self._holding = False
            return
        # A miss with nothing in the cup is safe to park; anything else holds still.
        park = act.label in ("grip",) or (not self._holding and act.label in
                                          ("raise", "over_source", "descend_pick"))
        self._finish_failed(act, f"{act.label}: {reason}", park=park)

    # ---- endings --------------------------------------------------------------

    def _finish_ok(self, act: _Active) -> None:
        self._active = None
        if act.kind == JOB:
            self._queue.finish(act.job.tile_id, ok=True)
            self._completed += 1
            self._last_error = None
            self._phase = IDLE
            self._notify(act.job.tile_id, True, None)
        else:
            if act.name == "startup":
                self._configured = True
            self._phase = IDLE

    def _finish_failed(self, act: _Active, reason: str, park: bool = False) -> None:
        if act.kind == JOB:
            self._fail_job(reason, park=park)
        else:
            self._active = None
            self._phase = FAULT
            self._fault = f"{act.name}: {reason}"
            self._last_error = self._fault

    def _fail_job(self, reason: str, park: bool) -> None:
        act = self._active
        if act is None or act.job is None:
            return
        tile_id = act.job.tile_id
        self._queue.finish(tile_id, ok=False)
        self._failed += 1
        self._last_error = f"{tile_id}: {reason}"
        self._notify(tile_id, False, reason)
        self._active = None
        if park and self._s.connected:
            # Nothing in the cup: drop the pump and get out of the way.
            self._holding = False
            self._start_service("park", [PlanStep(protocol.vac(False), "vac_off"), plan_park(self._model, self._feeds)])
        else:
            self._phase = FAULT
            self._fault = f"{tile_id}: {reason}" + (" - tile may be in the cup" if self._holding else "")

    def _abort_for_alarm(self, reason: str) -> None:
        act, self._active = self._active, None
        if act is not None and act.kind == JOB:
            self._queue.finish(act.job.tile_id, ok=False)
            self._failed += 1
            self._last_error = f"{act.job.tile_id}: alarm {reason}"
            self._notify(act.job.tile_id, False, f"alarm {reason}")
        self._phase = FAULT
        self._fault = f"alarm {reason}"

    def _notify(self, tile_id: str, ok: bool, reason: Optional[str]) -> None:
        if self._on_job_done:
            try:
                self._on_job_done(tile_id, ok, reason)
            except Exception:  # noqa: BLE001
                log.exception("on_job_done callback failed")

    # ---- for the GUI --------------------------------------------------------------

    def status(self) -> dict:
        with self._lock:
            a = self._active
            active = None
            if a is not None and a.kind == JOB:
                step = a.steps[min(a.index, len(a.steps) - 1)]
                active = {
                    "tile_id": a.job.tile_id, "grade": a.job.grade,
                    "source": a.plan.source, "destination": a.plan.destination,
                    "step": a.index + 1, "steps_total": len(a.steps),
                    "step_label": a.label if a.resume_raise else step.label,
                    "place_z_mm": a.plan.place_z,
                    "command": (a.cmd or step.command).encode(0)[2:],
                }
            s = self._s
            return {
                "active": active, "holding": self._holding,
                "paused": not self._enabled(),
                "completed": self._completed, "failed": self._failed,
                "last_error": self._last_error,
                "phase": self._phase if a is None or a.kind != SERVICE else f"service:{a.name}",
                "fault": self._fault,
                "homed": s.homed, "fsm": s.fsm, "vacuum": s.vacuum,
                "alarm": s.alarm.reason if s.alarm else None,
                "connected": s.connected, "link_error": self._link_error,
                "heartbeat_age_s": s.heartbeat_age_s(),
                "deviation_mm": s.pos.deviation() if s.pos else {},
            }
