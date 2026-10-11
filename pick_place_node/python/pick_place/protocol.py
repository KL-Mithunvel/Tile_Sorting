"""The gantry's line protocol — pure encode/parse, no I/O, no clock.

Spec: documents/programming/pick_place_control_protocol.md §5 (and its closed-loop
section). One ASCII line per message, `\\n`-terminated:

    Linux -> MCU   `<seq> <VERB> [args...]`
    MCU -> Linux   `ok <seq>` | `err <seq> <REASON>` | `done <seq> X.. Y.. Z..` |
                   `pos X.. Y.. Z.. [EX.. EY.. EZ.. EA..]` | `state ..` | `alarm ..` |
                   `hb <ms>` | `log <text>`

Closed loop (missed-step detection, 2026-10-12): the MCU reads an AS5600 on every
stepper and compares it to the commanded position. `pos` may carry the measured
position per motor (`EX EY EZ EA`, mm; `EA` is the Y beam's cloned second motor)
and the MCU raises `alarm LOST_STEPS_<axis>` / `alarm SKEW_Y` itself. This module
only carries and reports that; it never decides.

`parse_line()` never raises: a line it cannot understand becomes `Unknown`, so a
garbled byte on the link cannot kill the reader thread.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Optional, Union

AXES = ("X", "Y", "Z")
ENCODER_AXES = ("X", "Y", "Z", "A")  # A = encoder on the Y beam's second (cloned) motor
HOME_TARGETS = ("ALL",) + AXES
MOTION_VERBS = ("HOME", "MOVE", "JOG")

# STOPPED: a motion that was cancelled by STOP or by an alarm (it gets this instead of `done`).
ERR_REASONS = ("BAD_CMD", "NOT_HOMED", "OUT_OF_BOUNDS", "BUSY", "VACUUM_TIMEOUT", "ALARM_ACTIVE", "STOPPED")


# ---- commands (Linux -> MCU) -------------------------------------------------


def fmt_mm(value: float) -> str:
    """Wire form of a number: up to 3 decimals, trailing zeros trimmed but one kept
    (`500.0`, `12.5`, `0.125`). Refuses nan/inf — they would be a silent bad move."""
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"not a finite number: {value!r}")
    if abs(v) < 0.0005:
        v = 0.0
    text = f"{v:.3f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


@dataclass(frozen=True)
class Command:
    """One command, without its sequence number (the sender assigns that)."""

    verb: str
    args: tuple = ()

    @property
    def is_motion(self) -> bool:
        """True for commands that get a later `done` as well as the immediate `ok`."""
        return self.verb in MOTION_VERBS

    def encode(self, seq: int) -> str:
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise ValueError(f"seq must be a non-negative int, got {seq!r}")
        return " ".join((str(seq), self.verb) + tuple(self.args))


def _positive(name: str, value: float) -> float:
    v = float(value)
    if not math.isfinite(v) or v <= 0:
        raise ValueError(f"{name} must be a positive number, got {value!r}")
    return v


def ping() -> Command:
    return Command("PING")


def home(target: str = "ALL") -> Command:
    target = target.upper()
    if target not in HOME_TARGETS:
        raise ValueError(f"HOME target must be one of {HOME_TARGETS}, got {target!r}")
    return Command("HOME", (target,))


def move(x: Optional[float] = None, y: Optional[float] = None, z: Optional[float] = None,
         f: Optional[float] = None) -> Command:
    """Absolute move. An axis left as None holds. `f` is mm/min (None = MCU default)."""
    parts = []
    for letter, value in (("X", x), ("Y", y), ("Z", z)):
        if value is not None:
            parts.append(letter + fmt_mm(value))
    if not parts:
        raise ValueError("MOVE needs at least one of x, y, z")
    if f is not None:
        parts.append("F" + fmt_mm(_positive("feed", f)))
    return Command("MOVE", tuple(parts))


def jog(axis: str, delta_mm: float, f: Optional[float] = None) -> Command:
    axis = axis.upper()
    if axis not in AXES:
        raise ValueError(f"JOG axis must be one of {AXES}, got {axis!r}")
    sign = "+" if float(delta_mm) >= 0 else ""
    parts = [axis + sign + fmt_mm(delta_mm)]
    if f is not None:
        parts.append("F" + fmt_mm(_positive("feed", f)))
    return Command("JOG", tuple(parts))


def vac(on: bool) -> Command:
    return Command("VAC", ("ON" if on else "OFF",))


def stop() -> Command:
    return Command("STOP")


def get_pos() -> Command:
    return Command("GETPOS")


def get_state() -> Command:
    return Command("GETSTATE")


def clear_alarm() -> Command:
    return Command("CLEARALARM")


def set_env(x: tuple, y: tuple, z: tuple) -> Command:
    """Push the soft-limit envelope; each argument is (min_mm, max_mm)."""
    parts = []
    for letter, (lo, hi) in (("X", x), ("Y", y), ("Z", z)):
        if float(lo) >= float(hi):
            raise ValueError(f"envelope {letter}: min {lo} must be below max {hi}")
        parts += [f"{letter}{fmt_mm(lo)}", f"{letter}{fmt_mm(hi)}"]
    # Wire order is the doc's: X<min> X<max> Y<min> Y<max> Z<min> Z<max>.
    return Command("SETENV", tuple(parts))


def set_speed(axis: str, max_mm_min: float, accel_mm_s2: float) -> Command:
    axis = axis.upper()
    if axis not in AXES:
        raise ValueError(f"SETSPEED axis must be one of {AXES}, got {axis!r}")
    return Command("SETSPEED", (axis, "MAX" + fmt_mm(_positive("max speed", max_mm_min)),
                                "ACC" + fmt_mm(_positive("accel", accel_mm_s2))))


def set_tolerance(lost_steps_mm: float, skew_mm: float) -> Command:
    """Closed-loop thresholds: encoder-vs-commanded deviation that raises
    LOST_STEPS_<axis>, and Y-vs-A deviation that raises SKEW_Y."""
    return Command("SETTOL", ("LOST" + fmt_mm(_positive("lost-steps tolerance", lost_steps_mm)),
                              "SKEW" + fmt_mm(_positive("skew tolerance", skew_mm))))


class SeqCounter:
    """Monotonic sequence numbers, starting at 1. next() is safe from any thread."""

    def __init__(self, start: int = 1):
        self._it = itertools.count(start)

    def next(self) -> int:
        return next(self._it)


# ---- events (MCU -> Linux) ---------------------------------------------------


@dataclass(frozen=True)
class Ok:
    seq: int


@dataclass(frozen=True)
class Err:
    seq: int
    reason: str


@dataclass(frozen=True)
class Done:
    seq: int
    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Pos:
    x: float
    y: float
    z: float
    enc: dict = field(default_factory=dict)  # measured mm per motor: X/Y/Z/A (may be empty)

    def deviation(self) -> dict:
        """Measured minus commanded, mm, per motor we have an encoder reading for.
        `A` (the Y beam's second motor) is judged against the commanded Y."""
        commanded = {"X": self.x, "Y": self.y, "Z": self.z, "A": self.y}
        return {axis: measured - commanded[axis] for axis, measured in self.enc.items()}


@dataclass(frozen=True)
class State:
    fsm: str
    vacuum: bool
    homed: bool


@dataclass(frozen=True)
class Alarm:
    reason: str

    @property
    def kind(self) -> str:
        """LIMIT | ESTOP | LINK_TIMEOUT | LOST_STEPS | SKEW | OTHER"""
        r = self.reason
        for kind in ("LIMIT", "LOST_STEPS", "SKEW"):
            if r == kind or r.startswith(kind + "_"):
                return kind
        return r if r in ("ESTOP", "LINK_TIMEOUT") else "OTHER"

    @property
    def axis(self) -> Optional[str]:
        """X/Y/Z for LIMIT_X_MIN, LOST_STEPS_Z, SKEW_Y; None for ESTOP etc."""
        parts = self.reason.split("_")
        if self.kind == "LIMIT" and len(parts) >= 2:
            return parts[1] if parts[1] in AXES else None
        if self.kind in ("LOST_STEPS", "SKEW"):
            last = parts[-1]
            return last if last in AXES else None
        return None

    @property
    def is_closed_loop_fault(self) -> bool:
        """The position can no longer be trusted: re-home before any further motion."""
        return self.kind in ("LOST_STEPS", "SKEW")


@dataclass(frozen=True)
class Heartbeat:
    uptime_ms: int


@dataclass(frozen=True)
class Log:
    text: str


@dataclass(frozen=True)
class Unknown:
    raw: str


Event = Union[Ok, Err, Done, Pos, State, Alarm, Heartbeat, Log, Unknown]


def _number(token: str) -> Optional[float]:
    try:
        v = float(token)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _int(token: str) -> Optional[int]:
    try:
        return int(token)
    except ValueError:
        return None


def _axis_values(tokens: list, prefix: str, axes: tuple) -> dict:
    """{'X': 1.0, ...} for tokens like `X1.0` (prefix '') or `EX1.0` (prefix 'E').
    Tokens that don't fit are ignored; a bad number is simply absent."""
    out = {}
    for token in tokens:
        if not token.startswith(prefix):
            continue
        body = token[len(prefix):]
        if len(body) < 2 or body[0] not in axes:
            continue
        value = _number(body[1:])
        if value is not None:
            out[body[0]] = value
    return out


def parse_line(line: str) -> Event:
    """Parse one MCU line into a typed event. Never raises."""
    raw = line.strip()
    tokens = raw.split()
    if not tokens:
        return Unknown(raw)
    head, rest = tokens[0].lower(), tokens[1:]

    if head == "ok" and len(rest) == 1 and (seq := _int(rest[0])) is not None:
        return Ok(seq)

    if head == "err" and len(rest) >= 2 and (seq := _int(rest[0])) is not None:
        return Err(seq, " ".join(rest[1:]))

    if head == "done" and len(rest) >= 4 and (seq := _int(rest[0])) is not None:
        xyz = _axis_values(rest[1:], "", AXES)
        if len(xyz) == 3:
            return Done(seq, xyz["X"], xyz["Y"], xyz["Z"])

    if head == "pos":
        xyz = _axis_values(rest, "", AXES)
        if len(xyz) == 3:
            return Pos(xyz["X"], xyz["Y"], xyz["Z"], _axis_values(rest, "E", ENCODER_AXES))

    if head == "state" and rest:
        flags = dict(t.split("=", 1) for t in rest[1:] if "=" in t)
        if flags.get("VAC") in ("0", "1") and flags.get("HOMED") in ("0", "1"):
            return State(rest[0], flags["VAC"] == "1", flags["HOMED"] == "1")

    if head == "alarm" and len(rest) == 1:
        return Alarm(rest[0])

    if head == "hb" and len(rest) == 1 and (ms := _int(rest[0])) is not None:
        return Heartbeat(ms)

    if head == "log":
        return Log(raw[len(tokens[0]):].strip())

    return Unknown(raw)
