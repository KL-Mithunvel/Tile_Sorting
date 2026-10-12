"""What the gantry tells the rest of the station, and what it lets the station tell it.

`tile_feed.py` is the inbound half of the contract (finals in, acks out). This is
the outbound half the Pi could not see before: whether a tile was actually
placed, whether the machine is healthy, and a small set of remote commands.

    tile/<id>/placed        one per tile, after the job ended (placed | failed)
    station/gantry/status   retained, last-will `{"state":"offline"}`
    machine/alarm           an alarm / fault / link loss edge, `kind: gantry_*`
    station/gantry/cmd      pause | resume | acknowledge | reset_stack  (always on)
                            home | recover                (off unless enabled)
    station/gantry/cmd/result

Contract: documents/programming/gantry_station_plan.md and
station_coordination.md section 4.

Both classes are pure around an injected bus (`publish(topic, payload, qos,
retain)` / `subscribe(filter, cb)`) and clock, so they are tested with an
in-memory bus. Neither can stop the gantry from working: every publish is
guarded, and a bus that is down only means the report is buffered.

Safety: `home` and `recover` MOVE the machine, so they are refused unless
`motion_commands_enabled` is set. The e-stop is never a message.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Callable, Optional

log = logging.getLogger(__name__)

TOPIC_STATUS = "station/gantry/status"
TOPIC_CMD = "station/gantry/cmd"
TOPIC_CMD_RESULT = "station/gantry/cmd/result"
TOPIC_ALARM = "machine/alarm"
TOPIC_DIAG_PONG = "station/diag/pong/gantry"


def placed_topic(tile_id: str) -> str:
    return f"tile/{tile_id}/placed"


SAFE_COMMANDS = ("pause", "resume", "acknowledge", "reset_stack")
MOTION_COMMANDS = ("home", "recover")


class GantryReporter:
    def __init__(self, bus, driver, model, queue, backend=None, backend_name: str = "mock",
                 clock: Callable[[], float] = time.time, status_interval_s: float = 2.0,
                 max_pending: int = 200):
        self._bus, self._driver, self._model, self._queue = bus, driver, model, queue
        self._backend, self._backend_name, self._clock = backend, backend_name, clock
        self._closed = False
        self._status_due = False  # set from on_job_done, acted on by the next tick()
        self._interval = float(status_interval_s)
        self._pending: deque = deque(maxlen=max_pending)  # (topic, payload, qos, retain) not yet sent
        self._last_status_ts = float("-inf")
        self._last_key = None
        self._alarm_kinds: dict = {}  # kind -> the text it was last raised with
        self.counters = {"placed": 0, "failed": 0, "dropped": 0}

    def bind(self, driver) -> None:
        """The driver is built with this reporter's `on_job_done`, so it can only
        be handed over afterwards."""
        self._driver = driver

    def close(self) -> None:
        """Clean shutdown: say so, retained, instead of leaving it to the last-will."""
        if self._closed:
            return
        self._closed = True
        self._try(TOPIC_STATUS, {"state": "offline", "level": "fail", "ts": self._clock()}, 0, True)

    # ---- per-tile outcome -----------------------------------------------------

    def on_job_done(self, tile_id: str, ok: bool, reason: Optional[str]) -> None:
        """Hook for JobRunner / MockJobDriver `on_job_done`. Called from the
        driver's own thread, sometimes with its lock held: it must not call back
        into the driver except for the lock-free `holding` property."""
        now = self._clock()
        job = getattr(self._queue, "last_finished", None)
        if job is not None and job.tile_id != tile_id:
            job = None
        destination = job.destination if job else None
        holding = getattr(self._driver, "holding", None)
        stack_count = None
        if ok and destination is not None:
            try:
                stack_count = self._model.count(destination)
            except Exception:  # noqa: BLE001
                stack_count = None
        body = {
            "tile_id": tile_id,
            "station": "gantry",
            "outcome": "placed" if ok else "failed",
            "grade": job.grade if job else None,
            "source": job.source if job else None,
            "destination": destination,
            "reason": None if ok else (reason or "unknown"),
            # A failure with the tile still on the cup needs a person; one with
            # nothing in the cup (a missed grip, a refused plan) does not.
            "tile_in_cup": (bool(holding) if holding is not None else None) if not ok else False,
            "stack_count": stack_count,
            "attempt": job.attempt if job else None,
            "ts": now,
        }
        self.counters["placed" if ok else "failed"] += 1
        self._send(placed_topic(tile_id), body, qos=1)
        if not ok:
            self._raise("gantry_pick_failed", f"{tile_id}: {body['reason']}"
                        + (" - tile may still be on the cup" if body["tile_in_cup"] else ""), tile_id)
        # The status is NOT built here: this runs inside the driver's own lock and a
        # driver's status() takes that same lock (MockJobDriver's is not re-entrant).
        # The next tick() publishes it.
        self._status_due = True

    # ---- machine status + alarm edges ------------------------------------------

    def snapshot(self) -> dict:
        d = {}
        try:
            d = self._driver.status() if self._driver is not None else {}
        except Exception:  # noqa: BLE001
            log.exception("driver.status() failed")
        q = self._queue.snapshot()
        active = d.get("active") or {}
        connected = d.get("connected")
        alarm, fault = d.get("alarm"), d.get("fault")
        homed = d.get("homed")
        paused = bool(d.get("paused"))
        if alarm or fault or connected is False:
            level = "fail"
        elif homed is False or paused or d.get("link_error"):
            level = "warn"
        else:
            level = "ok"
        try:
            stacks = self._model.counts()
        except Exception:  # noqa: BLE001
            stacks = {}
        return {
            "state": "online",
            "level": level,
            "phase": d.get("phase") or ("job" if active else "idle"),
            "fault": fault,
            "alarm": alarm,
            "homed": homed,
            "fsm": d.get("fsm"),
            "vacuum": d.get("vacuum"),
            "link": connected,
            "link_error": d.get("link_error"),
            "paused": paused,
            "queue_depth": q["depth"],
            "active_tile": active.get("tile_id"),
            "step": active.get("step_label"),
            "completed": d.get("completed", self.counters["placed"]),
            "failed": d.get("failed", self.counters["failed"]),
            "last_error": d.get("last_error"),
            "stacks": stacks,
            "backend": self._backend_name,
            "ts": self._clock(),
        }

    def publish_status(self, force: bool = False) -> dict:
        now = self._clock()
        snap = self.snapshot()
        self._edge_alarms(snap)
        key = (snap["level"], snap["phase"], snap["alarm"], snap["fault"], snap["paused"], snap["homed"],
               snap["queue_depth"], snap["active_tile"], snap["link"], tuple(sorted(snap["stacks"].items())))
        if force or key != self._last_key or now - self._last_status_ts >= self._interval:
            self._last_key, self._last_status_ts = key, now
            self._send(TOPIC_STATUS, snap, qos=0, retain=True)
        return snap

    def _edge_alarms(self, snap: dict) -> None:
        for kind, text in (("gantry_alarm", f"gantry alarm {snap['alarm']}" if snap["alarm"] else None),
                           ("gantry_fault", f"gantry held: {snap['fault']}" if snap["fault"] else None),
                           ("gantry_link", "gantry link to the MCU is down" if snap["link"] is False else None)):
            if text is None:
                self._alarm_kinds.pop(kind, None)
            elif self._alarm_kinds.get(kind) != text:
                self._alarm_kinds[kind] = text
                self._raise(kind, text, snap.get("active_tile"))

    def _raise(self, kind: str, message: str, tile_id: Optional[str]) -> None:
        self._send(TOPIC_ALARM, {"ts": self._clock(), "level": "alarm", "kind": kind, "message": message,
                                 "tile_id": tile_id, "station": "gantry"}, qos=1)

    # ---- time ----------------------------------------------------------------------

    def tick(self) -> None:
        """Call a few times a second (any thread): flush what could not be sent,
        publish the status when it changed or is due."""
        self._flush()
        due, self._status_due = self._status_due, False
        self.publish_status(force=due)

    # ---- sending -----------------------------------------------------------------

    def _send(self, topic: str, payload: dict, qos: int, retain: bool = False) -> None:
        if self._pending:
            self._flush()
        if not self._try(topic, payload, qos, retain):
            # Status is overwritten by the next one; only tile outcomes and alarms
            # are worth carrying across an outage.
            if qos >= 1:
                if len(self._pending) == self._pending.maxlen:
                    self.counters["dropped"] += 1
                self._pending.append((topic, payload, qos, retain))

    def _flush(self) -> None:
        while self._pending:
            topic, payload, qos, retain = self._pending[0]
            if not self._try(topic, payload, qos, retain):
                return
            self._pending.popleft()

    def _try(self, topic: str, payload: dict, qos: int, retain: bool) -> bool:
        try:
            return bool(self._bus.publish(topic, payload, qos=qos, retain=retain))
        except Exception:  # noqa: BLE001 - reporting must never stop the gantry
            log.exception("could not publish %s", topic)
            return False

    @property
    def pending(self) -> int:
        return len(self._pending)


class GantryCommands:
    """`station/gantry/cmd` -> the runner / backend / model, one result per command."""

    def __init__(self, bus, driver, backend, model, reporter: Optional[GantryReporter] = None,
                 commands_enabled: bool = True, motion_commands_enabled: bool = False,
                 clock: Callable[[], float] = time.time):
        self._bus, self._driver, self._backend, self._model = bus, driver, backend, model
        self._reporter, self._clock = reporter, clock
        self.commands_enabled = bool(commands_enabled)
        self.motion_commands_enabled = bool(motion_commands_enabled)

    def bind(self, driver) -> None:
        self._driver = driver

    def attach(self) -> None:
        self._bus.subscribe(TOPIC_CMD, self._on_cmd)

    def _result(self, cmd_id, name, ok: bool, reason: Optional[str] = None) -> None:
        try:
            self._bus.publish(TOPIC_CMD_RESULT, {"id": cmd_id, "cmd": name, "ok": ok, "reason": reason,
                                                 "ts": self._clock()}, qos=1)
        except Exception:  # noqa: BLE001
            log.exception("could not publish a command result")

    def _on_cmd(self, topic: str, payload: bytes) -> None:
        try:
            body = json.loads(payload)
            if not isinstance(body, dict):
                raise ValueError("not an object")
            name = body["cmd"]
        except (ValueError, KeyError, TypeError):
            self._result(None, None, False, "unreadable command")
            return
        cmd_id = body.get("id")
        ok, reason = self.execute(name, body)
        self._result(cmd_id, name, ok, reason)
        if self._reporter is not None:
            self._reporter.publish_status(force=True)

    def execute(self, name, body: Optional[dict] = None) -> tuple:
        """Returns (ok, reason). Never raises."""
        body = body or {}
        if name not in SAFE_COMMANDS + MOTION_COMMANDS:
            return False, f"unknown command {name!r}"
        if not self.commands_enabled:
            return False, "remote commands are disabled on this node"
        if name in MOTION_COMMANDS and not self.motion_commands_enabled:
            return False, f"{name} moves the machine; motion_commands_enabled is off"
        try:
            if name == "pause":
                return self._set_mode("manual")
            if name == "resume":
                return self._set_mode("jobs")
            if name == "reset_stack":
                stack = body.get("stack")
                count = body.get("count", 0)
                if not isinstance(stack, str) or not isinstance(count, int) or isinstance(count, bool):
                    return False, "reset_stack needs a stack name and an integer count"
                self._model.reset_stack(stack, count)
                return True, None
            action = {"acknowledge": "acknowledge", "home": "request_home", "recover": "recover"}[name]
            fn = getattr(self._driver, action, None)
            if fn is None:
                return False, f"{name} is not available with the {type(self._driver).__name__}"
            if not fn():
                return False, f"{name} refused in the current state (busy, or an alarm is still latched)"
            return True, None
        except (ValueError, KeyError) as exc:
            return False, str(exc.args[0]) if exc.args else type(exc).__name__
        except Exception as exc:  # noqa: BLE001
            log.exception("command %s failed", name)
            return False, f"{type(exc).__name__}: {exc}"

    def _set_mode(self, mode: str) -> tuple:
        if self._backend is None:
            return False, "no backend to pause"
        self._backend.set_mode(mode)
        return True, None


__all__ = ["GantryReporter", "GantryCommands", "TOPIC_STATUS", "TOPIC_CMD", "TOPIC_CMD_RESULT", "placed_topic",
           "SAFE_COMMANDS", "MOTION_COMMANDS"]
