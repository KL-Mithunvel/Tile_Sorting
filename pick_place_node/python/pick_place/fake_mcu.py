"""A line-level simulation of the gantry MCU — the executable spec of the protocol.

It speaks the real wire format (documents/programming/pick_place_control_protocol.md
§5, plus the closed-loop additions), so the job runner, the session and the HMI
backend are tested against something that behaves like the firmware will, and the
firmware can later be checked against the same expectations.

Behaviour implemented (the rules the firmware must also follow):
  - every command gets an immediate `ok <seq>` or `err <seq> <REASON>`
  - HOME / MOVE / JOG get a later `done <seq> X Y Z`
  - one motion at a time (`BUSY`), `NOT_HOMED` before homing, `OUT_OF_BOUNDS`
  - `VAC ON` answers `ok` only after the confirm delay, or `err VACUUM_TIMEOUT`
  - an alarm latches: everything but PING/STOP/GETPOS/GETSTATE/CLEARALARM is
    `ALARM_ACTIVE`; CLEARALARM clears it but leaves HOMED=0
  - closed loop: an encoder offset beyond the SETTOL `LOST` tolerance raises
    `alarm LOST_STEPS_<axis>`; Y vs A beyond `SKEW` raises `alarm SKEW_Y`

Time is explicit: call `tick(dt_s)` (tests) or let `LoopbackLink` do it on a
thread (demo). Not the firmware, not hardware.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Optional

from pick_place.protocol import fmt_mm

AXES = ("X", "Y", "Z")


class SimulatedMcu:
    def __init__(self, envelope=((0.0, 600.0), (0.0, 400.0), (0.0, 200.0)), speed_mm_s: float = 400.0,
                 vac_confirm_s: float = 0.05, home_time_s: float = 0.2, heartbeat_s: float = 0.5,
                 honor_feed: bool = True):
        self.envelope = {a: tuple(r) for a, r in zip(AXES, envelope)}
        self._hard_envelope = dict(self.envelope)
        self.speed = float(speed_mm_s)
        self.honor_feed = honor_feed  # False: always move at `speed_mm_s` (fast tests)
        self.vac_confirm_s = vac_confirm_s
        self.home_time_s = home_time_s
        self.heartbeat_s = heartbeat_s
        self.pos = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        self.enc_offset = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}  # inject lost steps / skew
        self.tol_lost, self.tol_skew = 1.0, 0.5
        self.fsm = "NEEDS_HOMING"
        self.homed = False
        self.vac = False
        self.alarm: Optional[str] = None
        self.vacuum_failures = 0       # the next N `VAC ON` answer VACUUM_TIMEOUT
        self.received: list = []       # every command line, for assertions
        self._out: deque = deque()
        self._motion: Optional[dict] = None
        self._vac_pending: Optional[dict] = None
        self._t = 0.0
        self._next_hb = heartbeat_s
        self._last_state = None
        self._lock = threading.RLock()
        self._emit_state(force=True)

    # ---- the wire ---------------------------------------------------------

    def write(self, line: str) -> None:
        with self._lock:
            self.received.append(line.strip())
            self._handle(line.strip())

    def read_lines(self) -> list:
        with self._lock:
            out = list(self._out)
            self._out.clear()
            return out

    def _emit(self, text: str) -> None:
        self._out.append(text)

    def _emit_state(self, force: bool = False) -> None:
        cur = (self.fsm, self.vac, self.homed)
        if force or cur != self._last_state:
            self._last_state = cur
            self._emit(f"state {self.fsm} VAC={int(self.vac)} HOMED={int(self.homed)}")

    # ---- fault injection ----------------------------------------------------

    def inject_alarm(self, reason: str) -> None:
        with self._lock:
            self._raise_alarm(reason)

    def _raise_alarm(self, reason: str) -> None:
        if self.alarm is not None:
            return
        self.alarm = reason
        self.homed = False
        self._cancel_motion("STOPPED")
        self.fsm = "ALARM"
        self._emit(f"alarm {reason}")
        self._emit_state()

    # ---- commands -----------------------------------------------------------

    def _handle(self, line: str) -> None:
        parts = line.split()
        if len(parts) < 2 or not parts[0].isdigit():
            self._emit(f"err {parts[0] if parts and parts[0].isdigit() else 0} BAD_CMD")
            return
        seq, verb, args = int(parts[0]), parts[1].upper(), parts[2:]

        always = ("PING", "STOP", "GETPOS", "GETSTATE", "CLEARALARM", "VAC")
        if self.alarm is not None and verb not in always:
            self._emit(f"err {seq} ALARM_ACTIVE")
            return

        if verb == "PING":
            self._emit(f"ok {seq}")
        elif verb == "GETPOS":
            self._emit(f"ok {seq}")
            self._emit_pos()
        elif verb == "GETSTATE":
            self._emit(f"ok {seq}")
            self._emit_state(force=True)
        elif verb == "STOP":
            self._emit(f"ok {seq}")
            self._cancel_motion("STOPPED")
            if self.alarm is None and self.fsm in ("MOVING", "HOMING"):
                self.fsm = "IDLE" if self.homed else "NEEDS_HOMING"
            self._emit_state()
        elif verb == "CLEARALARM":
            self.alarm, self.homed = None, False
            self.enc_offset = {k: 0.0 for k in self.enc_offset}
            self.fsm = "NEEDS_HOMING"
            self._emit(f"ok {seq}")
            self._emit_state()
        elif verb == "VAC":
            self._vac(seq, args)
        elif verb == "SETENV":
            self._setenv(seq, args)
        elif verb == "SETSPEED":
            self._emit(f"ok {seq}")
        elif verb == "SETTOL":
            self._settol(seq, args)
        elif verb == "HOME":
            self._home(seq, args)
        elif verb == "MOVE":
            self._move(seq, args)
        elif verb == "JOG":
            self._jog(seq, args)
        else:
            self._emit(f"err {seq} BAD_CMD")

    def _num(self, token: str) -> Optional[float]:
        try:
            v = float(token)
        except ValueError:
            return None
        return v if math.isfinite(v) else None

    def _setenv(self, seq: int, args: list) -> None:
        env = {}
        for tok in args:
            if len(tok) < 2 or tok[0] not in AXES or self._num(tok[1:]) is None:
                self._emit(f"err {seq} BAD_CMD")
                return
            env.setdefault(tok[0], []).append(self._num(tok[1:]))
        if sorted(env) != sorted(AXES) or any(len(v) != 2 or v[0] >= v[1] for v in env.values()):
            self._emit(f"err {seq} BAD_CMD")
            return
        self.envelope = {a: (env[a][0], env[a][1]) for a in AXES}
        self._emit(f"ok {seq}")

    def _settol(self, seq: int, args: list) -> None:
        vals = {}
        for tok in args:
            for key in ("LOST", "SKEW"):
                if tok.startswith(key) and self._num(tok[len(key):]) is not None:
                    vals[key] = self._num(tok[len(key):])
        if set(vals) != {"LOST", "SKEW"} or min(vals.values()) <= 0:
            self._emit(f"err {seq} BAD_CMD")
            return
        self.tol_lost, self.tol_skew = vals["LOST"], vals["SKEW"]
        self._emit(f"ok {seq}")

    def _vac(self, seq: int, args: list) -> None:
        if args == ["OFF"]:
            self._vac_pending = None
            self.vac = False
            self._emit(f"ok {seq}")
            self._emit_state()
        elif args == ["ON"]:
            if self.alarm is not None:
                self._emit(f"err {seq} ALARM_ACTIVE")
                return
            self._vac_pending = {"seq": seq, "left": self.vac_confirm_s}
            self.vac = True  # pump running; grip not confirmed yet
            self._emit_state()
        else:
            self._emit(f"err {seq} BAD_CMD")

    def _begin(self, seq: int, kind: str, target: dict, duration_s: float) -> None:
        self._emit(f"ok {seq}")
        self._motion = {"seq": seq, "kind": kind, "start": dict(self.pos), "target": target,
                        "total": max(duration_s, 1e-6), "elapsed": 0.0}
        self.fsm = "HOMING" if kind == "HOME" else "MOVING"
        self._emit_state()

    def _check_motion_allowed(self, seq: int, needs_home: bool = True) -> bool:
        if self._motion is not None:
            self._emit(f"err {seq} BUSY")
            return False
        if needs_home and not self.homed:
            self._emit(f"err {seq} NOT_HOMED")
            return False
        return True

    def _duration(self, target: dict, feed_mm_min: Optional[float]) -> float:
        speed = (feed_mm_min / 60.0) if (feed_mm_min and self.honor_feed) else self.speed
        speed = max(min(speed, self.speed), 1e-3)
        dist = math.sqrt(sum((target[a] - self.pos[a]) ** 2 for a in AXES))
        return dist / speed

    def _parse_axes(self, args: list):
        values, feed = {}, None
        for tok in args:
            if tok[:1] == "F" and self._num(tok[1:]) is not None:
                feed = self._num(tok[1:])
            elif tok[:1] in AXES and self._num(tok[1:]) is not None:
                values[tok[0]] = self._num(tok[1:])
            else:
                return None, None
        return values, feed

    def _move(self, seq: int, args: list) -> None:
        values, feed = self._parse_axes(args)
        if not values:
            self._emit(f"err {seq} BAD_CMD")
            return
        if not self._check_motion_allowed(seq):
            return
        target = {a: values.get(a, self.pos[a]) for a in AXES}
        if any(not self.envelope[a][0] <= target[a] <= self.envelope[a][1] for a in AXES):
            self._emit(f"err {seq} OUT_OF_BOUNDS")
            return
        self._begin(seq, "MOVE", target, self._duration(target, feed))

    def _jog(self, seq: int, args: list) -> None:
        values, feed = self._parse_axes(args)
        if not values or len(values) != 1:
            self._emit(f"err {seq} BAD_CMD")
            return
        if not self._check_motion_allowed(seq):
            return
        target = dict(self.pos)
        for a, d in values.items():
            target[a] = self.pos[a] + d
        if any(not self.envelope[a][0] <= target[a] <= self.envelope[a][1] for a in AXES):
            self._emit(f"err {seq} OUT_OF_BOUNDS")
            return
        self._begin(seq, "JOG", target, self._duration(target, feed))

    def _home(self, seq: int, args: list) -> None:
        if len(args) != 1 or args[0] not in ("ALL",) + AXES:
            self._emit(f"err {seq} BAD_CMD")
            return
        if not self._check_motion_allowed(seq, needs_home=False):
            return
        # Z up first, then X, then Y (the doc's safe order); we just land at the home corner.
        target = {"X": 0.0, "Y": 0.0, "Z": self.envelope["Z"][1]}
        if args[0] != "ALL":
            target = {a: (target[a] if a == args[0] else self.pos[a]) for a in AXES}
        self._begin(seq, "HOME", target, self.home_time_s)

    def _cancel_motion(self, reason: str) -> None:
        if self._motion is not None:
            self._emit(f"err {self._motion['seq']} {reason}")
            self._motion = None

    # ---- time ---------------------------------------------------------------

    def tick(self, dt_s: float) -> None:
        with self._lock:
            self._t += dt_s
            if self._vac_pending is not None:
                self._vac_pending["left"] -= dt_s
                if self._vac_pending["left"] <= 0:
                    seq = self._vac_pending["seq"]
                    self._vac_pending = None
                    if self.vacuum_failures > 0:
                        self.vacuum_failures -= 1
                        self.vac = False
                        self._emit(f"err {seq} VACUUM_TIMEOUT")
                        self._emit_state()
                    else:
                        self._emit(f"ok {seq}")
            if self._motion is not None:
                m = self._motion
                m["elapsed"] += dt_s
                f = min(m["elapsed"] / m["total"], 1.0)
                for a in AXES:
                    self.pos[a] = m["start"][a] + (m["target"][a] - m["start"][a]) * f
                if f >= 1.0:
                    self._motion = None
                    if m["kind"] == "HOME":
                        self.homed = True
                    self.fsm = "IDLE"
                    self._emit(f"done {m['seq']} X{fmt_mm(self.pos['X'])} Y{fmt_mm(self.pos['Y'])} "
                               f"Z{fmt_mm(self.pos['Z'])}")
                    self._emit_state()
                else:
                    self._emit_pos()
            self._check_closed_loop()
            if self._t >= self._next_hb:
                self._next_hb = self._t + self.heartbeat_s
                self._emit(f"hb {int(self._t * 1000)}")

    def _check_closed_loop(self) -> None:
        if self.alarm is not None or not self.homed:
            return
        for a in AXES:
            if abs(self.enc_offset[a]) > self.tol_lost:
                self._raise_alarm(f"LOST_STEPS_{a}")
                return
        if abs(self.enc_offset["A"] - self.enc_offset["Y"]) > self.tol_skew:
            self._raise_alarm("SKEW_Y")

    def _emit_pos(self) -> None:
        enc = {a: self.pos[a] + self.enc_offset[a] for a in AXES}
        enc["A"] = self.pos["Y"] + self.enc_offset["A"]
        self._emit(
            "pos " + " ".join(f"{a}{fmt_mm(self.pos[a])}" for a in AXES) + " "
            + " ".join(f"E{a}{fmt_mm(enc[a])}" for a in ("X", "Y", "Z", "A"))
        )


class LoopbackLink:
    """A line link whose far end is a SimulatedMcu. `autotick=True` runs the MCU's
    clock on a thread in real time (demo); tests pass False and call mcu.tick()."""

    def __init__(self, mcu: SimulatedMcu, autotick: bool = False, tick_hz: float = 100.0):
        self.mcu = mcu
        self.connected = True
        self._running = autotick
        self._period = 1.0 / tick_hz
        self._thread = None
        if autotick:
            self._thread = threading.Thread(target=self._loop, name="sim-mcu", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        last = time.monotonic()
        while self._running:
            time.sleep(self._period)
            now = time.monotonic()
            self.mcu.tick(now - last)
            last = now

    def write_line(self, line: str) -> None:
        if not self.connected:
            raise RuntimeError("loopback link closed")
        self.mcu.write(line)

    def read_lines(self) -> list:
        return self.mcu.read_lines()

    def close(self) -> None:
        self._running = False
        self.connected = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
